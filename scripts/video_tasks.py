#!/usr/bin/env python3
"""启动时自动执行的「本地视频分析任务」运行器（完全离线）。

设计目标
--------
- 在代码/配置里预先写好要分析的视频任务（见同目录上层的 ``video-tasks.json``），
  启动系统时自动逐个调用本机 Ollama 视觉模型完成分析，无需登录、无需后端。
- 真实读取视频「主要参数」：时长 / 分辨率 / 帧率 / 编码（用 OpenCV，不依赖 ffmpeg/ffprobe）。
- 抽帧 → 视觉模型逐帧描述 → 文本模型汇总 → 在视频同目录写出 ``<name>.analysis.md``。

仅依赖：opencv-python(headless) + Python 标准库（urllib）。直连 Ollama HTTP API。

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
DEFAULT_MAX_FRAMES = 16        # 单个视频最多抽帧数
MAX_FRAME_EDGE = 768           # 长边缩放上限，控制 base64 体积
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
# --------------------------------------------------------------------------- #
def extract_frames(path: str, frame_interval: float, max_frames: int) -> list[tuple[float, str]]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError('无法打开视频用于抽帧')

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (total_frames / fps) if fps > 0 else 0.0

    if duration > 0:
        interval = max(frame_interval, duration / max_frames)
        timestamps, t = [], 0.0
        while t < duration and len(timestamps) < max_frames:
            timestamps.append(t)
            t += interval
    else:
        step = max(1, (total_frames or max_frames) // max_frames)
        timestamps = [float(i) for i in range(0, total_frames or max_frames, step)][:max_frames]

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
def ollama_chat(ollama_url: str, model: str, content: str, images: Optional[list[str]] = None) -> str:
    message: dict = {'role': 'user', 'content': content}
    if images:
        message['images'] = images
    payload = {'model': model, 'messages': [message], 'stream': False, 'options': {'temperature': 0.2}}
    req = urllib.request.Request(
        f'{ollama_url.rstrip("/")}/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return (data.get('message') or {}).get('content', '') or ''


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


def summary_prompt(language: str, meta: dict) -> str:
    if language == 'en':
        head = ('Below are time-ordered frame descriptions of one video. Synthesize them into a '
                'structured analysis with sections: ## Overview, ## Timeline, ## Key Subjects & Actions, '
                '## Highlights, ## Tags. Resolve repetition and infer the storyline.')
    else:
        head = ('以下是从同一视频按时间顺序抽取的若干帧画面描述。请整合为结构化分析，包含小节：'
                '## 概述、## 时间线、## 主要人物与动作、## 看点、## 标签。请去重并推断整体脉络。')
    return f'{head}\n\n（参考主要参数：时长 {meta["duration_hms"]} / 分辨率 {meta["resolution"]} / {meta["fps"]}fps / 编码 {meta["codec"]}）'


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
    save_report = task.get('save_report', True)
    custom_prompt = task.get('prompt')

    if not model:
        _c('ERR', f'未指定视觉模型（task.model 或 default_model）：{video_path}')
        return

    started = time.time()
    _c('INFO', f'分析：{video_path}')

    # 1) 主要参数
    meta = probe_metadata(video_path)
    _c('OK', f'参数 → 时长 {meta["duration_hms"]} ({meta["duration_sec"]}s) | '
             f'分辨率 {meta["resolution"]} | {meta["fps"]}fps | 编码 {meta["codec"]} | {meta["size_mb"]}MB')

    # 2) 抽帧
    frames = extract_frames(video_path, max(0.5, interval), max(1, min(max_frames, 64)))
    if not frames:
        _c('ERR', '未能从视频解码出任何帧')
        return
    _c('INFO', f'已抽取 {len(frames)} 帧，使用视觉模型 {model} 逐帧识别…')

    # 3) 逐帧描述（顺序，避免本机显存压力）
    fp = frame_prompt(language, custom_prompt)
    frame_results: list[tuple[int, float, str]] = []
    for i, (ts, b64) in enumerate(frames):
        try:
            desc = ollama_chat(ollama_url, model, fp, images=[b64]).strip()
        except Exception as exc:
            desc = f'(帧识别失败：{exc})'
        frame_results.append((i, ts, desc))
        _c('INFO', f'  帧 {i + 1}/{len(frames)} @ {ts:.1f}s')

    # 4) 汇总
    _c('INFO', f'使用 {summary_model} 汇总…')
    joined = '\n'.join(f'- [{ts:.1f}s] {desc}' for _, ts, desc in frame_results)
    fuse_input = f'{summary_prompt(language, meta)}\n\n## 画面帧描述\n{joined}'
    try:
        summary = ollama_chat(ollama_url, summary_model, fuse_input).strip()
    except Exception as exc:
        summary = f'(汇总失败：{exc})'

    elapsed = round(time.time() - started, 2)
    _c('OK', f'完成：{video_path}（耗时 {elapsed}s）')

    # 5) 写报告
    if save_report:
        report_path = write_report(video_path, model, summary_model, meta, frame_results, summary, elapsed)
        _c('OK', f'报告已保存 → {report_path}')


def write_report(video_path, model, summary_model, meta, frame_results, summary, elapsed) -> str:
    base, _ = os.path.splitext(video_path)
    report_path = f'{base}.analysis.md'
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
        '---',
        '',
        '## 逐帧描述 / Per-frame descriptions',
        '',
    ]
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
