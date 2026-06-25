#!/usr/bin/env python3
"""启动时自动执行的「本地视频分析任务」运行器（完全离线）。

设计目标
--------
- 在代码/配置里预先写好要分析的视频任务（见同目录上层的 ``video-tasks.json``），
  启动系统时自动逐个调用本机 Ollama 视觉模型完成分析，无需登录、无需后端。
- 真实读取视频「主要参数」：时长 / 分辨率 / 帧率 / 编码（用 OpenCV，不依赖 ffmpeg/ffprobe）。
- 抽帧 → 视觉模型逐帧描述 →（可选）faster-whisper 转写音轨 → 文本模型结合声画汇总
  → 在视频同目录写出 ``<name>.analysis.md``（开启音频时另写 ``<name>.srt`` 字幕）。

依赖：opencv-python(headless) 必需；faster-whisper 可选（开启音频转写时才需要，
内部用 PyAV 直接从视频解码音轨，无需系统 ffmpeg）。其余仅用 Python 标准库（urllib）。
直连 Ollama HTTP API。

用法
----
    python scripts/video_tasks.py                          # 用默认 video-tasks.json
    python scripts/video_tasks.py --config path/to.json    # 指定配置
    python scripts/video_tasks.py --ollama-url http://localhost:11434
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from typing import Optional

# macOS 上 cv2 与 faster-whisper 依赖的 av(PyAV) 各自携带一套 libav，
# 会触发 "Class AVFFrameReceiver is implemented in both ..." 重复加载告警，
# 极端情况下可能导致神秘崩溃。必须在导入 cv2 / av 之前设置以下环境变量。
os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

# OpenCV 是唯一的第三方依赖；缺失时给出清晰提示。
try:
    import cv2  # type: ignore
except Exception as exc:  # pragma: no cover
    print(f'[ERR ] 需要 opencv-python-headless：{exc}', file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------- #
# 默认参数（与后端 video_analysis.py 保持一致的量级）
# --------------------------------------------------------------------------- #
DEFAULT_FRAME_INTERVAL = 5.0   # 抽帧间隔（秒）
DEFAULT_MAX_FRAMES = 16        # 单个视频默认抽帧数
MAX_FRAMES_HARD_CAP = 2000     # 安全上限：可大幅调高 max_frames 以「时间换精度」（越多越慢越细）
MAX_FRAME_EDGE = 768           # 长边缩放上限，控制 base64 体积
DEFAULT_WHISPER_MODEL = 'base'  # faster-whisper 模型：tiny/base/small/medium/large-v3（越大越准越慢）
SUPPORTED_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.m4v', '.mpg', '.mpeg', '.wmv', '.ts'}


def _c(tag: str, msg: str) -> None:
    colors = {'INFO': '34', 'OK': '32', 'WARN': '33', 'ERR': '31'}
    print(f'\033[1;{colors.get(tag, "0")}m[{tag:>4}]\033[0m {msg}', flush=True)


# --------------------------------------------------------------------------- #
# 1) 主要参数：时长 / 分辨率 / 帧率 / 编码（OpenCV，无需 ffprobe）
# --------------------------------------------------------------------------- #
def probe_metadata(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError('无法打开视频（编码不支持？）')

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    cap.release()

    codec = ''.join([chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)]).strip('\x00 ').strip()
    duration = (frame_count / fps) if fps > 0 else 0.0
    size_bytes = os.path.getsize(path) if os.path.isfile(path) else 0

    return {
        'duration_sec': round(duration, 2),
        'duration_hms': time.strftime('%H:%M:%S', time.gmtime(duration)) if duration else 'unknown',
        'width': width,
        'height': height,
        'resolution': f'{width}x{height}' if width and height else 'unknown',
        'fps': round(fps, 2),
        'frame_count': frame_count,
        'codec': codec or 'unknown',
        'size_mb': round(size_bytes / 1024 / 1024, 2),
    }


# --------------------------------------------------------------------------- #
# 2) 抽帧（OpenCV）
# sample_mode:
#   'count'    固定张数：全片均匀抽 max_frames 帧（无视 frame_interval）
#   'interval' 时间跨度：每 frame_interval 秒抽 1 帧（长视频更完整，受硬上限保护）
#   'auto'     兼容旧行为：≤ max_frames 帧，且不密于 frame_interval
# --------------------------------------------------------------------------- #
def extract_frames(
    path: str, frame_interval: float, max_frames: int, sample_mode: str = 'auto'
) -> list[tuple[float, str]]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError('无法打开视频用于抽帧')

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (total_frames / fps) if fps > 0 else 0.0

    if duration > 0:
        if sample_mode == 'count':
            limit = max(1, min(max_frames, MAX_FRAMES_HARD_CAP))
            step = duration / limit
        elif sample_mode == 'interval':
            step = max(0.5, frame_interval)
            limit = int(duration // step) + 1
            if limit > MAX_FRAMES_HARD_CAP:  # 太长则退化为均匀铺满上限
                limit = MAX_FRAMES_HARD_CAP
                step = duration / limit
        else:  # auto
            limit = max(1, min(max_frames, MAX_FRAMES_HARD_CAP))
            step = max(frame_interval, duration / limit)

        timestamps, t = [], 0.0
        while t < duration and len(timestamps) < limit:
            timestamps.append(t)
            t += step
    else:
        # 时长未知：按帧索引兜底
        limit = max(1, min(max_frames, MAX_FRAMES_HARD_CAP))
        step = max(1, (total_frames or limit) // limit)
        timestamps = [float(i) for i in range(0, total_frames or limit, step)][:limit]

    out: list[tuple[float, str]] = []
    for ts in timestamps:
        if duration > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        else:
            cap.set(cv2.CAP_PROP_POS_FRAMES, ts)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        long_edge = max(h, w)
        if long_edge > MAX_FRAME_EDGE:
            scale = MAX_FRAME_EDGE / long_edge
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            continue
        out.append((round(ts, 2), base64.b64encode(buf.tobytes()).decode('utf-8')))
    cap.release()
    return out


# --------------------------------------------------------------------------- #
# 3) 调用 Ollama /api/chat（标准库 urllib，同步）
# --------------------------------------------------------------------------- #
def ollama_chat(
    ollama_url: str,
    model: str,
    content: str,
    images: Optional[list[str]] = None,
    *,
    stream: bool = False,
    on_delta=None,
) -> str:
    """调用 Ollama /api/chat。

    - ``stream=False``：一次性返回完整文本（默认）。
    - ``stream=True``：边收边吐，每收到一段增量文本就回调 ``on_delta(delta)``，
      可用于把模型输出**实时**打印到终端；最终仍返回拼接后的完整文本。
    """
    message: dict = {'role': 'user', 'content': content}
    if images:
        message['images'] = images
    payload = {'model': model, 'messages': [message], 'stream': bool(stream), 'options': {'temperature': 0.2}}
    req = urllib.request.Request(
        f'{ollama_url.rstrip("/")}/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    if not stream:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return (data.get('message') or {}).get('content', '') or ''

    parts: list[str] = []
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:  # Ollama 流式为按行的 NDJSON
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode('utf-8'))
            except Exception:
                continue
            delta = (obj.get('message') or {}).get('content', '') or ''
            if delta:
                parts.append(delta)
                if on_delta:
                    on_delta(delta)
            if obj.get('done'):
                break
    return ''.join(parts)


# --------------------------------------------------------------------------- #
# 3.5) 音频转写（faster-whisper，可选）
# faster-whisper 内部用 PyAV 直接从视频解码音轨，无需系统安装 ffmpeg。
# --------------------------------------------------------------------------- #
_WHISPER_CACHE: dict = {}  # 同一进程内复用已加载的模型，避免重复初始化


def _srt_ts(seconds: float) -> str:
    """秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def transcribe_audio(video_path: str, whisper_model: str, language: Optional[str] = None):
    """用 faster-whisper 转写视频音轨。

    返回 ``(full_text, segments)``，其中 segments 为 ``[(start, end, text), ...]``。
    未安装 faster-whisper、模型加载失败或视频无音轨时，返回 ``('', [])``。
    """
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        _c('WARN', f'未安装 faster-whisper，跳过音频转写：{exc}')
        return '', []

    model = _WHISPER_CACHE.get(whisper_model)
    if model is None:
        _c('INFO', f'加载 Whisper 模型「{whisper_model}」(首次会下载/初始化, CPU int8)…')
        try:
            model = WhisperModel(whisper_model, device='cpu', compute_type='int8')
        except Exception as exc:
            _c('WARN', f'加载 Whisper 失败，跳过音频：{exc}')
            return '', []
        _WHISPER_CACHE[whisper_model] = model

    try:
        segments, _info = model.transcribe(video_path, beam_size=5, language=language or None)
        seg_list = [(float(s.start), float(s.end), (s.text or '').strip()) for s in segments]
    except Exception as exc:
        _c('WARN', f'音频转写失败（可能无音轨）：{exc}')
        return '', []

    full_text = ' '.join(t for _, _, t in seg_list if t).strip()
    return full_text, seg_list


