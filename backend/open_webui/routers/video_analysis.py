"""Offline local-video analysis powered by a local Ollama vision model.

This router lets Open WebUI read a video file from the local filesystem
(fully offline, no cloud), sample key frames, describe each frame with a
local Ollama multimodal model (e.g. ``llava``, ``qwen2.5-vl``, ``minicpm-v``),
optionally transcribe the audio track with a local faster-whisper model,
and finally fuse everything into a coherent analysis with a local model.

All inference happens against the Ollama backends already configured in
``app.state.config.OLLAMA_BASE_URLS`` and the local Whisper model — nothing
leaves the machine.

Endpoints (mounted at ``/api/v1/video``):
    GET  /health          - quick capability / dependency probe
    GET  /models          - list local Ollama models (to pick a vision model)
    POST /analyze         - analyze a local video file and return the report
    POST /analyze/stream  - same, but stream progress events over SSE
"""

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Awaitable, Callable, Optional

import aiohttp
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from open_webui.config import UPLOAD_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.routers.ollama import get_api_key
from open_webui.utils.auth import get_verified_user
from open_webui.utils.session_pool import get_session

log = logging.getLogger(__name__)

router = APIRouter()

# Allowed video containers we are willing to open locally.
SUPPORTED_VIDEO_EXTS = {
    '.mp4',
    '.mov',
    '.mkv',
    '.avi',
    '.webm',
    '.flv',
    '.m4v',
    '.mpg',
    '.mpeg',
    '.wmv',
    '.ts',
}

# A vision model can only digest a limited number of frames per request.
DEFAULT_FRAME_INTERVAL = 5.0  # seconds between sampled frames
DEFAULT_MAX_FRAMES = 16  # hard cap on frames per video
DEFAULT_MIN_FRAMES = 20  # ensure at least this many frames (adaptive to duration); 0 = off
MAX_FRAME_EDGE = 768  # downscale long edge to keep base64 payloads small
DEFAULT_CONCURRENCY = 3  # parallel vision requests against Ollama
FRAMES_HARD_CAP = 64  # absolute upper bound on frames per request (payload/VRAM guard)

# Progress callback: receives a dict event. May be None.
ProgressCb = Optional[Callable[[dict], Awaitable[None]]]


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class VideoAnalyzeForm(BaseModel):
    """Payload for POST /analyze and /analyze/stream."""

    video_path: str  # absolute path, or path relative to UPLOAD_DIR
    model: str  # local Ollama vision model id (must support images)
    summary_model: Optional[str] = None  # text model to fuse frames; defaults to ``model``
    prompt: Optional[str] = None  # custom per-frame instruction
    frame_interval: float = DEFAULT_FRAME_INTERVAL
    max_frames: int = DEFAULT_MAX_FRAMES
    min_frames: int = DEFAULT_MIN_FRAMES  # adaptive floor so short clips aren't 1-frame
    concurrency: int = DEFAULT_CONCURRENCY  # parallel vision requests
    language: str = 'zh'  # 'zh' | 'en' output language
    include_audio: bool = False  # transcribe audio track via faster-whisper
    whisper_model: Optional[str] = None  # override local whisper model id
    save_report: bool = True  # write a markdown report next to the video
    ollama_url: Optional[str] = None  # override backend url; else use config

    model_config = ConfigDict(extra='allow')


class FrameResult(BaseModel):
    index: int
    timestamp: float  # seconds into the video
    description: str


class VideoAnalyzeResponse(BaseModel):
    video_path: str
    model: str
    summary_model: str
    duration: float
    frame_count: int
    sampled_frames: int
    frames: list[FrameResult]
    transcript: Optional[str] = None
    summary: str
    report_path: Optional[str] = None
    elapsed: float
    status: str = 'done'


# --------------------------------------------------------------------------- #
# Prompt templates (offline, language-aware)
# --------------------------------------------------------------------------- #
def _frame_prompt(language: str, custom: Optional[str]) -> str:
    if custom:
        return custom
    if language == 'en':
        return (
            'You are a video understanding assistant. Describe this single video '
            'frame in detail: the scene, people, actions, on-screen text, objects '
            'and overall mood. Be concise (2-4 sentences).'
        )
    return (
        '你是视频理解助手。请详细描述这一帧画面：场景、人物、动作、画面文字、'
        '关键物体以及整体氛围。请简洁（2-4 句）。'
    )


def _summary_prompt(language: str, transcript: Optional[str]) -> str:
    if language == 'en':
        base = (
            'Below are time-ordered descriptions of frames sampled from one video. '
            'Synthesize them into a structured analysis with sections: '
            '## Overview, ## Timeline, ## Key Subjects & Actions, ## Highlights, '
            '## Tags. Resolve repetition and infer the storyline.'
        )
        if transcript:
            base += '\n\nThe audio transcript is also provided; use it to enrich the analysis.'
        return base
    base = (
        '以下是从同一个视频中按时间顺序抽取的若干帧画面描述。请将它们整合为结构化分析，'
        '包含以下小节：## 概述、## 时间线、## 主要人物与动作、## 看点、## 标签。'
        '请去除重复信息并推断出整体内容脉络。'
    )
    if transcript:
        base += '\n\n另外还提供了音频转写文本，请结合它一起分析。'
    return base


