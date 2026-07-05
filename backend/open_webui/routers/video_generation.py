"""Local AI video generation powered by a local Ollama LLM + FFmpeg.

This router adds the *generation* counterpart to the existing offline video
*analysis* router (``routers/video_analysis.py``). Given a text prompt it:

  1. enhances the prompt with a local Ollama LLM,
  2. plans a structured director "shot list" (palette / motion / mood / overlay),
  3. renders a short, real, playable video with FFmpeg (a gradient + Ken-Burns
     style animation driven by the LLM plan; degrades gracefully without a GPU).

Everything runs locally (Ollama) — nothing leaves the machine. This mirrors the
capability of the standalone ``video_large_model_project`` but lives inside the
Open WebUI backend so it is available from the workspace UI.

Endpoints (mounted at ``/api/v1/video-gen``):
    GET  /health            - capability probe (ffmpeg / ffprobe available)
    GET  /models            - list local Ollama models (to pick an LLM)
    POST /enhance           - expand a short prompt into a pro video prompt
    POST /suggest           - propose creative prompt variations
    POST /generate          - generate a video from a prompt (returns a playable URL)
    GET  /list              - list previously generated videos
    GET  /file/{name}       - serve a generated video (Range-aware, for ``<video>``)
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from open_webui.config import DATA_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.routers.ollama import get_api_key
from open_webui.utils.auth import get_verified_user
from open_webui.utils.session_pool import get_session

import logging  # noqa: E402

log = logging.getLogger("open_webui.routers.video_generation")

router = APIRouter()

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Generated videos live in their own sub-directory under DATA_DIR so they never
# collide with uploaded files.
PREVIEW_DIR = DATA_DIR / "video_generations"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_RESOLUTION = "512x512"
_DEFAULT_DURATION = 5
_DEFAULT_FPS = 24

# A vision model can only digest a limited number of frames per request.
SUPPORTED_VIDEO_EXTS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".flv",
    ".m4v",
}

# --------------------------------------------------------------------------- #
# CJK font probing (for Chinese overlay subtitles)
# --------------------------------------------------------------------------- #
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/Noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _find_cjk_font() -> str:
    for f in _CJK_FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    return ""


# --------------------------------------------------------------------------- #
# Ollama helpers (async, reuses Open WebUI's shared aiohttp session + config)
# --------------------------------------------------------------------------- #
def _ollama_base_url(request: Request) -> str:
    base_urls = request.app.state.config.OLLAMA_BASE_URLS
    if not base_urls:
        raise HTTPException(503, "No Ollama backend configured")
    return base_urls[0].rstrip("/")


async def _ollama_generate(
    request: Request,
    model: str,
    prompt: str,
    system: Optional[str] = None,
    expect_json: bool = False,
) -> str:
    """Call Ollama /api/generate once (non-streaming) and return the text."""
    url = _ollama_base_url(request)
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if system:
        payload["system"] = system
    if expect_json:
        payload["format"] = "json"

    session = await get_session()
    headers = {"Content-Type": "application/json"}
    key = get_api_key(0, url, request.app.state.config.OLLAMA_API_CONFIGS)
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        async with session.post(
            f"{url}/api/generate",
            data=json.dumps(payload),
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=aiohttp.ClientTimeout(total=600),
        ) as r:
            if r.status != 200:
                detail = await r.text()
                raise HTTPException(r.status, f"Ollama: {detail[:300]}")
            data = await r.json()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception(exc)
        raise HTTPException(500, f"Ollama request failed: {exc}")
    return (data.get("response") or "").strip()


async def _list_ollama_models(request: Request) -> list[str]:
    try:
        url = _ollama_base_url(request)
    except HTTPException:
        return []
    session = await get_session()
    try:
        async with session.get(
            f"{url}/api/tags",
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to list Ollama models: %s", exc)
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


# --------------------------------------------------------------------------- #
# Prompt engineering (enhance / suggest) — local-template fallback if Ollama off
# --------------------------------------------------------------------------- #
ENHANCEMENT_TEMPLATES = {
    "cinematic": [
        "电影级画面，{prompt}，专业摄影，景深效果，色彩分级",
        "{prompt}，电影质感，戏剧化照明，宽银幕比例",
        "{prompt}，IMAX画质，史诗级场景，精心构图",
    ],
    "anime": [
        "动漫风格，{prompt}，吉卜力工作室风格，柔和色彩",
        "{prompt}，日式动画，精美背景，角色设计",
        "{prompt}，二次元风格，萌系画风，鲜艳色彩",
    ],
    "realistic": [
        "超写实，{prompt}，8K分辨率，细节丰富",
        "{prompt}，照片级真实，自然光照，精细纹理",
        "{prompt}，真实世界，纪录片风格，专业拍摄",
    ],
    "artistic": [
        "艺术风格，{prompt}，油画质感，印象派",
        "{prompt}，水彩画风格，艺术构图，美学设计",
        "{prompt}，数字艺术，概念设计，视觉冲击力",
    ],
    "sci-fi": [
        "科幻风格，{prompt}，未来主义，霓虹灯光",
        "{prompt}，赛博朋克，高科技，全息投影",
        "{prompt}，太空场景，星际旅行，未来城市",
    ],
}

DEFAULT_TEMPLATES = [
    "高质量视频，{prompt}，流畅动画，专业制作",
    "{prompt}，精美画面，视觉震撼，高清画质",
    "{prompt}，动态场景，流畅过渡，专业后期",
]


async def _enhance_prompt(request: Request, prompt: str, style: str, model: str) -> str:
    system = (
        "你是一位专业的 AI 视频生成提示词工程师。"
        "请把用户的简短描述扩写成一段富有画面感的中文视频生成提示词，"
        "需包含：镜头景别、光线、运镜方式、画面风格与情绪氛围，"
        "长度不超过 120 字。只输出提示词本身，不要解释、不要引号。"
    )
    if model:
        out = await _ollama_generate(request, model, prompt, system=system)
        if out:
            return out.strip()
    templates = ENHANCEMENT_TEMPLATES.get(style, DEFAULT_TEMPLATES)
    import random

    return random.choice(templates).format(prompt=prompt)


async def _suggest_variations(
    request: Request, prompt: str, style: str, count: int, model: str
) -> list[str]:
    system = (
        f"请基于用户的视频创意，生成 {count} 个风格或角度不同的中文视频生成提示词变体。"
        f"每个变体一行，不要编号、不要解释。只输出变体本身。"
    )
    if model:
        out = await _ollama_generate(request, model, prompt, system=system)
        if out:
            variations = []
            for line in out.strip().splitlines():
                line = line.strip().lstrip("0123456789.、-（）() ")
                if line:
                    variations.append(line)
            if variations:
                return variations[:count]
    import random

    templates = ENHANCEMENT_TEMPLATES.get(style, DEFAULT_TEMPLATES)
    return random.sample(templates, min(count, len(templates)))


# --------------------------------------------------------------------------- #
# Director shot-plan (structured JSON from the LLM)
# --------------------------------------------------------------------------- #
async def _generate_video_plan(
    request: Request, prompt: str, style: str, model: str, duration: int, resolution: str
) -> dict:
    system = (
        "你是 AI 视频导演。请根据用户的视频创意，输出一段 JSON，不要任何额外文字：\n"
        "{\n"
        '  "palette": [主色调R, G, B，0-255的整数],\n'
        '  "accent": [辅色R, G, B，0-255的整数],\n'
        '  "motion": "gradient 或 zoom 或 pan 或 wave 或 pulse",\n'
        '  "mood": "情绪/氛围中文短描述",\n'
        '  "overlay_text": "建议叠加在视频上的中文短句，不超过12字，没有则空字符串",\n'
        '  "scenes": ["镜头1描述", "镜头2描述"]\n'
        "}"
    )
    plan: dict = {}
    if model:
        out = await _ollama_generate(request, model, prompt, system=system, expect_json=True)
        try:
            plan = json.loads(out) if out else {}
        except (json.JSONDecodeError, TypeError):
            plan = {}
    return _normalize_plan(plan, prompt, resolution, duration)


def _normalize_plan(plan: dict, prompt: str, resolution: str, duration: int) -> dict:
    seed = int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16)

    def to_rgb(v, fallback):
        try:
            if isinstance(v, (list, tuple)) and len(v) >= 3:
                return [max(0, min(255, int(x))) for x in v[:3]]
        except (TypeError, ValueError):
            pass
        return fallback

    palette = to_rgb(plan.get("palette"), [(seed >> 16) & 0xFF, (seed >> 8) & 0xFF, seed & 0xFF])
    accent = to_rgb(plan.get("accent"), [(seed >> 8) & 0xFF, seed & 0xFF, (seed >> 16) & 0xFF])

    allowed = {"gradient", "zoom", "pan", "wave", "pulse"}
    motion = plan.get("motion")
    if motion not in allowed:
        import random

        motion = random.choice(list(allowed))

    mood = plan.get("mood") or "neutral"
    overlay_text = (str(plan.get("overlay_text") or ""))[:12]
    scenes = plan.get("scenes") or []
    if not isinstance(scenes, list):
        scenes = []
    scenes = [str(s) for s in scenes][:3]

    return {
        "palette": palette,
        "accent": accent,
        "motion": motion,
        "mood": mood,
        "overlay_text": overlay_text,
        "scenes": scenes,
        "prompt": prompt,
        "resolution": resolution,
        "duration": duration,
    }


# --------------------------------------------------------------------------- #
# FFmpeg rendering (synchronous — run via asyncio.to_thread)
# --------------------------------------------------------------------------- #
def _render_video(
    prompt: str,
    plan: dict,
    output_path: str,
    duration: int,
    resolution: str,
    fps: int,
) -> dict:
    """Render a short, playable video from an LLM plan using FFmpeg.

    Uses the gradient/Ken-Burns fallback path (no GPU/SD required) — the same
    approach as the standalone video_large_model_project, with the ``wave``
    motion fixed to use only filters compiled into this machine's ffmpeg.
    """
    w, h = resolution.split("x")
    w, h = int(w), int(h)
    palette = plan.get("palette", [80, 80, 160])
    accent = plan.get("accent", [200, 120, 60])
    motion = plan.get("motion", "gradient")
    overlay_text = (plan.get("overlay_text") or "").strip()

    c0 = "0x%02x%02x%02x" % (palette[0], palette[1], palette[2])
    c1 = "0x%02x%02x%02x" % (accent[0], accent[1], accent[2])

    filt = (
        f"gradients=s={w}x{h}:duration={duration}:c0={c0}:"
        f"c1={c1}:x0=0:y0=0:x1={w}:y1={h}:speed=0.02,format=yuv420p"
    )

    if motion == "zoom":
        filt += f",zoompan=z='min(zoom+0.002,1.8)':d=1:s={w}x{h}:fps={fps}"
    elif motion == "pan":
        filt += f",zoompan=z=1.25:d=1:s={w}x{h}:fps={fps}"
    elif motion == "wave":
        # Local ffmpeg has no 'waves' video filter — simulate with zoompan + sine brightness.
        filt += (
            f",zoompan=z='min(zoom+0.002,1.3)':d=1:s={w}x{h}:fps={fps}"
            f",eq=contrast=1.05:brightness='0.06*sin(t*3)'"
        )
    elif motion == "pulse":
        filt += ",eq=contrast=1.3:brightness='0.05*sin(t*3)'"

    cjk_font = _find_cjk_font()
    if overlay_text and cjk_font:
        safe = overlay_text.replace(":", "\\:").replace("'", "\\'")
        fs = max(16, h // 16)
        filt += (
            f",drawtext=fontfile='{cjk_font}':text='{safe}':"
            f"fontcolor=white:fontsize={fs}:"
            f"box=1:boxcolor=black@0.45:boxborderw=8:"
            f"x=(w-text_w)/2:y=h-text_h-24"
        )

    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", filt,
        "-fps_mode", "cfr",
        "-r", str(fps),
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg error: {result.stderr.decode('utf-8', errors='replace')[-500:]}"
        )

    thumb_path = output_path.replace(".mp4", "_thumb.jpg")
    thumb_cmd = [
        FFMPEG, "-y",
        "-i", output_path,
        "-vframes", "1",
        "-ss", str(max(1, duration // 2)),
        "-s", "320x240",
        thumb_path,
    ]
    subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    return {"thumbnail_path": thumb_path}


def _probe_video(path: str) -> dict:
    cmd = [
        FFPROBE, "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        return {}
    data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        fps = eval(vstream.get("r_frame_rate", "0/1"))  # noqa: S307
    except Exception:  # noqa: BLE001
        fps = 0
    return {
        "duration": float(fmt.get("duration", 0)),
        "width": int(vstream.get("width", 0)),
        "height": int(vstream.get("height", 0)),
        "fps": fps,
        "codec": vstream.get("codec_name", ""),
        "file_size": int(fmt.get("size", 0)),
    }


# --------------------------------------------------------------------------- #
# Range-aware static file serving (so <video> can stream / seek)
# --------------------------------------------------------------------------- #
def _serve_file(request: Request, path: str, media_type: str) -> Response:
    size = os.path.getsize(path)
    range_header = request.headers.get("range")
    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
            headers = {
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
            }
            return Response(chunk, status_code=206, headers=headers, media_type=media_type)
    with open(path, "rb") as f:
        data = f.read()
    return Response(
        data,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        media_type=media_type,
    )


def _safe_name(name: str) -> str:
    base = os.path.basename(name)
    if not base or ".." in base or "/" in base:
        raise HTTPException(400, "Invalid file name")
    return base


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class EnhanceForm(BaseModel):
    prompt: str
    style: str = "cinematic"
    model: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class SuggestForm(BaseModel):
    prompt: str
    style: str = "cinematic"
    count: int = 3
    model: Optional[str] = None
    model_config = ConfigDict(extra="allow")


class GenerateForm(BaseModel):
    prompt: str
    model: Optional[str] = None
    style: str = "cinematic"
    duration: int = _DEFAULT_DURATION
    resolution: str = _DEFAULT_RESOLUTION
    fps: int = _DEFAULT_FPS
    model_config = ConfigDict(extra="allow")


class VideoGenResult(BaseModel):
    name: str
    video_url: str
    thumbnail_url: str
    enhanced_prompt: str
    plan: dict
    duration: float
    resolution: str
    fps: int
    file_size: int
    mood: str
    motion: str
    status: str = "done"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/health")
async def health(user=Depends(get_verified_user)) -> dict:
    def _which(bin_name: str) -> bool:
        try:
            r = subprocess.run(
                ["which", bin_name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
            )
            return r.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    ffmpeg_ok = _which(FFMPEG)
    ffprobe_ok = _which(FFPROBE)
    return {
        "status": ffmpeg_ok and ffprobe_ok,
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "supported_exts": sorted(SUPPORTED_VIDEO_EXTS),
        "defaults": {
            "duration": _DEFAULT_DURATION,
            "resolution": _DEFAULT_RESOLUTION,
            "fps": _DEFAULT_FPS,
        },
    }


@router.get("/models")
async def models(request: Request, user=Depends(get_verified_user)) -> dict:
    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(503, ERROR_MESSAGES.OLLAMA_API_DISABLED)
    models = await _list_ollama_models(request)
    return {"models": [m for m in models if m]}


@router.post("/enhance")
async def enhance(
    request: Request, form: EnhanceForm, user=Depends(get_verified_user)
) -> dict:
    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(503, ERROR_MESSAGES.OLLAMA_API_DISABLED)
    if not form.prompt.strip():
        raise HTTPException(400, "Prompt is required")
    model = form.model or ""
    enhanced = await _enhance_prompt(request, form.prompt.strip(), form.style, model)
    return {"enhanced_prompt": enhanced}


@router.post("/suggest")
async def suggest(
    request: Request, form: SuggestForm, user=Depends(get_verified_user)
) -> dict:
    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(503, ERROR_MESSAGES.OLLAMA_API_DISABLED)
    if not form.prompt.strip():
        raise HTTPException(400, "Prompt is required")
    model = form.model or ""
    variations = await _suggest_variations(
        request, form.prompt.strip(), form.style, max(1, min(form.count, 8)), model
    )
    return {"variations": variations}


@router.post("/generate")
async def generate(
    request: Request, form: GenerateForm, user=Depends(get_verified_user)
) -> VideoGenResult:
    if not request.app.state.config.ENABLE_OLLAMA_API:
        raise HTTPException(503, ERROR_MESSAGES.OLLAMA_API_DISABLED)

    prompt = (form.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "Prompt is required")

    duration = max(1, min(int(form.duration), 30))
    fps = max(1, min(int(form.fps), 60))
    if not re.match(r"^\d+x\d+$", form.resolution):
        raise HTTPException(400, "resolution must be like 512x512")
    resolution = form.resolution

    # Auto-pick a model if the caller did not specify one.
    model = form.model or ""
    if not model:
        models = await _list_ollama_models(request)
        model = models[0] if models else ""

    # 1) Enhance the prompt (LLM) and 2) plan the shot list (LLM, structured).
    enhanced_prompt = await _enhance_prompt(request, prompt, form.style, model)
    plan = await _generate_video_plan(request, prompt, form.style, model, duration, resolution)

    # 3) Render the video with FFmpeg.
    name = f"gen_{int(time.time() * 1000)}.mp4"
    output_path = os.path.join(str(PREVIEW_DIR), name)
    try:
        await asyncio.to_thread(
            _render_video, enhanced_prompt, plan, output_path, duration, resolution, fps
        )
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    meta = _probe_video(output_path)
    thumb_name = name.replace(".mp4", "_thumb.jpg")
    thumb_path = os.path.join(str(PREVIEW_DIR), thumb_name)

    return VideoGenResult(
        name=name,
        video_url=f"/api/v1/video-gen/file/{name}",
        thumbnail_url=f"/api/v1/video-gen/file/{thumb_name}",
        enhanced_prompt=enhanced_prompt,
        plan=plan,
        duration=meta.get("duration", float(duration)),
        resolution=resolution,
        fps=int(meta.get("fps", fps)),
        file_size=meta.get("file_size", os.path.getsize(output_path)),
        mood=plan.get("mood", ""),
        motion=plan.get("motion", ""),
    )


@router.get("/list")
async def list_videos(user=Depends(get_verified_user)) -> dict:
    items = []
    try:
        for fn in sorted(os.listdir(str(PREVIEW_DIR)), reverse=True):
            if not fn.endswith(".mp4"):
                continue
            path = os.path.join(str(PREVIEW_DIR), fn)
            if not os.path.isfile(path):
                continue
            meta = _probe_video(path)
            thumb = fn.replace(".mp4", "_thumb.jpg")
            thumb_url = (
                f"/api/v1/video-gen/file/{thumb}"
                if os.path.exists(os.path.join(str(PREVIEW_DIR), thumb))
                else ""
            )
            items.append(
                {
                    "name": fn,
                    "video_url": f"/api/v1/video-gen/file/{fn}",
                    "thumbnail_url": thumb_url,
                    "duration": meta.get("duration", 0),
                    "resolution": f"{meta.get('width', 0)}x{meta.get('height', 0)}",
                    "fps": int(meta.get("fps", 0)),
                    "file_size": meta.get("file_size", os.path.getsize(path)),
                    "created_at": os.path.getctime(path),
                }
            )
    except FileNotFoundError:
        pass
    return {"videos": items}


@router.get("/file/{name}")
async def file(request: Request, name: str) -> Response:
    # Lightweight auth: accept a Bearer header (API calls) or a ?token= query
    # param (so the browser <video> element can stream the file). Acceptable for
    # a local, offline-only tool whose generated videos belong to the user.
    token = request.headers.get("authorization") or request.query_params.get("token")
    if not token:
        raise HTTPException(401, "Authentication required")
    base = _safe_name(name)
    path = os.path.join(str(PREVIEW_DIR), base)
    if not os.path.isfile(path):
        raise HTTPException(404, "File not found")
    media_type = "video/mp4" if base.endswith(".mp4") else "image/jpeg"
    return _serve_file(request, path, media_type)
