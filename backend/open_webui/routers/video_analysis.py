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

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
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
MAX_FRAME_EDGE = 768  # downscale long edge to keep base64 payloads small
DEFAULT_CONCURRENCY = 3  # parallel vision requests against Ollama

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
    path: str, frame_interval: float, max_frames: int
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
        max(1, min(form_data.max_frames, 64)),
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
            'concurrency': DEFAULT_CONCURRENCY,
        },
    }


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
    return await _run_analysis(request, form_data)


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