# --------------------------------------------------------------------------- #
# Filesystem helpers
# --------------------------------------------------------------------------- #
def _resolve_video_path(video_path: str) -> str:
    """Resolve to an existing, allowed video file path.

    Accepts an absolute path or a path relative to ``UPLOAD_DIR``. Guards
    against directory traversal outside UPLOAD_DIR when a relative path is given.
    """
    candidate = video_path
    if not os.path.isabs(candidate):
        candidate = os.path.normpath(os.path.join(UPLOAD_DIR, candidate))
        # prevent escaping the upload sandbox via ../ on relative inputs
        if not os.path.realpath(candidate).startswith(os.path.realpath(UPLOAD_DIR)):
            raise HTTPException(status_code=400, detail='Invalid relative video path')

    if not os.path.isfile(candidate):
        raise HTTPException(status_code=404, detail=f'Video not found: {video_path}')

    ext = os.path.splitext(candidate)[1].lower()
    if ext not in SUPPORTED_VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported video type "{ext}". Supported: {sorted(SUPPORTED_VIDEO_EXTS)}',
        )
    return candidate


# --------------------------------------------------------------------------- #
# Frame extraction (OpenCV, runs in a worker thread)
# --------------------------------------------------------------------------- #
def _extract_frames_sync(
    path: str, frame_interval: float, max_frames: int, min_frames: int = 0
) -> tuple[list[tuple[float, str]], float, int]:
    """Sample frames and return ``([(timestamp, base64_jpeg)], duration, total_frames)``.

    Pure-CPU, blocking work — call via ``asyncio.to_thread``.
    """
    import cv2  # opencv-python-headless, already in requirements.txt

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise HTTPException(status_code=400, detail='Failed to open video (codec unsupported?)')

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (total_frames / fps) if fps > 0 else 0.0

    # Decide sampling timestamps evenly across the video.
    if duration > 0:
        interval = max(frame_interval, duration / max_frames)
        timestamps = []
        t = 0.0
        while t < duration and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval
    else:
        # Fallback: sample by frame index when duration is unknown.
        step = max(1, (total_frames or max_frames) // max_frames)
        timestamps = [i for i in range(0, total_frames or max_frames, step)][:max_frames]
        timestamps = [float(i) for i in timestamps]

    # Guarantee a minimum number of frames for short videos so the model gets
    # enough context (e.g. a 10s clip shouldn't be summarized from a single frame).
    if min_frames and duration > 0 and len(timestamps) < min_frames:
        target = min(int(min_frames), FRAMES_HARD_CAP)
        if total_frames > 0:
            target = min(target, total_frames)
        if target > len(timestamps):
            even_step = duration / target
            timestamps = [round(i * even_step, 2) for i in range(target)]

    results: list[tuple[float, str]] = []
    for ts in timestamps:
        if duration > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        else:
            cap.set(cv2.CAP_PROP_POS_FRAMES, ts)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue

        # Downscale to keep base64 payload (and VRAM) reasonable.
        h, w = frame.shape[:2]
        long_edge = max(h, w)
        if long_edge > MAX_FRAME_EDGE:
            scale = MAX_FRAME_EDGE / long_edge
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            continue
        b64 = base64.b64encode(buf.tobytes()).decode('utf-8')
        results.append((round(ts, 2), b64))

    cap.release()
    return results, duration, total_frames


# --------------------------------------------------------------------------- #
# Audio transcription (faster-whisper, fully local)
# --------------------------------------------------------------------------- #
async def _transcribe_audio(request: Request, path: str, whisper_model: Optional[str]) -> str:
    """Transcribe the video's audio track with a local faster-whisper model.

    faster-whisper decodes audio directly from the media file (via PyAV/ffmpeg),
    so we can pass the video path as-is. Reuses the shared model on app.state.
    """
    # Lazy import to avoid coupling at module import time.
    from open_webui.routers.audio import set_faster_whisper_model

    model_id = whisper_model or getattr(request.app.state.config, 'WHISPER_MODEL', None) or 'base'

    model = getattr(request.app.state, 'faster_whisper_model', None)
    if model is None or whisper_model:
        model = await asyncio.to_thread(set_faster_whisper_model, model_id, True)
        # cache only the config-default model on app.state
        if not whisper_model:
            request.app.state.faster_whisper_model = model

    if model is None:
        return ''

    def _run() -> str:
        segments, _info = model.transcribe(path, beam_size=5)
        return ''.join(seg.text for seg in segments).strip()

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:  # audio track may be absent / undecodable
        log.warning(f'Audio transcription failed: {exc}')
        return ''


# --------------------------------------------------------------------------- #
# Ollama interaction (non-streaming, offline)
# --------------------------------------------------------------------------- #
def _pick_ollama_url(request: Request, override: Optional[str]) -> tuple[str, int]:
    if override:
        return override.rstrip('/'), 0
    base_urls = request.app.state.config.OLLAMA_BASE_URLS
    if not base_urls:
        raise HTTPException(status_code=503, detail='No Ollama backend configured')
    return base_urls[0].rstrip('/'), 0


async def _ollama_chat(
    request: Request,
    url: str,
    url_idx: int,
    model: str,
    content: str,
    images: Optional[list[str]] = None,
) -> str:
    """Call Ollama /api/chat once (non-streaming) and return the message text."""
    message: dict = {'role': 'user', 'content': content}
    if images:
        message['images'] = images

    payload = {
        'model': model,
        'messages': [message],
        'stream': False,
        'options': {'temperature': 0.2},
    }

    session = await get_session()
    headers = {'Content-Type': 'application/json'}
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)
    if key:
        headers['Authorization'] = f'Bearer {key}'

    try:
        async with session.post(
            f'{url}/api/chat',
            data=json.dumps(payload),
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=aiohttp.ClientTimeout(total=600),
        ) as r:
            if r.status != 200:
                detail = await r.text()
                raise HTTPException(status_code=r.status, detail=f'Ollama: {detail[:300]}')
            data = await r.json()
    except HTTPException:
        raise
    except Exception as exc:
        log.exception(exc)
        raise HTTPException(status_code=500, detail=f'Ollama request failed: {exc}')

    return (data.get('message') or {}).get('content', '') or ''