def _out_base(video_path: str, output_dir: Optional[str]) -> str:
    """计算输出文件的「无扩展名基准路径」。

    - ``output_dir`` 为空：沿用旧行为，与视频同目录、同主名。
    - ``output_dir`` 非空：写到该目录下，仍用视频主名（不含扩展名）。
    """
    base, _ = os.path.splitext(video_path)
    if output_dir:
        return os.path.join(output_dir, os.path.basename(base))
    return base


def write_srt(video_path: str, segments, output_dir: Optional[str] = None) -> Optional[str]:
    """把转写分段写成与视频同名的 .srt 字幕文件（可指定 output_dir）。"""
    if not segments:
        return None
    srt_path = f'{_out_base(video_path, output_dir)}.srt'
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, (start, end, text) in enumerate(segments, 1):
            f.write(f'{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n\n')
    return srt_path


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
def frame_prompt(language: str, custom: Optional[str]) -> str:
    if custom:
        return custom
    if language == 'en':
        return ('Describe this single video frame in detail: scene, people, actions, '
                'on-screen text, key objects and overall mood. Be concise (2-4 sentences).')
    return '请详细描述这一帧画面：场景、人物、动作、画面文字、关键物体以及整体氛围。请简洁（2-4 句）。'


def summary_prompt(language: str, meta: dict, transcript: str = '') -> str:
    if language == 'en':
        head = ('Below are time-ordered frame descriptions of one video. Synthesize them into a '
                'structured analysis with sections: ## Overview, ## Timeline, ## Key Subjects & Actions, '
                '## Highlights, ## Tags. Resolve repetition and infer the storyline.')
    else:
        head = ('以下是从同一视频按时间顺序抽取的若干帧画面描述。请整合为结构化分析，包含小节：'
                '## 概述、## 时间线、## 主要人物与动作、## 看点、## 标签。请去重并推断整体脉络。')
    out = f'{head}\n\n（参考主要参数：时长 {meta["duration_hms"]} / 分辨率 {meta["resolution"]} / {meta["fps"]}fps / 编码 {meta["codec"]}）'
    if transcript:
        out += ('\n\nAn audio transcript is also provided; combine audio and visuals.'
                if language == 'en' else
                '\n\n另外提供了音频转写文本（台词/旁白），请结合声音与画面一起分析。')
    return out


# --------------------------------------------------------------------------- #
# 单个任务
# --------------------------------------------------------------------------- #
def run_task(task: dict, defaults: dict, ollama_url: str) -> None:
    video_path = os.path.expanduser(task['video_path'])
    if not os.path.isfile(video_path):
        _c('ERR', f'视频不存在：{video_path}')
        return
    if os.path.splitext(video_path)[1].lower() not in SUPPORTED_EXTS:
        _c('ERR', f'不支持的视频格式：{video_path}')
        return

    model = task.get('model') or defaults.get('default_model')
    summary_model = task.get('summary_model') or defaults.get('default_summary_model') or model
    language = task.get('language', defaults.get('language', 'zh'))
    interval = float(task.get('frame_interval', DEFAULT_FRAME_INTERVAL))
    max_frames = int(task.get('max_frames', DEFAULT_MAX_FRAMES))
    sample_mode = str(task.get('sample_mode') or defaults.get('sample_mode') or 'auto').lower()
    save_report = task.get('save_report', True)
    custom_prompt = task.get('prompt')
    include_audio = bool(task.get('include_audio', defaults.get('include_audio', False)))
    whisper_model = task.get('whisper_model') or defaults.get('whisper_model') or DEFAULT_WHISPER_MODEL
    whisper_language = task.get('whisper_language') or defaults.get('whisper_language')
    # 报告/字幕输出目录：留空＝写到视频同目录（旧行为）；填了就写到该目录（自动创建）。
    output_dir = task.get('output_dir') or defaults.get('output_dir')
    if output_dir:
        output_dir = os.path.expanduser(output_dir)
        os.makedirs(output_dir, exist_ok=True)
    # 是否把逐帧描述与汇总结果实时打印到终端（默认开启）。
    stream_log = bool(task.get('stream_log', defaults.get('stream_log', True)))

    if not model:
        _c('ERR', f'未指定视觉模型（task.model 或 default_model）：{video_path}')
        return

    started = time.time()
    _c('INFO', f'分析：{video_path}')

    # 1) 主要参数
    meta = probe_metadata(video_path)
    _c('OK', f'参数 → 时长 {meta["duration_hms"]} ({meta["duration_sec"]}s) | '
             f'分辨率 {meta["resolution"]} | {meta["fps"]}fps | 编码 {meta["codec"]} | {meta["size_mb"]}MB')

    # 2) 抽帧（按 sample_mode 选择抽样策略；可「时间换精度」）
    if sample_mode == 'interval':
        _c('INFO', f'抽帧方式：时间跨度，每 {interval:g}s 一帧（安全上限 {MAX_FRAMES_HARD_CAP} 帧）')
    elif sample_mode == 'count':
        _c('INFO', f'抽帧方式：固定张数，全片均匀 {max_frames} 帧')
    else:
        _c('INFO', f'抽帧方式：自动（≤{max_frames} 帧且不密于 {interval:g}s）')
    frames = extract_frames(video_path, max(0.5, interval), max_frames, sample_mode)
    if not frames:
        _c('ERR', '未能从视频解码出任何帧')
        return
    _c('INFO', f'已抽取 {len(frames)} 帧，使用视觉模型 {model} 逐帧识别…')

    # 3) 逐帧描述（顺序，避免本机显存压力）
    fp = frame_prompt(language, custom_prompt)
    frame_results: list[tuple[int, float, str]] = []
    for i, (ts, b64) in enumerate(frames):
        _c('INFO', f'  帧 {i + 1}/{len(frames)} @ {ts:.1f}s')
        try:
            if stream_log:
                # 流式：边识别边把描述吐到终端，实时可见
                sys.stdout.write('\033[2m      ')  # 暗色缩进前缀
                sys.stdout.flush()
                desc = ollama_chat(
                    ollama_url, model, fp, images=[b64],
                    stream=True, on_delta=lambda d: (sys.stdout.write(d), sys.stdout.flush()),
                ).strip()
                sys.stdout.write('\033[0m\n')
                sys.stdout.flush()
            else:
                desc = ollama_chat(ollama_url, model, fp, images=[b64]).strip()
        except Exception as exc:
            desc = f'(帧识别失败：{exc})'
            _c('WARN', f'  帧 {i + 1} 识别失败：{exc}')
        frame_results.append((i, ts, desc))

    # 3.5) 可选：音频转写（faster-whisper）
    transcript, segments, srt_path = '', [], None
    if include_audio:
        _c('INFO', f'转写音频（faster-whisper / {whisper_model}）…')
        transcript, segments = transcribe_audio(video_path, whisper_model, whisper_language)
        if transcript:
            _c('OK', f'音频转写完成（{len(transcript)} 字，{len(segments)} 段）')
            if save_report:
                srt_path = write_srt(video_path, segments, output_dir=output_dir)
                if srt_path:
                    _c('OK', f'字幕已导出 → {srt_path}')
        else:
            _c('WARN', '未获得音频转写文本（可能无音轨或 faster-whisper 不可用）')

    # 4) 汇总（结合画面 + 音频）
    _c('INFO', f'使用 {summary_model} 汇总…')
    joined = '\n'.join(f'- [{ts:.1f}s] {desc}' for _, ts, desc in frame_results)
    fuse_input = f'{summary_prompt(language, meta, transcript)}\n\n## 画面帧描述\n{joined}'
    if transcript:
        fuse_input += f'\n\n## 音频转写\n{transcript}'
    try:
        if stream_log:
            sys.stdout.write('\033[2m')  # 暗色显示汇总过程
            sys.stdout.flush()
            summary = ollama_chat(
                ollama_url, summary_model, fuse_input,
                stream=True, on_delta=lambda d: (sys.stdout.write(d), sys.stdout.flush()),
            ).strip()
            sys.stdout.write('\033[0m\n')
            sys.stdout.flush()
        else:
            summary = ollama_chat(ollama_url, summary_model, fuse_input).strip()
    except Exception as exc:
        summary = f'(汇总失败：{exc})'

    elapsed = round(time.time() - started, 2)
    _c('OK', f'完成：{video_path}（耗时 {elapsed}s）')

    # 5) 写报告
    if save_report:
        report_path = write_report(
            video_path, model, summary_model, meta, frame_results, summary, elapsed,
            transcript=transcript, srt_path=srt_path, output_dir=output_dir,
        )
        _c('OK', f'报告已保存 → {report_path}')