# --------------------------------------------------------------------------- #
# Core analysis pipeline (shared by /analyze and /analyze/stream)
# --------------------------------------------------------------------------- #
async def _run_analysis(
    request: Request,
    form_data: VideoAnalyzeForm,
    on_progress: ProgressCb = None,
) -> VideoAnalyzeResponse:
    async def emit(event: dict) -> None:
        if on_progress:
            await on_progress(event)

    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(status_code=503, detail=ERROR_MESSAGES.OLLAMA_API_DISABLED)

    started = time.time()
    path = _resolve_video_path(form_data.video_path)
    summary_model = form_data.summary_model or form_data.model
    url, url_idx = _pick_ollama_url(request, form_data.ollama_url)

    # 1) Extract frames off the event loop.
    await emit({'stage': 'extract', 'message': 'Extracting frames...'})
    frames, duration, total_frames = await asyncio.to_thread(
        _extract_frames_sync,
        path,
        max(0.5, form_data.frame_interval),
        max(1, min(form_data.max_frames, FRAMES_HARD_CAP)),
        max(0, min(form_data.min_frames, FRAMES_HARD_CAP)),
    )
    if not frames:
        raise HTTPException(status_code=422, detail='No frames could be decoded from the video')
    await emit({'stage': 'extracted', 'sampled_frames': len(frames), 'duration': round(duration, 2)})

    # 2) (optional) Transcribe audio in parallel with frame description below.
    transcript_task: Optional[asyncio.Task] = None
    if form_data.include_audio:
        await emit({'stage': 'audio', 'message': 'Transcribing audio (faster-whisper)...'})
        transcript_task = asyncio.create_task(
            _transcribe_audio(request, path, form_data.whisper_model)
        )

    # 3) Describe each frame with the local vision model — concurrently.
    frame_prompt = _frame_prompt(form_data.language, form_data.prompt)
    concurrency = max(1, min(form_data.concurrency, 8))
    semaphore = asyncio.Semaphore(concurrency)
    done_counter = {'n': 0}
    total = len(frames)

    async def describe(idx: int, ts: float, b64: str) -> FrameResult:
        async with semaphore:
            desc = await _ollama_chat(
                request, url, url_idx, form_data.model, content=frame_prompt, images=[b64]
            )
            done_counter['n'] += 1
            await emit(
                {
                    'stage': 'frame',
                    'index': idx,
                    'timestamp': ts,
                    'done': done_counter['n'],
                    'total': total,
                    'description': desc.strip(),
                }
            )
            return FrameResult(index=idx, timestamp=ts, description=desc.strip())

    frame_results: list[FrameResult] = await asyncio.gather(
        *[describe(i, ts, b64) for i, (ts, b64) in enumerate(frames)]
    )
    frame_results.sort(key=lambda fr: fr.index)

    transcript = ''
    if transcript_task is not None:
        transcript = await transcript_task
        await emit({'stage': 'audio_done', 'chars': len(transcript)})

    # 4) Fuse per-frame observations (+ transcript) into a structured report.
    await emit({'stage': 'summarize', 'message': 'Fusing into final report...'})
    joined = '\n'.join(f'- [{fr.timestamp:.1f}s] {fr.description}' for fr in frame_results)
    fuse_input = f'{_summary_prompt(form_data.language, transcript)}\n\n## 画面帧描述 / Frames\n{joined}'
    if transcript:
        fuse_input += f'\n\n## 音频转写 / Transcript\n{transcript}'
    summary = (await _ollama_chat(request, url, url_idx, summary_model, content=fuse_input)).strip()

    elapsed = round(time.time() - started, 2)

    # 5) Optionally persist a markdown report next to the source video.
    report_path = None
    if form_data.save_report:
        report_path = await asyncio.to_thread(
            _write_report,
            path,
            form_data,
            summary_model,
            duration,
            frame_results,
            transcript,
            summary,
            elapsed,
        )

    result = VideoAnalyzeResponse(
        video_path=path,
        model=form_data.model,
        summary_model=summary_model,
        duration=round(duration, 2),
        frame_count=total_frames,
        sampled_frames=len(frame_results),
        frames=frame_results,
        transcript=transcript or None,
        summary=summary,
        report_path=report_path,
        elapsed=elapsed,
    )
    await emit({'stage': 'done', 'report_path': report_path, 'elapsed': elapsed})
    return result


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get('/health')
async def health(user=Depends(get_verified_user)) -> dict:
    """Probe that OpenCV is importable and report video-analysis capability."""
    try:
        import cv2  # noqa: F401

        cv2_ok = True
    except Exception as exc:  # pragma: no cover
        cv2_ok = False
        log.warning(f'OpenCV not available: {exc}')

    return {
        'status': cv2_ok,
        'opencv': cv2_ok,
        'supported_exts': sorted(SUPPORTED_VIDEO_EXTS),
        'defaults': {
            'frame_interval': DEFAULT_FRAME_INTERVAL,
            'max_frames': DEFAULT_MAX_FRAMES,
            'min_frames': DEFAULT_MIN_FRAMES,
            'concurrency': DEFAULT_CONCURRENCY,
        },
    }