def write_report(video_path, model, summary_model, meta, frame_results, summary, elapsed,
                 transcript: str = '', srt_path: Optional[str] = None,
                 output_dir: Optional[str] = None) -> str:
    report_path = f'{_out_base(video_path, output_dir)}.analysis.md'
    lines = [
        '# 视频离线分析报告 / Video Analysis Report',
        '',
        f'- 源文件 / Source: `{os.path.basename(video_path)}`',
        f'- 生成时间 / Generated: {datetime.now().isoformat(timespec="seconds")}',
        f'- 视觉模型 / Vision model: `{model}`',
        f'- 汇总模型 / Summary model: `{summary_model}`',
        f'- 耗时 / Elapsed: {elapsed:.2f}s',
        '',
        '## 主要参数 / Key Parameters',
        '',
        f'- 时长 / Duration: {meta["duration_hms"]}（{meta["duration_sec"]}s）',
        f'- 分辨率 / Resolution: {meta["resolution"]}',
        f'- 帧率 / FPS: {meta["fps"]}',
        f'- 总帧数 / Frames: {meta["frame_count"]}',
        f'- 编码 / Codec: {meta["codec"]}',
        f'- 文件大小 / Size: {meta["size_mb"]} MB',
        '',
        '---',
        '',
        summary,
        '',
    ]
    if transcript:
        lines += ['---', '', '## 音频转写 / Transcript', '']
        if srt_path:
            lines += [f'> 字幕文件 / SRT: `{os.path.basename(srt_path)}`', '']
        lines += [transcript, '']
    lines += ['---', '', '## 逐帧描述 / Per-frame descriptions', '']
    for idx, ts, desc in frame_results:
        lines += [f'### 帧 {idx} @ {ts:.1f}s', '', desc, '']
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return report_path


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_config = os.path.normpath(os.path.join(here, '..', 'video-tasks.json'))

    parser = argparse.ArgumentParser(description='离线视频分析任务运行器')
    parser.add_argument('--config', default=default_config, help='任务配置 JSON 路径')
    parser.add_argument('--ollama-url', default=None, help='覆盖 Ollama 地址')
    args = parser.parse_args()

    if not os.path.isfile(args.config):
        _c('ERR', f'未找到配置文件：{args.config}')
        return 1

    with open(args.config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    ollama_url = args.ollama_url or cfg.get('ollama_url') or os.getenv('OLLAMA_BASE_URL') or 'http://localhost:11434'
    tasks = cfg.get('tasks', [])
    if not tasks:
        _c('WARN', 'video-tasks.json 中没有任务（tasks 为空），跳过。')
        return 0

    # 探活 Ollama
    try:
        urllib.request.urlopen(f'{ollama_url.rstrip("/")}/api/tags', timeout=10).read()
    except Exception as exc:
        _c('ERR', f'无法连接 Ollama（{ollama_url}）：{exc}')
        return 1

    _c('INFO', f'共 {len(tasks)} 个视频任务，Ollama = {ollama_url}')
    for task in tasks:
        try:
            run_task(task, cfg, ollama_url)
        except Exception as exc:
            _c('ERR', f'任务异常（{task.get("video_path", "?")}）：{exc}')
    _c('OK', '全部视频任务执行完毕。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