@router.post('/upload-and-analyze')
async def upload_and_analyze(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    summary_model: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    frame_interval: float = Form(DEFAULT_FRAME_INTERVAL),
    max_frames: int = Form(DEFAULT_MAX_FRAMES),
    min_frames: int = Form(DEFAULT_MIN_FRAMES),
    concurrency: int = Form(DEFAULT_CONCURRENCY),
    language: str = Form('zh'),
    include_audio: bool = Form(False),
    whisper_model: Optional[str] = Form(None),
    save_report: bool = Form(True),
    ollama_url: Optional[str] = Form(None),
    user=Depends(get_verified_user),
) -> VideoAnalyzeResponse:
    """Upload a video file and analyze it.
    
    This endpoint accepts a video file upload, saves it to UPLOAD_DIR,
    and then analyzes it using the video analysis pipeline.
    
    Returns the same response as /analyze.
    """
    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported video type "{ext}". Supported: {sorted(SUPPORTED_VIDEO_EXTS)}'
        )
    
    # Save uploaded file
    upload_dir = UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    
    # Generate unique filename to avoid collisions
    file_id = str(uuid.uuid4())
    safe_filename = f'{file_id}{ext}'
    file_path = os.path.join(upload_dir, safe_filename)
    
    try:
        with open(file_path, 'wb') as f:
            content = await file.read()
            f.write(content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Failed to save uploaded file: {exc}')
    
    # Create form data for analysis
    form_data = VideoAnalyzeForm(
        video_path=file_path,
        model=model,
        summary_model=summary_model,
        prompt=prompt,
        frame_interval=frame_interval,
        max_frames=max_frames,
        min_frames=min_frames,
        concurrency=concurrency,
        language=language,
        include_audio=include_audio,
        whisper_model=whisper_model,
        save_report=save_report,
        ollama_url=ollama_url,
    )
    
    # Run analysis
    try:
        result = await _run_analysis(request, form_data)
        return result
    except Exception as exc:
        # Clean up uploaded file on error
        if os.path.isfile(file_path):
            os.remove(file_path)
        raise


@router.post('/upload-and-analyze/stream')
async def upload_and_analyze_stream(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    summary_model: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    frame_interval: float = Form(DEFAULT_FRAME_INTERVAL),
    max_frames: int = Form(DEFAULT_MAX_FRAMES),
    min_frames: int = Form(DEFAULT_MIN_FRAMES),
    concurrency: int = Form(DEFAULT_CONCURRENCY),
    language: str = Form('zh'),
    include_audio: bool = Form(False),
    whisper_model: Optional[str] = Form(None),
    save_report: bool = Form(True),
    ollama_url: Optional[str] = Form(None),
    user=Depends(get_verified_user),
):
    """Upload a video file and analyze it with streaming progress updates (SSE)."""
    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported video type "{ext}". Supported: {sorted(SUPPORTED_VIDEO_EXTS)}'
        )
    
    # Save uploaded file
    upload_dir = UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    
    file_id = str(uuid.uuid4())
    safe_filename = f'{file_id}{ext}'
    file_path = os.path.join(upload_dir, safe_filename)
    
    try:
        with open(file_path, 'wb') as f:
            content = await file.read()
            f.write(content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Failed to save uploaded file: {exc}')
    
    # Create form data for analysis
    form_data = VideoAnalyzeForm(
        video_path=file_path,
        model=model,
        summary_model=summary_model,
        prompt=prompt,
        frame_interval=frame_interval,
        max_frames=max_frames,
        min_frames=min_frames,
        concurrency=concurrency,
        language=language,
        include_audio=include_audio,
        whisper_model=whisper_model,
        save_report=save_report,
        ollama_url=ollama_url,
    )
    
    # Stream progress events
    async def progress_generator():
        queue = asyncio.Queue()
        
        async def on_progress(event: dict):
            await queue.put(event)
        
        # Run analysis in background
        async def run_analysis():
            try:
                result = await _run_analysis(request, form_data, on_progress=on_progress)
                await queue.put({'stage': 'done', 'result': result.model_dump()})
            except Exception as exc:
                await queue.put({'stage': 'error', 'detail': str(exc)})
            finally:
                await queue.put(None)  # Signal end
        
        task = asyncio.create_task(run_analysis())
        
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f'data: {json.dumps(event, ensure_ascii=False)}\n\n'
        
        await task
    
    return StreamingResponse(
        progress_generator(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.get('/models')
async def list_models(request: Request, user=Depends(get_verified_user)) -> dict:
    """List local Ollama models so the UI can pick a vision-capable one."""
    url, url_idx = _pick_ollama_url(request, None)
    session = await get_session()
    headers = {'Content-Type': 'application/json'}
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)
    if key:
        headers['Authorization'] = f'Bearer {key}'
    try:
        async with session.get(
            f'{url}/api/tags',
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            data = await r.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Failed to list models: {exc}')
    return {'models': [m.get('model') for m in (data or {}).get('models', [])]}


@router.post('/analyze', response_model=VideoAnalyzeResponse)
async def analyze_video(
    request: Request,
    form_data: VideoAnalyzeForm,
    user=Depends(get_verified_user),
) -> VideoAnalyzeResponse:
    """Read a local video and produce an offline LLM analysis report."""
    result = await _run_analysis(request, form_data)
    # Save to history if analysis completed successfully
    if result.status == 'done':
        _append_history_record(form_data, result)
    return result


@router.post('/analyze/stream')
async def analyze_video_stream(
    request: Request,
    form_data: VideoAnalyzeForm,
    user=Depends(get_verified_user),
):
    """Same as /analyze, but stream progress events over Server-Sent Events."""
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()

    async def on_progress(event: dict) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            result = await _run_analysis(request, form_data, on_progress=on_progress)
            # Save to history if analysis completed successfully
            if result.status == 'done':
                _append_history_record(form_data, result)
            await queue.put({'stage': 'result', 'result': result.model_dump()})
        except HTTPException as exc:
            await queue.put({'stage': 'error', 'detail': exc.detail, 'status': exc.status_code})
        except Exception as exc:  # pragma: no cover
            log.exception(exc)
            await queue.put({'stage': 'error', 'detail': str(exc)})
        finally:
            await queue.put(_SENTINEL)

    async def event_stream():
        task = asyncio.create_task(runner())
        try:
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    break
                yield f'data: {json.dumps(event, ensure_ascii=False)}\n\n'
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _write_report(
    video_path: str,
    form_data: VideoAnalyzeForm,
    summary_model: str,
    duration: float,
    frame_results: list[FrameResult],
    transcript: str,
    summary: str,
    elapsed: float,
) -> str:
    base, _ = os.path.splitext(video_path)
    report_path = f'{base}.analysis.md'
    lines = [
        '# 视频离线分析报告 / Video Analysis Report',
        '',
        f'- 源文件 / Source: `{os.path.basename(video_path)}`',
        f'- 生成时间 / Generated: {datetime.now().isoformat(timespec="seconds")}',
        f'- 视觉模型 / Vision model: `{form_data.model}`',
        f'- 汇总模型 / Summary model: `{summary_model}`',
        f'- 时长 / Duration: {duration:.2f}s，采样帧 / Frames: {len(frame_results)}',
        f'- 耗时 / Elapsed: {elapsed:.2f}s',
        '',
        '---',
        '',
        summary,
        '',
    ]
    if transcript:
        lines += ['---', '', '## 音频转写 / Transcript', '', transcript, '']
    lines += ['---', '', '## 逐帧描述 / Per-frame descriptions', '']
    for fr in frame_results:
        lines.append(f'### 帧 {fr.index} @ {fr.timestamp:.1f}s')
        lines.append('')
        lines.append(fr.description)
        lines.append('')

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return report_path


# --------------------------------------------------------------------------- #
# Batch / directory analysis
# ---------------------------------------------------------------------------
# Drop a directory in (from the web UI), the backend scans it for supported
# videos and analyzes them one-by-one in a background task. The UI polls
# ``GET /batch/{job_id}`` to render per-video status, overall progress and the
# per-video report — no login-less script needed, fully offline.
# --------------------------------------------------------------------------- #
# In-memory job registry. Survives as long as the backend process lives.
_BATCH_JOBS: "dict[str, dict]" = {}
_BATCH_JOBS_MAX = 20  # keep only the most recent N jobs to bound memory


class BatchAnalyzeForm(BaseModel):
    """Payload for POST /batch — analyze every supported video in a directory."""

    directory: str  # absolute directory path on the server
    model: str  # local Ollama vision model id (must support images)
    summary_model: Optional[str] = None
    prompt: Optional[str] = None
    frame_interval: float = DEFAULT_FRAME_INTERVAL
    max_frames: int = DEFAULT_MAX_FRAMES
    min_frames: int = DEFAULT_MIN_FRAMES
    concurrency: int = DEFAULT_CONCURRENCY
    language: str = 'zh'
    include_audio: bool = False
    whisper_model: Optional[str] = None
    save_report: bool = True
    ollama_url: Optional[str] = None
    recursive: bool = False  # walk sub-directories too
    skip_existing: bool = True  # skip videos that already have <name>.analysis.md

    model_config = ConfigDict(extra='allow')


def _scan_videos(directory: str, recursive: bool) -> list[str]:
    """Return a sorted list of supported video files under ``directory``."""
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=404, detail=f'Directory not found: {directory}')

    found: list[str] = []
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in SUPPORTED_VIDEO_EXTS:
                    found.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(directory):
            p = os.path.join(directory, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in SUPPORTED_VIDEO_EXTS:
                found.append(p)
    return sorted(found)


def _public_job(job: dict) -> dict:
    """Serialize a job for the API (kept JSON-friendly)."""
    return {
        'id': job['id'],
        'directory': job['directory'],
        'status': job['status'],
        'created': job['created'],
        'finished': job.get('finished'),
        'total': job['total'],
        'completed': job['completed'],
        'current': job.get('current'),
        'items': job['items'],
        'params': job.get('params', {}),
    }


def _prune_jobs() -> None:
    if len(_BATCH_JOBS) <= _BATCH_JOBS_MAX:
        return
    # drop the oldest finished jobs first
    finished = sorted(
        (j for j in _BATCH_JOBS.values() if j['status'] in ('done', 'error', 'cancelled')),
        key=lambda j: j.get('finished') or j['created'],
    )
    for j in finished:
        if len(_BATCH_JOBS) <= _BATCH_JOBS_MAX:
            break
        _BATCH_JOBS.pop(j['id'], None)


async def _run_batch(request: Request, job_id: str) -> None:
    """Background worker: analyze each pending video in the job sequentially."""
    job = _BATCH_JOBS.get(job_id)
    if not job:
        return

    for item in job['items']:
        # Check for cancel
        if job.get('cancel'):
            break
        
        # Check for pause
        while job.get('paused') and not job.get('cancel'):
            await asyncio.sleep(1)
        
        if item['status'] != 'pending':
            continue

        item['status'] = 'running'
        item['started'] = time.time()
        job['current'] = {'name': item['name'], 'path': item['path'], 'done': 0, 'total': 0}

        form = VideoAnalyzeForm(
            video_path=item['path'],
            model=job['params']['model'],
            summary_model=job['params'].get('summary_model'),
            prompt=job['params'].get('prompt'),
            frame_interval=job['params']['frame_interval'],
            max_frames=job['params']['max_frames'],
            min_frames=job['params'].get('min_frames', DEFAULT_MIN_FRAMES),
            concurrency=job['params']['concurrency'],
            language=job['params']['language'],
            include_audio=job['params']['include_audio'],
            whisper_model=job['params'].get('whisper_model'),
            save_report=job['params']['save_report'],
            ollama_url=job['params'].get('ollama_url'),
        )

        async def on_progress(event: dict) -> None:
            stage = event.get('stage')
            cur = job.get('current') or {}
            if stage == 'extracted':
                cur['total'] = event.get('sampled_frames', 0)
            elif stage == 'frame':
                cur['done'] = event.get('done', cur.get('done', 0))
                cur['total'] = event.get('total', cur.get('total', 0))
            job['current'] = cur

        try:
            result = await _run_analysis(request, form, on_progress=on_progress)
            item['status'] = 'done'
            item['report_path'] = result.report_path
            item['summary'] = result.summary
            item['elapsed'] = result.elapsed
            item['sampled_frames'] = result.sampled_frames
            # Save to history if analysis completed successfully
            if result.status == 'done':
                _append_history_record(form, result)
        except HTTPException as exc:
            item['status'] = 'error'
            item['error'] = str(exc.detail)
        except Exception as exc:  # pragma: no cover
            log.exception(exc)
            item['status'] = 'error'
            item['error'] = str(exc)
        finally:
            item['finished'] = time.time()
            job['completed'] += 1

    # mark any still-pending items as cancelled
    if job.get('cancel'):
        for item in job['items']:
            if item['status'] == 'pending':
                item['status'] = 'cancelled'
        job['status'] = 'cancelled'
    else:
        job['status'] = 'done'
    job['current'] = None
    job['finished'] = time.time()


@router.post('/batch/scan')
async def scan_directory(
    request: Request,
    form_data: BatchAnalyzeForm,
    user=Depends(get_verified_user),
) -> dict:
    """Preview which videos a directory contains (no analysis yet)."""
    videos = _scan_videos(form_data.directory, form_data.recursive)
    items = []
    for p in videos:
        base, _ = os.path.splitext(p)
        analyzed = os.path.exists(f'{base}.analysis.md')
        items.append({'path': p, 'name': os.path.basename(p), 'analyzed': analyzed})
    return {
        'directory': os.path.expanduser(form_data.directory),
        'count': len(items),
        'analyzed': sum(1 for it in items if it['analyzed']),
        'videos': items,
    }


@router.post('/batch')
async def start_batch(
    request: Request,
    form_data: BatchAnalyzeForm,
    user=Depends(get_verified_user),
) -> dict:
    """Scan a directory and start analyzing every supported video in background."""
    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(status_code=503, detail=ERROR_MESSAGES.OLLAMA_API_DISABLED)

    videos = _scan_videos(form_data.directory, form_data.recursive)
    if not videos:
        raise HTTPException(
            status_code=404, detail='No supported video files found in the directory'
        )

    items = []
    for p in videos:
        base, _ = os.path.splitext(p)
        existing = f'{base}.analysis.md'
        analyzed = os.path.exists(existing)
        skipped = bool(form_data.skip_existing and analyzed)
        items.append(
            {
                'path': p,
                'name': os.path.basename(p),
                'status': 'skipped' if skipped else 'pending',
                'report_path': existing if analyzed else None,
                'summary': None,
                'sampled_frames': None,
                'elapsed': None,
                'error': None,
                'started': None,
                'finished': None,
            }
        )

    job_id = uuid.uuid4().hex[:12]
    job = {
        'id': job_id,
        'directory': os.path.expanduser(form_data.directory),
        'status': 'running',
        'created': time.time(),
        'finished': None,
        'cancel': False,
        'paused': False,
        'total': len(items),
        'completed': sum(1 for it in items if it['status'] == 'skipped'),
        'current': None,
        'items': items,
        'params': {
            'model': form_data.model,
            'summary_model': form_data.summary_model,
            'prompt': form_data.prompt,
            'frame_interval': form_data.frame_interval,
            'max_frames': form_data.max_frames,
            'min_frames': form_data.min_frames,
            'concurrency': form_data.concurrency,
            'language': form_data.language,
            'include_audio': form_data.include_audio,
            'whisper_model': form_data.whisper_model,
            'save_report': form_data.save_report,
            'ollama_url': form_data.ollama_url,
            'recursive': form_data.recursive,
            'skip_existing': form_data.skip_existing,
        },
    }
    _BATCH_JOBS[job_id] = job
    _prune_jobs()

    asyncio.create_task(_run_batch(request, job_id))

    return {
        'job_id': job_id,
        'total': job['total'],
        'pending': sum(1 for it in items if it['status'] == 'pending'),
        'skipped': job['completed'],
    }


@router.get('/batch')
async def list_batches(user=Depends(get_verified_user)) -> dict:
    """List recent batch jobs (compact, without per-video summaries)."""
    jobs = []
    for job in sorted(_BATCH_JOBS.values(), key=lambda j: j['created'], reverse=True):
        jobs.append(
            {
                'id': job['id'],
                'directory': job['directory'],
                'status': job['status'],
                'created': job['created'],
                'finished': job.get('finished'),
                'total': job['total'],
                'completed': job['completed'],
            }
        )
    return {'jobs': jobs}


@router.get('/batch/{job_id}')
async def get_batch(job_id: str, user=Depends(get_verified_user)) -> dict:
    """Full state of one batch job — the UI polls this to render progress."""
    job = _BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Batch job not found')
    return _public_job(job)


@router.post('/batch/{job_id}/cancel')
async def cancel_batch(job_id: str, user=Depends(get_verified_user)) -> dict:
    """Request cancellation; the current video finishes, the rest are skipped."""
    job = _BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Batch job not found')
    if job['status'] == 'running':
        job['cancel'] = True
    return {'id': job_id, 'status': job['status'], 'cancel': job['cancel']}


@router.post('/batch/{job_id}/pause')
async def pause_batch(job_id: str, user=Depends(get_verified_user)) -> dict:
    """Pause a running batch job."""
    job = _BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Batch job not found')
    if job['status'] == 'running':
        job['paused'] = True
        job['status'] = 'paused'
    return {'id': job_id, 'status': job['status'], 'paused': job['paused']}


@router.post('/batch/{job_id}/resume')
async def resume_batch(job_id: str, user=Depends(get_verified_user)) -> dict:
    """Resume a paused batch job."""
    job = _BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Batch job not found')
    if job['status'] == 'paused':
        job['paused'] = False
        job['status'] = 'running'
    return {'id': job_id, 'status': job['status'], 'paused': job['paused']}


@router.post('/batch/{job_id}/retry')
async def retry_batch(job_id: str, request: Request, user=Depends(get_verified_user)) -> dict:
    """Retry failed items in a batch job."""
    job = _BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Batch job not found')
    
    # Get optional video path from request body
    try:
        body = await request.json()
        video_path = body.get('video_path')
    except Exception:
        video_path = None
    
    retried = 0
    for item in job['items']:
        if video_path:
            # Retry specific video
            if item['path'] == video_path and item['status'] == 'error':
                item['status'] = 'pending'
                item['error'] = None
                retried += 1
        else:
            # Retry all failed videos
            if item['status'] == 'error':
                item['status'] = 'pending'
                item['error'] = None
                retried += 1
    
    if retried > 0 and job['status'] in ('done', 'error', 'cancelled'):
        # Restart the job if it was finished
        job['status'] = 'running'
        job['cancel'] = False
        job['finished'] = None
        asyncio.create_task(_run_batch(request, job_id))
    
    return {'id': job_id, 'status': job['status'], 'retried': retried}


# --------------------------------------------------------------------------- #
# Upload-and-analyze: turn the server into a "video analysis agent".
# ---------------------------------------------------------------------------
# A colleague on another machine can POST their *local* video file and get a
# report back — no server-filesystem access needed. The file is streamed to
# UPLOAD_DIR/video_analysis, analyzed by the same offline pipeline, and the
# structured report is returned in the response.
#
#   curl -X POST http://<host>:9102/api/v1/video/analyze/upload \
#        -H "Authorization: Bearer sk-xxxx" \
#        -F "file=@/local/path/clip.mp4" \
#        -F "language=zh"
# --------------------------------------------------------------------------- #
async def _default_vision_model(request: Request) -> str:
    """Pick a sensible local vision model when the caller doesn't specify one."""
    try:
        url, url_idx = _pick_ollama_url(request, None)
        session = await get_session()
        headers = {'Content-Type': 'application/json'}
        key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)
        if key:
            headers['Authorization'] = f'Bearer {key}'
        async with session.get(
            f'{url}/api/tags',
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            data = await r.json()
        names = [m.get('model') for m in (data or {}).get('models', []) if m.get('model')]
    except Exception:
        names = []
    # Prefer vision models known to load reliably on Ollama; keep mllama-based
    # ones (e.g. llama3.2-vision) last since some Ollama builds fail to load them
    # ("unknown model architecture: 'mllama'").
    import re

    preferred = [
        r'minicpm-?v',
        r'qwen.*vl',
        r'llava|bakllava',
        r'moondream',
        r'cogvlm',
        r'vl\b|vision',  # generic fallback (covers llama*-vision last)
    ]
    for pat in preferred:
        for n in names:
            if re.search(pat, n, re.I):
                return n
    if names:
        return names[0]
    raise HTTPException(status_code=503, detail='No local Ollama vision model available')


@router.post('/analyze/upload', response_model=VideoAnalyzeResponse)
async def analyze_uploaded_video(
    request: Request,
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    summary_model: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    language: str = Form('zh'),
    frame_interval: float = Form(DEFAULT_FRAME_INTERVAL),
    max_frames: int = Form(DEFAULT_MAX_FRAMES),
    min_frames: int = Form(DEFAULT_MIN_FRAMES),
    concurrency: int = Form(DEFAULT_CONCURRENCY),
    include_audio: bool = Form(False),
    whisper_model: Optional[str] = Form(None),
    save_report: bool = Form(True),
    keep_file: bool = Form(True),  # keep the uploaded file (and its report) on the server
    user=Depends(get_verified_user),
) -> VideoAnalyzeResponse:
    """Accept an uploaded video, analyze it offline, and return the report."""
    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(status_code=503, detail=ERROR_MESSAGES.OLLAMA_API_DISABLED)

    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in SUPPORTED_VIDEO_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported video type "{ext}". Supported: {sorted(SUPPORTED_VIDEO_EXTS)}',
        )

    chosen_model = model or await _default_vision_model(request)

    # Stream the upload to disk (avoid loading the whole file in memory).
    dest_dir = os.path.join(UPLOAD_DIR, 'video_analysis')
    os.makedirs(dest_dir, exist_ok=True)
    safe_base = os.path.basename(file.filename or f'video{ext}')
    dest = os.path.join(dest_dir, f'{uuid.uuid4().hex[:8]}_{safe_base}')

    import shutil

    try:
        with open(dest, 'wb') as out:
            await asyncio.to_thread(shutil.copyfileobj, file.file, out, 1024 * 1024)
    finally:
        await file.close()

    form = VideoAnalyzeForm(
        video_path=dest,
        model=chosen_model,
        summary_model=summary_model,
        prompt=prompt,
        frame_interval=frame_interval,
        max_frames=max_frames,
        min_frames=min_frames,
        concurrency=concurrency,
        language=language,
        include_audio=include_audio,
        whisper_model=whisper_model,
        save_report=save_report,
    )
    try:
        result = await _run_analysis(request, form)
    finally:
        if not keep_file:
            try:
                os.remove(dest)
            except OSError:
                pass
    return result


# --------------------------------------------------------------------------- #
# History: persistent, cross-session record of finished analyses.
# --------------------------------------------------------------------------- #
# Analyses that finish with status=="done" are appended to a per-install
# JSONL file (`video_analysis_history.jsonl` next to `open_webui.db`).
# The file is append-only; each line is a complete JSON object so the file
# can be tailed / rotated without parsing the whole thing.
# --------------------------------------------------------------------------- #
_HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'video_analysis_history.jsonl')


def _append_history(record: dict) -> None:
    """Append one analysis record to the history file (JSONL)."""
    try:
        os.makedirs(os.path.dirname(_HISTORY_PATH), exist_ok=True)
        with open(_HISTORY_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        log.warning('Failed to append video analysis history', exc_info=True)


def _append_history_record(form_data: VideoAnalyzeForm, result: VideoAnalyzeResponse) -> None:
    """Build and append a history record from form data and analysis result."""
    try:
        record = {
            'video_path': form_data.video_path,
            'video_name': os.path.basename(form_data.video_path),
            'model': result.model,
            'summary_model': result.summary_model,
            'duration': result.duration,
            'sampled_frames': result.sampled_frames,
            'has_audio': bool(result.transcript),
            'language': form_data.language,
            'analyzed_at': datetime.utcnow().isoformat(),
            'summary': result.summary,
            'transcript': result.transcript,
            'frames': [fr.model_dump() for fr in result.frames] if result.frames else [],
            'report_path': result.report_path,
            'elapsed': result.elapsed,
            'file_size': os.path.getsize(form_data.video_path) if os.path.exists(form_data.video_path) else None,
        }
        _append_history(record)
    except Exception:
        log.warning('Failed to build history record', exc_info=True)


def _load_history() -> list[dict]:
    """Load all history records (newest first)."""
    records = []
    if not os.path.exists(_HISTORY_PATH):
        return records
    try:
        with open(_HISTORY_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        log.warning('Failed to load video analysis history', exc_info=True)
    # Newest first
    records.reverse()
    return records


def _delete_history(video_path: str) -> bool:
    """Delete all history records for *video_path* (path is the unique key)."""
    if not os.path.exists(_HISTORY_PATH):
        return False
    try:
        with open(_HISTORY_PATH, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
        kept = []
        deleted = False
        for line in lines:
            try:
                rec = json.loads(line)
                if rec.get('video_path') != video_path:
                    kept.append(line)
                else:
                    deleted = True
            except json.JSONDecodeError:
                kept.append(line)
        if deleted:
            with open(_HISTORY_PATH, 'w', encoding='utf-8') as f:
                for line in kept:
                    f.write(line + '\n')
        return deleted
    except Exception:
        log.warning('Failed to delete video analysis history', exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# History API endpoints
# --------------------------------------------------------------------------- #
class VideoHistoryItem(BaseModel):
    video_path: str
    video_name: str
    model: Optional[str] = None
    summary_model: Optional[str] = None
    duration: Optional[float] = None
    sampled_frames: Optional[int] = None
    has_audio: bool = False
    language: str = 'zh'
    analyzed_at: str
    summary: Optional[str] = None
    transcript: Optional[str] = None
    frames: list = []
    report_path: Optional[str] = None
    elapsed: Optional[float] = None
    file_size: Optional[int] = None


@router.get('/history', response_model=list[VideoHistoryItem])
async def get_video_history(user=Depends(get_verified_user)) -> list[dict]:
    """Return persisted video analysis history (newest first)."""
    return _load_history()


@router.delete('/history')
async def delete_video_history(request: Request, user=Depends(get_verified_user)) -> dict:
    """Delete history records for the given video path."""
    body = await request.json()
    video_path = body.get('video_path')
    if not video_path:
        raise HTTPException(status_code=400, detail='video_path is required')
    deleted = _delete_history(video_path)
    return {'deleted': deleted}

