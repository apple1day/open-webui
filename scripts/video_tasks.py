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

import argparse
import base64
import hashlib
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime
from typing import Optional, List, Tuple

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

# 尝试导入可选依赖
try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False
    Image = None

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False
    np = None

# 内容去重：感知哈希
try:
    if _HAVE_PIL and _HAVE_NUMPY:
        from PIL import ImageFilter
        _HAVE_PERCEPTUAL_HASH = True
    else:
        _HAVE_PERCEPTUAL_HASH = False
except Exception:
    _HAVE_PERCEPTUAL_HASH = False

# 硬字幕OCR：PaddleOCR
try:
    from paddleocr import PaddleOCR
    _HAVE_PADDLE_OCR = True
except ImportError:
    _HAVE_PADDLE_OCR = False
    PaddleOCR = None


# --------------------------------------------------------------------------- #
# 默认参数（与后端 video_analysis.py 保持一致的量级）
# --------------------------------------------------------------------------- #
DEFAULT_FRAME_INTERVAL = 5.0   # 抽帧间隔（秒）
DEFAULT_MAX_FRAMES = 16        # 单个视频默认抽帧数
DEFAULT_MIN_FRAMES = 20        # 最少帧数保底：无论 sample_mode，最终抽帧数不少于它（受总帧数/硬上限约束）。0=不保底
MAX_FRAMES_HARD_CAP = 2000     # 安全上限：可大幅调高 max_frames 以「时间换精度」（越多越慢越细）
MAX_FRAME_EDGE = 768           # 长边缩放上限，控制 base64 体积
DEFAULT_WHISPER_MODEL = 'base'  # faster-whisper 模型：tiny/base/small/medium/large-v3（越大越准越慢）
DEFAULT_SCENE_THRESHOLD = 0.6   # scene 模式：HSV 直方图相关度阈值，低于它判为「镜头切换」（越小越不敏感、关键帧越少）
DEFAULT_SCENE_PROBE = 1.0       # scene 模式：每隔多少秒探测一次（越小越细、越慢）
SUPPORTED_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.m4v', '.mpg', '.mpeg', '.wmv', '.ts'}

# MLX 后端默认模型（Apple Silicon 原生；24GB 内存推荐 4bit 量化版）
DEFAULT_MLX_VLM_MODEL = 'mlx-community/Qwen2.5-VL-7B-Instruct-4bit'   # 视觉（逐帧识别）
DEFAULT_MLX_LM_MODEL = 'mlx-community/Qwen2.5-7B-Instruct-4bit'       # 文本（汇总）

# 本地多模态 VLM 候选（均经 Ollama 运行，Apache 2.0 可商用）。
# 当前默认用 MiniCPM-V；Google Gemma 4 多模态（本机已装 gemma4:26b）可作为更高效的
# 本地化升级替代品，逐帧质量对比见 docs/202606/gemma4_local_upgrade.md。
KNOWN_LOCAL_VLM_MODELS = (
    'minicpm-v:latest',        # 当前默认：轻量多模态（~5GB）
    'gemma4:26b',              # Google Gemma 4 多模态（已装，推荐本地升级）
    'gemma4:12b',              # Gemma 4 中档（需 ollama pull）
    'gemma4:4b',               # Gemma 4 轻量，低显存设备
    'llama3.2-vision:11b',     # Meta 视觉模型（已装）
    'qwen2.5-vl:7b',           # 阿里通义千问视觉
)
DEFAULT_FRAME_MAX_TOKENS = 300     # MLX 逐帧描述的最大生成 token
DEFAULT_SUMMARY_MAX_TOKENS = 1200  # MLX 汇总的最大生成 token

# 内容去重配置
DEFAULT_DEDUP_THRESHOLD = 10  # 感知哈希汉明距离阈值（越小越严格，推荐 5-15）
DEFAULT_DEDUP_ENABLED = False  # 是否启用内容去重

# 硬字幕OCR配置
DEFAULT_OCR_ENABLED = False  # 是否启用硬字幕OCR
DEFAULT_OCR_INTERVAL = 2.0   # OCR识别间隔（秒）
DEFAULT_OCR_LANG = 'ch'      # PaddleOCR语言：ch（中文）/ en（英文）/ japan（日文）等

# 缓存配置
DEFAULT_CACHE_ENABLED = True  # 是否启用缓存
DEFAULT_CACHE_DIR = '~/.video_analysis_cache'  # 缓存目录

# GPU加速配置
DEFAULT_GPU_ENABLED = True  # 是否启用GPU加速（如果可用）

# 并发配置
DEFAULT_CONCURRENCY = 3     # 默认并发帧分析数（与前端表单、video-tasks.json、后端路由保持一致）
MAX_CONCURRENCY = 8         # 并发安全上限：避免 Ollama 显存/连接句柄被打爆

# 笔记模式配置
DEFAULT_NOTEBOOK_MODE = False  # 是否启用笔记模式（保存截图+结构化笔记）
DEFAULT_NOTEBOOK_STYLE = 'education'  # 笔记风格：education(教育/课程), general(通用), meeting(会议)

# 帧质量过滤配置
DEFAULT_QUALITY_FILTER_ENABLED = False  # 是否启用帧质量过滤（过滤模糊、过暗、静态帧）
DEFAULT_QUALITY_BLUR_THRESHOLD = 100.0  # 拉普拉斯方差阈值（小于此值认为是模糊帧）
DEFAULT_QUALITY_DARK_THRESHOLD = 30.0   # 平均亮度下限（小于此值认为是过暗帧）
DEFAULT_QUALITY_BRIGHT_THRESHOLD = 225.0  # 平均亮度上限（大于此值认为是过曝帧）
DEFAULT_QUALITY_STATIC_THRESHOLD = 0.98  # 帧间相似度阈值（大于此值认为是静态帧，与上一帧几乎相同）

# 模糊视频清理（用于批量筛除「整段模糊」的低质量视频）
DEFAULT_BLUR_DELETE_ENABLED = False  # 是否开启「整段模糊检测」
DEFAULT_BLUR_DELETE_THRESHOLD = 100.0  # 全片平均拉普拉斯方差阈值；低于它判定为模糊（整段）
DEFAULT_BLUR_DELETE_SAMPLE = 10       # 抽多少帧估算整体清晰度（越多越准越慢）
DEFAULT_BLUR_DELETE_SKIP = True       # 判为模糊后是否跳过昂贵的逐帧/汇总分析（直接记录待删）
DEFAULT_BLUR_DELETE_LOG = 'logs/delete.log'    # 模糊视频记录文件
DEFAULT_BLUR_DELETE_SCRIPT = 'logs/delete.sh'  # 生成的 rm -rf 删除脚本

# 长视频分段配置
DEFAULT_SEGMENT_ENABLED = False   # 是否启用长视频分段处理
DEFAULT_SEGMENT_DURATION = 600.0  # 每段时长（秒），默认10分钟
DEFAULT_SEGMENT_OVERLAP = 30.0    # 段间重叠时长（秒），避免切分关键内容

# --------------------------------------------------------------------------- #
# P1-a) 任务能力位掩码（移植自 TVKPlayer 的 TVKDrmSupportType 位掩码范式）
# 用位掩码表达「本机/任务具备哪些高级能力」，能力查询前置 → 缺失即安全降级。
# 设计参见 docs/202606/tvkplayer_architecture_playbook.md（机制 A）。
# --------------------------------------------------------------------------- #
class VideoTaskCap:
    """任务/设备高级能力位掩码（每个能力占一位，可自由组合）。"""
    NONE = 0
    SCENE_ENHANCED = 0x1   # 增强版镜头检测（纯 Python，恒可用）
    DEDUP = 0x2            # 感知哈希去重（需 PIL + numpy）
    QUALITY_FILTER = 0x4   # 帧质量过滤（纯 Python，恒可用）
    OCR = 0x8              # 硬字幕 OCR（需 PaddleOCR）
    ASR = 0x10             # 音频转写（需 faster-whisper）
    NOTEBOOK = 0x20        # 笔记模式（纯 Python，恒可用）
    SEGMENT = 0x40         # 长视频分段（纯 Python，恒可用）
    GPU = 0x80             # GPU 加速（需可用 GPU）
    MLX = 0x100            # Apple Silicon MLX 后端（需 mlx_vlm/mlx_lm）
    CACHE = 0x200          # 文件级缓存（纯 Python，恒可用）


def _cap_to_labels(cap: int) -> str:
    """把能力位掩码转成可读标签串，便于日志。"""
    labels = []
    for bit, name in (
        (VideoTaskCap.DEDUP, '去重'), (VideoTaskCap.OCR, 'OCR'),
        (VideoTaskCap.ASR, '转写'), (VideoTaskCap.GPU, 'GPU'),
        (VideoTaskCap.MLX, 'MLX'),
    ):
        if cap & bit:
            labels.append(name)
    return '、'.join(labels) if labels else '基础（仅逐帧+汇总）'


def detect_device_capability() -> int:
    """探测本机实际可用的高级能力，返回 ``VideoTaskCap`` 位掩码。

    对应 TVKPlayer 的 ``GetDrmCapability()`` 前置查询：在调度层（main）调用一次，
    把结果传给每个任务做能力对齐（缺失即安全降级），而非跑到一半才因缺依赖崩溃。

    纯 Python 能力（质量过滤 / 镜头检测 / 笔记 / 分段 / 缓存）无外部依赖，
    不在本探测范围——它们恒可用，由任务配置决定开关。
    """
    cap = VideoTaskCap.NONE
    if _HAVE_PERCEPTUAL_HASH:
        cap |= VideoTaskCap.DEDUP
    if _HAVE_PADDLE_OCR:
        cap |= VideoTaskCap.OCR
    try:  # 仅确认依赖是否安装，不真正加载模型
        import faster_whisper  # type: ignore  # noqa: F401
        cap |= VideoTaskCap.ASR
    except Exception:
        pass
    try:
        has_gpu, _ = detect_gpu()
        if has_gpu:
            cap |= VideoTaskCap.GPU
    except Exception:
        pass
    if sys.platform == 'darwin':
        try:
            import mlx_vlm  # type: ignore  # noqa: F401
            import mlx_lm  # type: ignore  # noqa: F401
            cap |= VideoTaskCap.MLX
        except Exception:
            pass
    return cap


def _reconcile_capabilities(task: dict, defaults: dict, device_cap: int) -> None:
    """把任务「请求的」高级能力与「设备实际具备的」能力对齐（P1-a 安全降级）。

    若任务开启了某能力但设备探测不到对应依赖，则就地关闭该能力并打 WARN，
    避免 run_task 中途因缺依赖崩溃。纯 Python 能力不走此降级。
    """
    def _gate(key: str, cap_bit: int, label: str) -> None:
        if task.get(key, defaults.get(key, False)) and not (device_cap & cap_bit):
            _c('WARN', f'设备不支持 [{label}]，安全降级：本次关闭该能力')
            task[key] = False

    _gate('ocr_enabled', VideoTaskCap.OCR, '硬字幕 OCR (PaddleOCR)')
    _gate('dedup_enabled', VideoTaskCap.DEDUP, '内容去重 (感知哈希)')
    _gate('include_audio', VideoTaskCap.ASR, '音频转写 (faster-whisper)')
    _gate('gpu_enabled', VideoTaskCap.GPU, 'GPU 加速')

    # MLX 后端：要求 Apple Silicon 且已安装 mlx 库
    vb = str(task.get('backend') or defaults.get('backend') or 'ollama').lower()
    sb = str(task.get('summary_backend') or defaults.get('summary_backend') or vb).lower()
    if (vb == 'mlx' or sb == 'mlx') and not (device_cap & VideoTaskCap.MLX):
        _c('WARN', '请求 MLX 后端但设备不支持，安全降级：视觉/汇总回退到 ollama')
        if vb == 'mlx':
            task['backend'] = 'ollama'
        if sb == 'mlx':
            task['summary_backend'] = 'ollama'

# --------------------------------------------------------------------------- #
# 2.3) 长视频分段处理
# 将长视频切分为多个片段分别处理，避免内存溢出和提高处理速度
# --------------------------------------------------------------------------- #
def split_video_for_processing(path: str, segment_duration: float = DEFAULT_SEGMENT_DURATION,
                                segment_overlap: float = DEFAULT_SEGMENT_OVERLAP) -> list[tuple[float, float]]:
    """将视频分段，返回每段的时间范围列表。
    
    Args:
        path: 视频文件路径
        segment_duration: 每段时长（秒）
        segment_overlap: 段间重叠时长（秒）
    
    Returns:
        [(start_time, end_time), ...] 列表
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return [(0.0, 0.0)]
    
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (total_frames / fps) if fps > 0 else 0.0
    cap.release()
    
    if duration <= segment_duration:
        # 视频时长小于分段时长，无需分段
        return [(0.0, duration)]
    
    segments = []
    start = 0.0
    
    while start < duration:
        end = min(start + segment_duration, duration)
        segments.append((start, end))
        
        # 下一段的起始位置（考虑重叠）
        start = end - segment_overlap
        if start < 0:
            start = 0
    
    _c('INFO', f'长视频分段：{duration:.1f}秒 → {len(segments)}段（每段{segment_duration}秒，重叠{segment_overlap}秒）')
    return segments


def extract_frames_from_segment(path: str, start_time: float, end_time: float,
                                frame_interval: float, max_frames: int, 
                                sample_mode: str = 'auto',
                                scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
                                scene_probe: float = DEFAULT_SCENE_PROBE,
                                min_frames: int = 0,
                                save_images: bool = False,
                                image_dir: Optional[str] = None,
                                quality_config: Optional[dict] = None,
                                dedup_config: Optional[dict] = None,
                                scene_enhanced: bool = False) -> list[tuple[float, str]]:
    """从视频的指定时间段抽取帧。
    
    与extract_frames类似，但只处理指定时间范围。
    """
    global _FRAME_FILES
    
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError('无法打开视频用于抽帧')
    
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    duration = end_time - start_time
    
    if duration <= 0:
        cap.release()
        return []
    
    # 确定图片保存目录
    if save_images and not image_dir:
        base, _ = os.path.splitext(path)
        image_dir = f'{base}_frames'
    if save_images and image_dir:
        image_dir = os.path.expanduser(image_dir)
        os.makedirs(image_dir, exist_ok=True)
    
    # 根据sample_mode确定该段内的抽帧时间戳
    timestamps = []
    
    if sample_mode == 'scene':
        # 镜头检测模式：从start_time开始
        if scene_enhanced:
            # 注意：这里需要临时设置cap的位置
            cap.set(cv2.CAP_PROP_POS_MSEC, start_time * 1000.0)
            # 为简化，使用基础版镜头检测
            scene_timestamps = _detect_scene_timestamps(cap, duration, max(0.5, scene_probe), scene_threshold)
            timestamps = [start_time + ts for ts in scene_timestamps if ts <= duration]
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, start_time * 1000.0)
            scene_timestamps = _detect_scene_timestamps(cap, duration, max(0.5, scene_probe), scene_threshold)
            timestamps = [start_time + ts for ts in scene_timestamps if ts <= duration]
    elif sample_mode == 'count':
        # 固定张数模式
        limit = max(1, min(max_frames, MAX_FRAMES_HARD_CAP))
        step = duration / limit
        timestamps = [start_time + (i * step) for i in range(limit)]
    elif sample_mode == 'interval':
        # 时间间隔模式
        step = max(0.5, frame_interval)
        limit = int(duration // step) + 1
        if limit > MAX_FRAMES_HARD_CAP:
            limit = MAX_FRAMES_HARD_CAP
            step = duration / limit
        timestamps = [start_time + (i * step) for i in range(limit)]
    else:
        # auto模式
        limit = max(1, min(max_frames, MAX_FRAMES_HARD_CAP))
        step = max(frame_interval, duration / limit)
        timestamps = [start_time + (i * step) for i in range(limit)]
    
    # 最少帧数保底
    if min_frames and len(timestamps) < min_frames:
        target = min(int(min_frames), MAX_FRAMES_HARD_CAP)
        even_step = duration / target
        timestamps = [start_time + (i * even_step) for i in range(target)]
    
    # 抽取帧
    out = []
    for ts in timestamps:
        if ts > end_time:
            break
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
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
        
        b64_data = base64.b64encode(buf.tobytes()).decode('utf-8')
        out.append((round(ts, 2), b64_data))
        
        # 保存帧图片
        if save_images and image_dir:
            frame_idx = len(out) - 1
            img_filename = f'frame_{frame_idx:03d}_{ts:.1f}s.jpg'
            img_path = os.path.join(image_dir, img_filename)
            cv2.imwrite(img_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            _FRAME_FILES[round(ts, 2)] = (img_path, img_filename)
    
    cap.release()
    
    # 质量过滤
    if quality_config and quality_config.get('enabled', False):
        out = filter_frames_by_quality(out, quality_config)
    
    return out

# 全局帧文件映射（时间戳 -> (绝对路径, 文件名)），供 write_report 引用
_FRAME_FILES = {}  # type: dict[float, tuple[str, str]]


# --------------------------------------------------------------------------- #
# 0) 日志文件：把 _c() 的输出同步写入日志文件（便于事后核查每帧运行结果）
# --------------------------------------------------------------------------- #
_LOG_FILE = None          # 全局日志文件句柄（None=仅打印到终端）
_LOG_FILE_PATH = None

def set_log_file(path: Optional[str]) -> None:
    """打开日志文件（追加模式）。失败不影响终端输出。"""
    global _LOG_FILE, _LOG_FILE_PATH
    if not path:
        return
    try:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        _LOG_FILE_PATH = path
        _LOG_FILE = open(path, 'a', encoding='utf-8')
    except Exception as exc:
        _LOG_FILE = None
        print(f'[WARN] 无法打开日志文件 {path}：{exc}', file=sys.stderr)

def close_log_file() -> None:
    global _LOG_FILE
    if _LOG_FILE is not None:
        try:
            _LOG_FILE.flush()
            _LOG_FILE.close()
        except Exception:
            pass
        _LOG_FILE = None

def _log_to_file(tag: str, msg: str) -> None:
    """把一行日志（带时间戳、去色）写入日志文件。"""
    if _LOG_FILE is None:
        return
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        _LOG_FILE.write(f'[{ts}] [{tag}] {msg}\n')
        _LOG_FILE.flush()
    except Exception:
        pass

def _log_frame_detail(video_path: str, idx: int, ts: float, desc: str) -> None:
    """把单帧的识别结果以结构化块的形式写入日志文件（按帧汇总便于核查）。"""
    if _LOG_FILE is None:
        return
    ts_hms = time.strftime('%H:%M:%S', time.gmtime(ts))
    try:
        _LOG_FILE.write('\n' + '=' * 64 + '\n')
        _LOG_FILE.write(f'[帧结果] 视频={os.path.basename(video_path)} '
                        f'帧序={idx + 1} 时间={ts_hms} ({ts:.1f}s)\n')
        _LOG_FILE.write('-' * 64 + '\n')
        _LOG_FILE.write((desc or '') + '\n')
        _LOG_FILE.flush()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 0.1) 模糊视频清理：检测整段模糊的视频并记录到 delete.log、生成删除脚本
# --------------------------------------------------------------------------- #
DELETE_LOG_PATH = None       # 模糊视频记录文件路径（main 中按配置设置）
DELETE_SCRIPT_PATH = None    # 生成的 rm -rf 删除脚本路径（main 中按配置设置）
_BLUR_RECORDED: set = set()  # 本次运行已记录的模糊视频（去重，内存兜底）


def assess_video_blur(video_path: str, sample_count: int = DEFAULT_BLUR_DELETE_SAMPLE) -> Optional[float]:
    """估算整段视频的清晰度：均匀抽 ``sample_count`` 帧，返回平均拉普拉斯方差。

    返回值越小越模糊（沿用逐帧模糊判定的同一量纲，阈值与 ``quality_blur_threshold`` 一致）。
    - 需要 numpy；缺失或无法解码时返回 ``None``（视为「无法判定」）。
    - 仅作抽样粗估，开销远低于逐帧识别。
    """
    if not _HAVE_NUMPY:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            return None
        sample_count = max(1, min(int(sample_count), total))
        # 均匀铺满全片（含首末帧）的帧索引
        idxs = sorted({int(round(i * (total - 1) / max(1, sample_count - 1))) for i in range(sample_count)})
        variances = []
        for fi in idxs:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                variances.append(cv2.Laplacian(gray, cv2.CV_64F).var())
            except Exception:
                pass
        if not variances:
            return None
        return sum(variances) / len(variances)
    finally:
        cap.release()


def record_blurry_video(video_path: str, avg_variance: float) -> None:
    """把判为模糊的视频记录到 ``delete.log``，并在内存中留底（用于生成删除脚本）。"""
    global _BLUR_RECORDED
    abspath = os.path.abspath(os.path.expanduser(video_path))
    _BLUR_RECORDED.add(abspath)
    if DELETE_LOG_PATH:
        try:
            d = os.path.dirname(os.path.abspath(DELETE_LOG_PATH))
            os.makedirs(d, exist_ok=True)
            with open(DELETE_LOG_PATH, 'a', encoding='utf-8') as f:
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f'{ts} | BLUR | avg_laplacian={avg_variance:.2f} | {abspath}\n')
                f.flush()
        except Exception as exc:
            _c('WARN', f'写入 delete.log 失败：{exc}')
    _c('WARN', f'视频判定为模糊（平均拉普拉斯方差={avg_variance:.2f}），已记录：{abspath}')


def generate_delete_script() -> None:
    """根据 ``delete.log`` 中记录的全部模糊视频，生成 ``rm -rf`` 删除脚本（去重）。"""
    if not DELETE_SCRIPT_PATH:
        return
    paths: list[str] = []
    seen: set[str] = set()
    # 优先从 delete.log 解析（含历史运行积累），再补本次内存记录
    if DELETE_LOG_PATH and os.path.isfile(DELETE_LOG_PATH):
        try:
            with open(DELETE_LOG_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or ' | BLUR | ' not in line:
                        continue
                    p = line.split(' | ')[-1].strip()
                    if p and p not in seen:
                        seen.add(p)
                        paths.append(p)
        except Exception:
            pass
    for p in sorted(_BLUR_RECORDED):
        if p not in seen:
            seen.add(p)
            paths.append(p)
    try:
        d = os.path.dirname(os.path.abspath(DELETE_SCRIPT_PATH))
        os.makedirs(d, exist_ok=True)
        # 纯 rm -rf 行，可直接执行（无头部/注释）
        with open(DELETE_SCRIPT_PATH, 'w', encoding='utf-8') as f:
            for p in paths:
                f.write(f'rm -rf "{p}"\n')
        _c('OK', f'已生成删除脚本（{len(paths)} 个文件）：{DELETE_SCRIPT_PATH}')
    except Exception as exc:
        _c('WARN', f'生成删除脚本失败：{exc}')


def _c(tag: str, msg: str) -> None:
    colors = {'INFO': '34', 'OK': '32', 'WARN': '33', 'ERR': '31'}
    print(f'\033[1;{colors.get(tag, "0")}m[{tag:>4}]\033[0m {msg}', flush=True)
    _log_to_file(tag, msg)


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
# 2.1) 帧质量过滤（P0改进：过滤模糊、过暗、静态帧）
# --------------------------------------------------------------------------- #
def _is_blurry(frame: 'np.ndarray', threshold: float = DEFAULT_QUALITY_BLUR_THRESHOLD) -> bool:
    """判断帧是否模糊（拉普拉斯方差法）。
    
    拉普拉斯方差越小，图像越模糊。通常<100认为是模糊的。
    """
    if not _HAVE_NUMPY:
        return False
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        variance = cv2.Laplacian(gray, cv2.CV_64F).var()
        return variance < threshold
    except Exception:
        return False


def _is_bad_lighting(frame: 'np.ndarray', 
                     dark_threshold: float = DEFAULT_QUALITY_DARK_THRESHOLD,
                     bright_threshold: float = DEFAULT_QUALITY_BRIGHT_THRESHOLD) -> bool:
    """判断帧是否过暗或过曝。
    
    计算平均亮度，超出阈值范围则过滤。
    """
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        avg_brightness = gray.mean()
        return avg_brightness < dark_threshold or avg_brightness > bright_threshold
    except Exception:
        return False


def _is_static_frame(frame: 'np.ndarray', prev_frame: 'np.ndarray', 
                     threshold: float = DEFAULT_QUALITY_STATIC_THRESHOLD) -> bool:
    """判断帧是否与上一帧几乎相同（静态帧）。
    
    计算两帧的结构相似度（SSIM简化版：用MSE替代），
    相似度高于阈值则认为是静态帧。
    """
    if prev_frame is None:
        return False
    try:
        # 转灰度后计算均方误差（MSE）
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        
        # 缩放以提高速度
        h, w = gray.shape
        if h > 160 or w > 160:
            gray_small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
            prev_small = cv2.resize(prev_gray, (160, 90), interpolation=cv2.INTER_AREA)
        else:
            gray_small = gray
            prev_small = prev_gray
        
        # 计算结构相似度（简化版：用相关系数替代SSIM）
        gray_small = gray_small.astype(np.float32)
        prev_small = prev_small.astype(np.float32)
        corr = cv2.matchTemplate(gray_small, prev_small, cv2.TM_CCOEFF_NORMED)[0][0]
        # TM_CCOEFF_NORMED 返回值在[-1,1]，需要转换
        # 改用直接计算相似度
        diff = np.abs(gray_small - prev_small)
        similarity = 1.0 - (diff.mean() / 255.0)
        return similarity > threshold
    except Exception:
        return False


def filter_frames_by_quality(frames: list[tuple[float, str]], 
                             quality_config: dict) -> list[tuple[float, str]]:
    """根据质量配置过滤帧。
    
    Args:
        frames: (时间戳, base64) 列表
        quality_config: 质量过滤配置字典
            - enabled: 是否启用
            - blur_threshold: 模糊阈值
            - dark_threshold: 过暗阈值
            - bright_threshold: 过曝阈值
            - static_threshold: 静态帧阈值
    
    Returns:
        过滤后的帧列表
    """
    if not quality_config.get('enabled', False) or not frames:
        return frames
    
    _c('INFO', f'帧质量过滤：对 {len(frames)} 帧进行质量检查...')
    
    blur_threshold = quality_config.get('blur_threshold', DEFAULT_QUALITY_BLUR_THRESHOLD)
    dark_threshold = quality_config.get('dark_threshold', DEFAULT_QUALITY_DARK_THRESHOLD)
    bright_threshold = quality_config.get('bright_threshold', DEFAULT_QUALITY_BRIGHT_THRESHOLD)
    static_threshold = quality_config.get('static_threshold', DEFAULT_QUALITY_STATIC_THRESHOLD)
    
    filtered = []
    prev_frame = None
    filtered_count = 0
    
    for i, (ts, b64) in enumerate(frames):
        # 解码base64为numpy数组用于质量检查
        try:
            img_data = base64.b64decode(b64)
            img_array = np.frombuffer(img_data, dtype=np.uint8)
            frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            
            if frame is None:
                filtered.append((ts, b64))
                continue
            
            # 检查模糊
            if _is_blurry(frame, blur_threshold):
                _c('INFO', f'  帧 {i+1} @ {ts:.1f}s 被过滤（模糊）')
                filtered_count += 1
                prev_frame = frame
                continue
            
            # 检查亮度
            if _is_bad_lighting(frame, dark_threshold, bright_threshold):
                _c('INFO', f'  帧 {i+1} @ {ts:.1f}s 被过滤（过暗/过曝）')
                filtered_count += 1
                prev_frame = frame
                continue
            
            # 检查静态帧
            if _is_static_frame(frame, prev_frame, static_threshold):
                _c('INFO', f'  帧 {i+1} @ {ts:.1f}s 被过滤（静态帧）')
                filtered_count += 1
                prev_frame = frame
                continue
            
            # 通过所有检查
            filtered.append((ts, b64))
            prev_frame = frame
        except Exception as exc:
            _c('WARN', f'  帧 {i+1} @ {ts:.1f}s 质量检查失败：{exc}，保留该帧')
            filtered.append((ts, b64))
    
    _c('OK', f'质量过滤完成：{len(frames)} 帧 → {len(filtered)} 帧（过滤 {filtered_count} 个低质量帧）')
    return filtered


# --------------------------------------------------------------------------- #
# 2.2) 增强 scene 模式：综合多维度镜头检测
# --------------------------------------------------------------------------- #
def _detect_scene_timestamps_enhanced(cap, duration: float, probe: float, 
                                       threshold: float = DEFAULT_SCENE_THRESHOLD) -> list[float]:
    """增强版镜头切换检测：综合 HSV 相关度 + 帧间差值 + 边缘变化。
    
    相比基础版（只用HSV相关度），增加：
    1. 帧间绝对差值（突然变化说明镜头切换）
    2. 边缘密度变化（场景切换通常伴随边缘剧变）
    
    Args:
        cap: OpenCV VideoCapture 对象
        duration: 视频时长（秒）
        probe: 探测间隔（秒）
        threshold: HSV相关度阈值
    
    Returns:
        判定为镜头切换的时间戳列表
    """
    stamps: list[float] = []
    prev_hist = None
    prev_frame = None
    prev_edge_density = None
    t = 0.0
    
    while t < duration and len(stamps) < MAX_FRAMES_HARD_CAP:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            t += probe
            continue
        
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        
        # 计算边缘密度（Canny边缘检测）
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / (edges.shape[0] * edges.shape[1])
        
        is_scene_cut = False
        reason = ''
        
        if prev_hist is None:
            is_scene_cut = True
            reason = '首帧'
        else:
            # 1. HSV相关度检测
            corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
            if corr < threshold:
                is_scene_cut = True
                reason = f'HSV相关度低({corr:.3f})'
            
            # 2. 帧间差值检测（突然变化）
            if prev_frame is not None and not is_scene_cut:
                diff = np.abs(small.astype(np.float32) - prev_frame.astype(np.float32))
                mean_diff = diff.mean()
                if mean_diff > 50:  # 平均差值超过50（0-255范围）
                    is_scene_cut = True
                    reason = f'帧间差值大({mean_diff:.1f})'
            
            # 3. 边缘密度变化检测
            if prev_edge_density is not None and not is_scene_cut:
                edge_change = abs(edge_density - prev_edge_density)
                if edge_change > 0.1:  # 边缘密度变化超过10%
                    is_scene_cut = True
                    reason = f'边缘密度变化({edge_change:.3f})'
        
        if is_scene_cut:
            stamps.append(round(t, 2))
            if len(reason) > 0:
                _c('INFO', f'  镜头切换 @ {t:.1f}s ({reason})')
        
        prev_hist = hist
        prev_frame = small.copy()
        prev_edge_density = edge_density
        t += probe
    
    return stamps


# --------------------------------------------------------------------------- #
# 2) 抽帧（OpenCV）
# sample_mode:
#   'count'    固定张数：全片均匀抽 max_frames 帧（无视 frame_interval）
#   'interval' 时间跨度：每 frame_interval 秒抽 1 帧（长视频更完整，受硬上限保护）
#   'scene'    镜头切换：每 scene_probe 秒探测一次，HSV 直方图相关度骤降即判为新镜头并抽帧
#   'auto'     兼容旧行为：≤ max_frames 帧，且不密于 frame_interval
# --------------------------------------------------------------------------- #
def _detect_scene_timestamps(cap, duration: float, probe: float, threshold: float) -> list[float]:
    """粗粒度探测镜头切换：返回判定为「新镜头」的时间戳列表。

    做法：每 ``probe`` 秒取一帧，缩成小图算 HSV 色调-饱和度直方图，与上一帧做相关度比较；
    相关度低于 ``threshold`` 说明画面构成发生明显变化（镜头切换/场景转换），记为关键帧。
    纯 OpenCV，不引入额外依赖。
    """
    stamps: list[float] = []
    prev = None
    t = 0.0
    while t < duration and len(stamps) < MAX_FRAMES_HARD_CAP:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if ok and frame is not None:
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
            if prev is None:
                stamps.append(round(t, 2))  # 首帧必收
            else:
                corr = cv2.compareHist(prev, hist, cv2.HISTCMP_CORREL)
                if corr < threshold:
                    stamps.append(round(t, 2))  # 镜头切换
            prev = hist
        t += probe
    return stamps


def extract_frames(
    path: str, frame_interval: float, max_frames: int, sample_mode: str = 'auto',
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD, scene_probe: float = DEFAULT_SCENE_PROBE,
    min_frames: int = 0,
    save_images: bool = False,
    image_dir: Optional[str] = None,
    quality_config: Optional[dict] = None,
    dedup_config: Optional[dict] = None,
    scene_enhanced: bool = False,
) -> list[tuple[float, str]]:
    """抽取视频帧，可选保存图片文件用于生成带截图的笔记报告。

    Args:
        save_images: 是否将帧保存为 JPG 文件
        image_dir: 图片保存目录（默认为视频同目录下的 _frames 子目录）
        quality_config: 帧质量过滤配置（None=不启用）
        scene_enhanced: 是否使用增强版镜头检测（综合多维度）
    Returns:
        (时间戳, base64) 列表；若 save_images=True，额外设置全局 _FRAME_FILES 映射
    """
    global _FRAME_FILES
    _FRAME_FILES = {}

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError('无法打开视频用于抽帧')

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (total_frames / fps) if fps > 0 else 0.0

    # 确定图片保存目录
    if save_images and not image_dir:
        base, _ = os.path.splitext(path)
        image_dir = f'{base}_frames'
    if save_images and image_dir:
        image_dir = os.path.expanduser(image_dir)
        os.makedirs(image_dir, exist_ok=True)

    # 根据是否增强版选择不同的镜头检测函数
    if duration > 0 and sample_mode == 'scene':
        if scene_enhanced:
            _c('INFO', f'使用增强版镜头检测（综合HSV+帧间差值+边缘变化）...')
            timestamps = _detect_scene_timestamps_enhanced(cap, duration, max(0.5, scene_probe), scene_threshold)
        else:
            timestamps = _detect_scene_timestamps(cap, duration, max(0.5, scene_probe), scene_threshold)
        if not timestamps:
            timestamps = [0.0]
    elif duration > 0:
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

    # —— 最少帧数保底：若按所选策略抽到的帧数不足 min_frames，则在全片均匀补到 min_frames ——
    # 解决「短视频 + 大 interval」只抽 1 帧、信息不足的问题。受总帧数与硬上限约束。
    if min_frames and duration > 0 and len(timestamps) < min_frames:
        target = min(int(min_frames), MAX_FRAMES_HARD_CAP)
        if total_frames > 0:
            target = min(target, total_frames)
        if target > len(timestamps):
            even_step = duration / target
            timestamps = [round(i * even_step, 2) for i in range(target)]

    out: list[tuple[float, str]] = []
    out_images: list = []  # 存储原始图像用于去重
    
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
        b64_data = base64.b64encode(buf.tobytes()).decode('utf-8')
        out.append((round(ts, 2), b64_data))
        out_images.append(frame.copy())  # 保存原始图像用于去重

        # 保存帧图片到文件（用于笔记模式嵌入截图）
        if save_images and image_dir:
            frame_idx = len(out) - 1
            img_filename = f'frame_{frame_idx:03d}_{ts:.1f}s.jpg'
            img_path = os.path.join(image_dir, img_filename)
            cv2.imwrite(img_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            _FRAME_FILES[round(ts, 2)] = (img_path, img_filename)
    
    cap.release()
    
    # 内容去重（如果启用）
    if dedup_config and dedup_config.get('enabled', False) and _HAVE_PERCEPTUAL_HASH and out:
        dedup_threshold = dedup_config.get('threshold', DEFAULT_DEDUP_THRESHOLD)
        # 需要重新读取帧图像用于去重计算
        out_images_for_dedup = []
        for ts, b64 in out:
            try:
                img_data = base64.b64decode(b64)
                img_array = np.frombuffer(img_data, dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                if frame is not None:
                    out_images_for_dedup.append(frame)
                else:
                    out_images_for_dedup.append(None)
            except Exception:
                out_images_for_dedup.append(None)
        
        out, out_images_for_dedup = deduplicate_frames(out, out_images_for_dedup, dedup_threshold)
    
    # 帧质量过滤（如果启用）
    if quality_config and quality_config.get('enabled', False):
        out = filter_frames_by_quality(out, quality_config)
    
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
# 3.1) 并发处理（加速逐帧分析）
# --------------------------------------------------------------------------- #
def process_frames_concurrently(
    frames: list[tuple[float, str]],
    backend: str,
    ollama_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    concurrency: int = 3,
    stream_log: bool = True,
) -> list[tuple[int, float, str]]:
    """并发处理多个帧的识别任务。
    
    使用线程池并发调用视觉模型，加速逐帧分析。
    注意：并发数受限于Ollama的并发处理能力，建议不超过3-5。
    
    Args:
        frames: 帧列表（索引, 时间戳, base64图像）
        backend: 后端类型（ollama/mlx）
        ollama_url: Ollama服务地址
        model: 视觉模型
        prompt: 逐帧分析提示词
        max_tokens: 最大生成token数
        concurrency: 并发数
        stream_log: 是否实时打印结果
    
    Returns:
        帧分析结果列表（索引, 时间戳, 描述）
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    _c('INFO', f'并发分析 {len(frames)} 帧（并发数={concurrency}）...')
    
    def _process_single_frame(i, ts, b64):
        """处理单帧"""
        try:
            if stream_log:
                # 注意：并发模式下实时打印会交错，这里简化为完成后打印
                desc = chat(backend, ollama_url, model, prompt, images=[b64],
                           max_tokens=max_tokens).strip()
            else:
                desc = chat(backend, ollama_url, model, prompt, images=[b64],
                           max_tokens=max_tokens).strip()
            return (i, ts, desc)
        except Exception as exc:
            return (i, ts, f'(帧识别失败：{exc})')
    
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_process_single_frame, i, ts, b64): (i, ts, b64) 
                   for i, (ts, b64) in enumerate(frames)}
        
        for future in as_completed(futures):
            i, ts, desc = future.result()
            results.append((i, ts, desc))
            if stream_log:
                _c('INFO', f'  帧 {i+1}/{len(frames)} @ {ts:.1f}s 完成')
    
    # 按索引排序
    results.sort(key=lambda x: x[0])
    return [(i, ts, desc) for i, ts, desc in results]


# --------------------------------------------------------------------------- #
# 3.2) MLX 后端（Apple Silicon 原生）
# 视觉用 mlx-vlm、文本汇总用 mlx-lm；相比 Ollama(GGUF/llama.cpp) 在苹果芯片上
# 利用统一内存零拷贝 + Metal，通常更快、占用更低。
#   安装： pip install -U mlx mlx-vlm mlx-lm
#   视觉模型示例： mlx-community/Qwen2.5-VL-7B-Instruct-4bit
#   文本模型示例： mlx-community/Qwen2.5-7B-Instruct-4bit
# 完全离线：模型首次自动从 HuggingFace 下载到本地缓存，之后断网可用。
# --------------------------------------------------------------------------- #
_MLX_VLM_CACHE: dict = {}   # model_id -> (model, processor, config)
_MLX_LM_CACHE: dict = {}    # model_id -> (model, tokenizer)


def _b64_to_pil(b64: str):
    """base64(JPEG) → PIL.Image（RGB），供 mlx-vlm 直接消费。"""
    import io
    from PIL import Image
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert('RGB')


def _load_mlx_vlm(model_id: str):
    cached = _MLX_VLM_CACHE.get(model_id)
    if cached is not None:
        return cached
    from mlx_vlm import load
    _c('INFO', f'加载 MLX 视觉模型「{model_id}」(首次会下载, 之后走本地缓存)…')
    model, processor = load(model_id)
    try:
        config = model.config
    except Exception:
        from mlx_vlm.utils import load_config
        config = load_config(model_id)
    _MLX_VLM_CACHE[model_id] = (model, processor, config)
    return _MLX_VLM_CACHE[model_id]


def _vlm_call(fn, model, processor, prompt, images, kwargs):
    """兼容 mlx-vlm 不同版本的图像入参：image= / images= / 位置参数。"""
    try:
        return fn(model, processor, prompt, image=images, **kwargs)
    except TypeError:
        pass
    try:
        return fn(model, processor, prompt, images=images, **kwargs)
    except TypeError:
        return fn(model, processor, prompt, images, **kwargs)


def mlx_vlm_chat(model_id, content, images, *, stream=False, on_delta=None,
                 max_tokens: int = DEFAULT_FRAME_MAX_TOKENS) -> str:
    """用 mlx-vlm 对一帧/多帧图像做视觉理解，签名对齐 ollama_chat。"""
    model, processor, config = _load_mlx_vlm(model_id)
    from mlx_vlm.prompt_utils import apply_chat_template
    pil_images = [_b64_to_pil(b) for b in (images or [])]
    try:
        prompt = apply_chat_template(processor, config, content, num_images=len(pil_images))
    except TypeError:
        prompt = apply_chat_template(processor, config, content, len(pil_images))

    kwargs = {'max_tokens': max_tokens, 'verbose': False}
    if stream:
        try:
            from mlx_vlm import stream_generate
            parts: list[str] = []
            for chunk in _vlm_call(stream_generate, model, processor, prompt, pil_images, kwargs):
                t = getattr(chunk, 'text', None)
                if t is None:
                    t = chunk if isinstance(chunk, str) else ''
                if t:
                    parts.append(t)
                    if on_delta:
                        on_delta(t)
            return ''.join(parts)
        except Exception:
            pass  # 不支持流式则回退非流式
    from mlx_vlm import generate
    out = _vlm_call(generate, model, processor, prompt, pil_images, kwargs)
    text = getattr(out, 'text', None)
    if text is None:
        text = out if isinstance(out, str) else str(out)
    if stream and on_delta and text:
        on_delta(text)
    return text


def _load_mlx_lm(model_id: str):
    cached = _MLX_LM_CACHE.get(model_id)
    if cached is not None:
        return cached
    from mlx_lm import load
    _c('INFO', f'加载 MLX 文本模型「{model_id}」(首次会下载)…')
    model, tokenizer = load(model_id)
    _MLX_LM_CACHE[model_id] = (model, tokenizer)
    return _MLX_LM_CACHE[model_id]


def mlx_lm_chat(model_id, content, *, stream=False, on_delta=None,
                max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS) -> str:
    """用 mlx-lm 做纯文本生成（汇总），签名对齐 ollama_chat。"""
    model, tokenizer = _load_mlx_lm(model_id)
    messages = [{'role': 'user', 'content': content}]
    try:
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
    except Exception:
        prompt = content

    if stream:
        try:
            from mlx_lm import stream_generate
            parts: list[str] = []
            for r in stream_generate(model, tokenizer, prompt, max_tokens=max_tokens):
                t = getattr(r, 'text', None)
                if t is None:
                    t = r if isinstance(r, str) else ''
                if t:
                    parts.append(t)
                    if on_delta:
                        on_delta(t)
            return ''.join(parts)
        except Exception:
            pass
    from mlx_lm import generate
    out = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)
    return out if isinstance(out, str) else getattr(out, 'text', str(out))


# --------------------------------------------------------------------------- #
# 3.3) 统一调度：按 backend 选择 ollama / mlx，对外签名一致
# --------------------------------------------------------------------------- #
def chat(backend, ollama_url, model, content, images=None, *,
         stream=False, on_delta=None, max_tokens=None) -> str:
    if (backend or 'ollama').lower() == 'mlx':
        if images:
            return mlx_vlm_chat(model, content, images, stream=stream, on_delta=on_delta,
                                max_tokens=max_tokens or DEFAULT_FRAME_MAX_TOKENS)
        return mlx_lm_chat(model, content, stream=stream, on_delta=on_delta,
                           max_tokens=max_tokens or DEFAULT_SUMMARY_MAX_TOKENS)
    return ollama_chat(ollama_url, model, content, images=images, stream=stream, on_delta=on_delta)


# --------------------------------------------------------------------------- #
# 3.5) 音频转写（faster-whisper，可选）
# faster-whisper 内部用 PyAV 直接从视频解码音轨，无需系统安装 ffmpeg。
# --------------------------------------------------------------------------- #
_WHISPER_CACHE: dict = {}  # 同一进程内复用已加载的模型，避免重复初始化
_WHISPER_LOCK = threading.Lock()  # 防止并发任务同时初始化同一模型

# 异步 ASR：全进程共享的单工位执行器。
# 单工位原因：whisper 本身已多线程吃满 CPU，多个转写同时跑只会互相争抢变慢；
# 排队串行 + 与逐帧识别/OCR 重叠，才是最优组合。
_ASR_EXECUTOR = None
_ASR_EXECUTOR_LOCK = threading.Lock()


def submit_transcribe_async(video_path: str, whisper_model: str,
                            language: Optional[str] = None):
    """把音频转写提交到后台执行器，立即返回 Future（不阻塞主流程）。

    Future.result() 返回 ``(full_text, segments)``，与 transcribe_audio 一致。
    """
    global _ASR_EXECUTOR
    from concurrent.futures import ThreadPoolExecutor
    with _ASR_EXECUTOR_LOCK:
        if _ASR_EXECUTOR is None:
            _ASR_EXECUTOR = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix='asr')
    return _ASR_EXECUTOR.submit(transcribe_audio, video_path, whisper_model, language)


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

    with _WHISPER_LOCK:
        model = _WHISPER_CACHE.get(whisper_model)
        if model is None:
            _c('INFO', f'加载 Whisper 模型「{whisper_model}」(首次会下载/初始化, CPU int8)…')
            try:
                # cpu_threads 默认只有 4，这里用满物理核心数以提升 CPU 利用率
                model = WhisperModel(whisper_model, device='cpu', compute_type='int8',
                                     cpu_threads=max(4, os.cpu_count() or 4))
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
# 4) 内容去重（感知哈希）
# 使用感知哈希算法识别相似帧，避免重复分析几乎相同的帧
# --------------------------------------------------------------------------- #
def _perceptual_hash(image: 'np.ndarray', hash_size: int = 8) -> str:
    """计算图像的感知哈希值。
    
    将图像缩放到 hash_size x hash_size，转为灰度图，计算DCT，
    取左上角低频部分生成哈希值。相似图像会有相似的哈希值。
    """
    if not _HAVE_PIL or not _HAVE_NUMPY:
        return ''
    
    try:
        # 转为PIL Image
        if len(image.shape) == 3:
            img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        else:
            img = Image.fromarray(image)
        
        # 缩放到hash_size x hash_size
        img = img.resize((hash_size, hash_size), Image.Resampling.LANCZOS)
        
        # 转为灰度图
        img = img.convert('L')
        
        # 计算DCT
        pixels = np.array(img.getdata()).reshape((hash_size, hash_size))
        dct = np.zeros((hash_size, hash_size))
        
        # 简化的DCT计算（取平均亮度作为阈值）
        avg = pixels.mean()
        
        # 生成哈希值
        diff = pixels > avg
        hash_str = ''.join(['1' if d else '0' for d in diff.flatten()])
        
        return hash_str
    except Exception as exc:
        _c('WARN', f'感知哈希计算失败：{exc}')
        return ''


# --------------------------------------------------------------------------- #
# 4.1) 缓存机制
# 缓存帧分析结果和OCR结果，避免重复分析相同内容
# --------------------------------------------------------------------------- #
def _get_cache_key(video_path: str, frame_timestamp: float, model: str, prompt: str) -> str:
    """生成缓存键。
    
    基于视频文件路径、帧时间戳、模型和提示词生成唯一的缓存键。
    """
    content = f'{video_path}|{frame_timestamp}|{model}|{prompt}'
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def _get_video_cache_key(video_path: str) -> str:
    """生成视频级别的缓存键（基于文件内容）。"""
    try:
        # 使用文件大小和修改时间生成缓存键（简单快速）
        stat = os.stat(video_path)
        content = f'{video_path}|{stat.st_size}|{stat.st_mtime}'
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    except Exception:
        # 如果无法获取文件信息，使用文件路径
        return hashlib.md5(video_path.encode('utf-8')).hexdigest()


def _load_cache(cache_dir: str, cache_key: str) -> Optional[str]:
    """从缓存加载结果。"""
    if not DEFAULT_CACHE_ENABLED:
        return None
    
    cache_dir = os.path.expanduser(cache_dir)
    cache_file = os.path.join(cache_dir, f'{cache_key}.json')
    
    if not os.path.isfile(cache_file):
        return None
    
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('result')
    except Exception:
        return None


def _save_cache(cache_dir: str, cache_key: str, result: str) -> None:
    """保存结果到缓存。"""
    if not DEFAULT_CACHE_ENABLED:
        return
    
    cache_dir = os.path.expanduser(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, f'{cache_key}.json')
    
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump({'result': result, 'timestamp': time.time()}, f, ensure_ascii=False)
    except Exception as exc:
        _c('WARN', f'保存缓存失败：{exc}')


# --------------------------------------------------------------------------- #
# 4.2) GPU加速支持
# 检测GPU可用性，自动启用GPU加速
# --------------------------------------------------------------------------- #
def detect_gpu() -> tuple[bool, str]:
    """检测GPU可用性。
    
    Returns:
        (has_gpu, gpu_type): 是否有GPU，GPU类型（nvidia/amd/apple/mps）
    """
    # 检测NVIDIA GPU
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            _c('INFO', f'检测到NVIDIA GPU: {gpu_name}')
            return True, 'nvidia'
    except ImportError:
        pass
    
    # 检测Apple Silicon (MPS)
    try:
        import torch
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            _c('INFO', '检测到Apple Silicon GPU (MPS)')
            return True, 'apple'
    except ImportError:
        pass
    
    # 检测AMD GPU (ROCm)
    try:
        import torch
        if hasattr(torch.version, 'hip') and torch.version.hip:
            _c('INFO', '检测到AMD GPU (ROCm)')
            return True, 'amd'
    except ImportError:
        pass
    
    # 使用OpenCV检测GPU
    try:
        if cv2.cuda.getCudaEnabledDeviceCount() > 0:
            _c('INFO', f'检测到CUDA设备（OpenCV）：{cv2.cuda.getCudaEnabledDeviceCount()} 个')
            return True, 'nvidia'
    except Exception:
        pass
    
    _c('INFO', '未检测到GPU，使用CPU模式')
    return False, ''


def auto_configure_gpu(task: dict, defaults: dict) -> bool:
    """自动配置GPU加速。
    
    根据GPU检测结果和配置，自动决定是否使用GPU。
    
    Returns:
        是否启用GPU加速
    """
    gpu_enabled = bool(task.get('gpu_enabled', defaults.get('gpu_enabled', DEFAULT_GPU_ENABLED)))
    
    if not gpu_enabled:
        return False
    
    has_gpu, gpu_type = detect_gpu()
    
    if not has_gpu:
        _c('WARN', '配置启用了GPU加速，但未检测到可用GPU，将使用CPU')
        return False
    
    _c('OK', f'GPU加速已启用（类型：{gpu_type}）')
    return True


def _hamming_distance(hash1: str, hash2: str) -> int:
    """计算两个感知哈希值之间的汉明距离。"""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999  # 返回大值表示不匹配
    
    # 将二进制字符串转为整数
    int1 = int(hash1, 2)
    int2 = int(hash2, 2)
    
    # 计算异或后的1的个数（汉明距离）
    xor = int1 ^ int2
    return bin(xor).count('1')


def deduplicate_frames(frames: list[tuple[float, str]], 
                       images: list, 
                       threshold: int = DEFAULT_DEDUP_THRESHOLD) -> tuple[list[tuple[float, str]], list]:
    """对抽出的帧进行去重，返回去重后的帧列表和对应的图像数据。
    
    Args:
        frames: 时间戳和base64编码的帧列表
        images: 对应的图像数据列表（numpy数组）
        threshold: 感知哈希汉明距离阈值，小于此值认为是相似帧
    
    Returns:
        去重后的frames和images
    """
    if not _HAVE_PERCEPTUAL_HASH or not frames:
        return frames, images
    
    _c('INFO', f'内容去重：对 {len(frames)} 帧进行去重（阈值={threshold}）...')
    
    unique_frames = []
    unique_images = []
    hashes = []
    
    for i, ((ts, b64), img) in enumerate(zip(frames, images)):
        # 计算感知哈希
        hash_val = _perceptual_hash(img)
        
        if not hash_val:
            # 无法计算哈希，保留该帧
            unique_frames.append((ts, b64))
            unique_images.append(img)
            hashes.append(hash_val)
            continue
        
        # 与已有哈希比较
        is_duplicate = False
        for existing_hash in hashes:
            if not existing_hash:
                continue
            distance = _hamming_distance(hash_val, existing_hash)
            if distance < threshold:
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique_frames.append((ts, b64))
            unique_images.append(img)
            hashes.append(hash_val)
        else:
            _c('INFO', f'  帧 {i+1} @ {ts:.1f}s 被去重（与已有帧相似）')
    
    _c('OK', f'去重完成：{len(frames)} 帧 → {len(unique_frames)} 帧（去除 {len(frames) - len(unique_frames)} 个重复帧）')
    
    return unique_frames, unique_images


# --------------------------------------------------------------------------- #
# 5) 硬字幕OCR（PaddleOCR）
# 识别视频帧中的文字（硬字幕、标题、标语等）
# --------------------------------------------------------------------------- #
_OCR_CACHE: dict = {}  # OCR模型缓存

def init_ocr(ocr_lang: str = DEFAULT_OCR_LANG, use_gpu: bool = False):
    """初始化PaddleOCR模型。"""
    if not _HAVE_PADDLE_OCR:
        _c('WARN', '未安装 PaddleOCR，跳过硬字幕识别')
        return None
    
    cache_key = f'{ocr_lang}_{use_gpu}'
    if cache_key in _OCR_CACHE:
        return _OCR_CACHE[cache_key]
    
    try:
        _c('INFO', f'加载 PaddleOCR 模型（语言={ocr_lang}, GPU={use_gpu}）...')
        ocr = PaddleOCR(
            use_angle_cls=True,
            lang=ocr_lang,
            use_gpu=use_gpu,
            show_log=False
        )
        _OCR_CACHE[cache_key] = ocr
        _c('OK', 'PaddleOCR 模型加载成功')
        return ocr
    except Exception as exc:
        _c('WARN', f'PaddleOCR 模型加载失败：{exc}')
        return None


def recognize_text_in_frame(ocr, image: 'np.ndarray') -> list[tuple[float, float, float, float, str, float]]:
    """识别单帧中的文字。
    
    Args:
        ocr: PaddleOCR实例
        image: 图像数据（numpy数组）
    
    Returns:
        识别结果列表：[(x1, y1, x2, y2, text, confidence), ...]
    """
    if ocr is None:
        return []
    
    try:
        # PaddleOCR识别
        result = ocr.ocr(image, cls=True)
        
        if not result or not result[0]:
            return []
        
        # 解析结果
        text_regions = []
        for line in result[0]:
            box = line[0]  # 四个点的坐标
            text = line[1][0]  # 识别的文本
            confidence = line[1][1]  # 置信度
            
            # 计算边界框
            x1 = min(box[0][0], box[3][0])
            y1 = min(box[0][1], box[1][1])
            x2 = max(box[1][0], box[2][0])
            y2 = max(box[2][1], box[3][1])
            
            text_regions.append((x1, y1, x2, y2, text, confidence))
        
        return text_regions
    except Exception as exc:
        _c('WARN', f'OCR识别失败：{exc}')
        return []


def ocr_video_frames(video_path: str, 
                     frames: list[tuple[float, str]], 
                     ocr_interval: float = DEFAULT_OCR_INTERVAL,
                     ocr_lang: str = DEFAULT_OCR_LANG,
                     use_gpu: bool = False) -> dict:
    """对视频帧进行OCR识别，返回时间戳到识别结果的映射。
    
    Args:
        video_path: 视频路径
        frames: 抽帧列表（时间戳, base64）
        ocr_interval: OCR识别间隔（秒），避免每帧都识别
        ocr_lang: OCR语言
        use_gpu: 是否使用GPU
    
    Returns:
        字典：{timestamp: [(x1, y1, x2, y2, text, confidence), ...]}
    """
    if not _HAVE_PADDLE_OCR:
        return {}
    
    ocr = init_ocr(ocr_lang, use_gpu)
    if ocr is None:
        return {}
    
    _c('INFO', f'硬字幕OCR：识别 {len(frames)} 帧中的文字（间隔={ocr_interval}s）...')
    
    results = {}
    last_ocr_time = -ocr_interval  # 确保第一帧被识别
    
    for i, (ts, b64) in enumerate(frames):
        # 按间隔进行OCR识别
        if ts - last_ocr_time < ocr_interval and i > 0:
            continue
        
        # 解码base64图像
        try:
            img_data = base64.b64decode(b64)
            img_array = np.frombuffer(img_data, dtype=np.uint8)
            image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            
            if image is None:
                continue
            
            # OCR识别
            text_regions = recognize_text_in_frame(ocr, image)
            
            if text_regions:
                results[ts] = text_regions
                texts = [r[4] for r in text_regions if r[4]]
                _c('INFO', f'  帧 {i+1} @ {ts:.1f}s: 识别到 {len(text_regions)} 个文本区域，内容：{texts[:3]}...')
            
            last_ocr_time = ts
        except Exception as exc:
            _c('WARN', f'  帧 {i+1} @ {ts:.1f}s OCR失败：{exc}')
            continue
    
    _c('OK', f'OCR识别完成：在 {len(frames)} 帧中识别到 {sum(len(v) for v in results.values())} 个文本区域')
    
    return results


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


def notebook_frame_prompt(language: str, style: str = 'education') -> str:
    """笔记模式的逐帧提示词 - 针对知识提取优化。

    Args:
        style: 笔记风格
            - education: 教育/课程视频（提取知识点、公式、例题）
            - general: 通用视频（提取关键信息）
            - meeting: 会议/演讲（提取议题、结论、行动项）
    """
    prompts = {
        'education': {
            'zh': ('你是一位专业的学习笔记助手。请分析这帧教学/课程画面，提取以下信息（如有）：\n'
                   '1. **知识点/主题**：当前讲解的核心概念或公式\n'
                   '2. **画面文字**：PPT/黑板上的文字内容（原样保留）\n'
                   '3. **关键要点**：讲师强调的重点\n'
                   '4. **例题/题目**：展示的例题或练习题\n'
                   '请用简洁的中文输出，没有的内容不编造。格式参考：\n'
                   '- 【知识点】xxx\n'
                   '- 【板书】xxx\n'
                   '- 【要点】xxx'),
            'en': ('You are a study note assistant. Analyze this educational video frame and extract:\n'
                   '1. **Key Concept**: The core topic or formula being taught\n'
                   '2. **On-screen Text**: Text from PPT/blackboard (preserve original)\n' 
                   '3. **Key Points**: What the instructor emphasizes\n'
                   '4. **Examples**: Problems or exercises shown\n'
                   'Output concisely in English. Do not fabricate missing content.'),
        },
        'general': {
            'zh': ('请分析这帧画面的核心信息，用简洁的中文输出：\n'
                   '- 核心内容（1句话概括）\n'
                   '- 关键细节（人物、文字、物体等）\n'
                   '- 值得记录的信息点'),
            'en': ('Analyze this frame and output:\n'
                   '- Core content (one-line summary)\n'
                   '- Key details (people, text, objects)\n'
                   '- Notable information worth recording'),
        },
        'meeting': {
            'zh': ('请分析这帧会议/演讲画面，提取：\n'
                   '- 演讲者/发言人\n'
                   '- 当前议题或观点\n'
                   '- PPT/屏幕上的关键内容\n'
                   '- 重要数据或结论'),
            'en': ('Analyze this meeting/presentation frame and extract:\n'
                   '- Speaker\n'
                   '- Current topic or point\n'
                   '- Key PPT/screen content\n'
                   '- Important data or conclusions'),
        },
    }
    style_cfg = prompts.get(style, prompts['general'])
    return style_cfg.get(language, style_cfg['zh'])


def summary_prompt(language: str, meta: dict, transcript: str = '', ocr_text: str = '') -> str:
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
    if ocr_text:
        out += ('\n\nHard-coded subtitles / on-screen text detected via OCR; incorporate this text information.'
                if language == 'en' else
                '\n\n另外通过OCR识别到画面中的硬字幕/文字，请将这些文字信息也纳入分析。')
    return out


def notebook_summary_prompt(language: str, meta: dict, style: str = 'education',
                            transcript: str = '', ocr_text: str = '') -> str:
    """笔记模式的汇总提示词 - 生成结构化学习笔记。

    Args:
        style: 笔记风格（education/general/meeting）
    """
    prompts = {
        'education': {
            'zh': (
                '你是一位专业的学习笔记整理专家。以下是从课程视频中按时间顺序抽取的画面分析结果。\n\n'
                '请将其整理为**结构化的学习笔记**，要求：\n\n'
                '### 格式要求\n'
                '1. **课程概述**：本节课程的核心主题和目标\n'
                '2. **知识点清单**：按逻辑顺序列出所有知识点，每个知识点包含：\n'
                '   - 概念名称\n'
                '   - 简要解释\n'
                '   - 对应时间戳 [MM:SS]\n'
                '3. **重点解析**：\n'
                '   - 题目/问题类型归纳\n'
                '   - 解题策略/方法论\n'
                '   - 易错点/注意事项\n'
                '4. **例题整理**（如有）：\n'
                '   - 例题原文\n'
                '   - 解题思路\n'
                '   - 时间戳引用\n'
                '5. **板书/PPT文字汇总**（如有）\n'
                '6. **复习建议**：关键要点回顾\n\n'
                '### 注意事项\n'
                '- 每个重要知识点必须标注对应的时间戳 [MM:SS]，方便回看\n'
                '- 保持逻辑清晰，同类内容合并\n'
                '- 不编造不存在的内容\n'
                f'\n\n---\n（视频参数：时长 {meta["duration_hms"]} / 分辨率 {meta["resolution"]}）\n'
            ),
            'en': (
                'You are an expert at creating structured study notes. Below are frame-by-frame analyses '
                'from an educational video.\n\nPlease organize them into a **structured study note** with:\n\n'
                '### Required Sections\n'
                '1. **Course Overview**: Core topic and objectives\n'
                '2. **Knowledge Points**: List all concepts logically, each with:\n'
                '   - Concept name\n'
                '   - Brief explanation\n'
                '   - Timestamp [MM:SS]\n'
                '3. **Key Analysis**:\n'
                '   - Problem types summary\n'
                '   - Solution strategies/methods\n'
                '   - Common pitfalls\n'
                '4. **Example Problems** (if any):\n'
                '   - Original problem\n'
                '   - Solution approach\n'
                '   - Timestamp reference\n'
                '5. **Board/PPT Text Summary** (if any)\n'
                '6. **Review Tips**: Key takeaways\n\n'
                '### Guidelines\n'
                '- Each knowledge point MUST include timestamp [MM:SS] for easy review\n'
                '- Keep logical flow; merge similar content\n'
                f'- Video info: Duration {meta["duration_hms"]} / Resolution {meta["resolution"]}\n'
            ),
        },
        'general': {
            'zh': (
                '以下是视频逐帧分析结果，请整理为**结构化笔记**：\n'
                '## 概要\n'
                '一句话总结视频核心内容。\n'
                '## 详细内容（按时间线）\n'
                '按时间顺序列出关键信息点，每条标注时间戳 [MM:SS]。\n'
                '## 重点摘录\n'
                '值得记录的核心信息、数据、结论。\n'
                f'\n（视频参数：时长 {meta["duration_hms"]}）\n'
            ),
            'en': (
                'Below are frame analyses. Please create **structured notes**:\n'
                '## Summary\n'
                'One-line overview of the video.\n'
                '## Detailed Content (Timeline)\n'
                'Key information points in chronological order, each with [MM:SS].\n'
                '## Key Takeaways\n'
                'Core information, data, conclusions worth recording.\n'
                f'\n(Video: Duration {meta["duration_hms"]})\n'
            ),
        },
        'meeting': {
            'zh': (
                '以下是会议/演讲视频的逐帧分析，请整理为**会议纪要格式**：\n'
                '## 会议概要\n'
                '会议主题、时间、参会人（如可识别）\n'
                '## 议程与讨论\n'
                '按时间线列出各议题及核心观点，标注时间戳 [MM:SS]。\n'
                '## 决议与行动项\n'
                '明确的决定和待办事项。\n'
                '## 关键数据/图表\n'
                '重要的数字、图表内容。\n'
                f'\n（视频参数：时长 {meta["duration_hms"]}）\n'
            ),
            'en': (
                'Below are frame analyses of a meeting/video. Please create **meeting minutes**:\n'
                '## Meeting Overview\n'
                'Topic, time, attendees (if identifiable)\n'
                '## Agenda & Discussion\n'
                'Topics and key points in chronological order, each with [MM:SS].\n'
                '## Decisions & Action Items\n'
                'Clear decisions and next steps.\n'
                '## Key Data/Charts\n'
                'Important numbers, chart contents.\n'
                f'\n(Video: Duration {meta["duration_hms"]})\n'
            ),
        },
    }
    style_cfg = prompts.get(style, prompts['general'])
    out = style_cfg.get(language, style_cfg['zh'])

    if transcript:
        out += ('\n\n【音频转写文本】请将以下语音内容也纳入笔记整理：\n' if language == 'zh'
                else '\n\n[Audio Transcript] Please also incorporate the following speech:\n')
        out += transcript

    if ocr_text:
        out += ('\n\n【OCR识别文字】画面中的文字内容如下，请整合到对应的知识点中：\n' if language == 'zh'
                else '\n\n[OCR Text] On-screen text to incorporate:\n')
        out += ocr_text

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

    # ===== 模糊视频清理：整段模糊则记录到 delete.log，并按需跳过进一步分析 =====
    blur_delete_enabled = bool(task.get('blur_delete_enabled', defaults.get('blur_delete_enabled', DEFAULT_BLUR_DELETE_ENABLED)))
    if blur_delete_enabled:
        blur_threshold = float(task.get('blur_delete_threshold', defaults.get('blur_delete_threshold', DEFAULT_BLUR_DELETE_THRESHOLD)))
        blur_skip = bool(task.get('blur_delete_skip', defaults.get('blur_delete_skip', DEFAULT_BLUR_DELETE_SKIP)))
        blur_sample = int(task.get('blur_delete_sample', defaults.get('blur_delete_sample', DEFAULT_BLUR_DELETE_SAMPLE)))
        avg_var = assess_video_blur(video_path, blur_sample)
        if avg_var is None:
            _c('WARN', '无法估算视频清晰度（缺 numpy 或解码失败），跳过模糊判定')
        elif avg_var < blur_threshold:
            record_blurry_video(video_path, avg_var)
            generate_delete_script()  # 立即更新删除脚本，便于中途直接执行
            if blur_skip:
                _c('INFO', f'模糊视频跳过进一步分析：{video_path}')
                return
        else:
            _c('INFO', f'清晰度检查通过（平均拉普拉斯方差={avg_var:.2f} ≥ {blur_threshold}）')

    # 后端选择：视觉与汇总可分别走 ollama 或 mlx（Apple Silicon 原生）。
    vision_backend = str(task.get('backend') or defaults.get('backend') or 'ollama').lower()
    summary_backend = str(task.get('summary_backend') or defaults.get('summary_backend') or vision_backend).lower()

    if vision_backend == 'mlx':
        model = task.get('mlx_model') or defaults.get('mlx_model') or DEFAULT_MLX_VLM_MODEL
    else:
        model = task.get('model') or defaults.get('default_model')

    if summary_backend == 'mlx':
        summary_model = task.get('mlx_summary_model') or defaults.get('mlx_summary_model') or DEFAULT_MLX_LM_MODEL
    else:
        summary_model = task.get('summary_model') or defaults.get('default_summary_model') or model

    frame_max_tokens = int(task.get('frame_max_tokens', defaults.get('frame_max_tokens', DEFAULT_FRAME_MAX_TOKENS)))
    summary_max_tokens = int(task.get('summary_max_tokens', defaults.get('summary_max_tokens', DEFAULT_SUMMARY_MAX_TOKENS)))
    language = task.get('language', defaults.get('language', 'zh'))
    interval = float(task.get('frame_interval', DEFAULT_FRAME_INTERVAL))
    max_frames = int(task.get('max_frames', DEFAULT_MAX_FRAMES))
    min_frames = int(task.get('min_frames', defaults.get('min_frames', DEFAULT_MIN_FRAMES)))
    sample_mode = str(task.get('sample_mode') or defaults.get('sample_mode') or 'auto').lower()
    scene_threshold = float(task.get('scene_threshold', defaults.get('scene_threshold', DEFAULT_SCENE_THRESHOLD)))
    scene_probe = float(task.get('scene_probe', defaults.get('scene_probe', DEFAULT_SCENE_PROBE)))
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
    # 批量场景：若同名报告已存在则跳过，便于「增量」分析新视频。
    skip_existing = bool(task.get('skip_existing', defaults.get('skip_existing', False)))
    if skip_existing and save_report:
        _existing = f'{_out_base(video_path, output_dir)}.analysis.md'
        if os.path.isfile(_existing):
            _c('INFO', f'已存在报告，跳过：{_existing}')
            return

    # ===== 笔记模式配置 =====
    notebook_mode = bool(task.get('notebook_mode', defaults.get('notebook_mode', DEFAULT_NOTEBOOK_MODE)))
    notebook_style = str(task.get('notebook_style', defaults.get('notebook_style', DEFAULT_NOTEBOOK_STYLE)) or DEFAULT_NOTEBOOK_STYLE)
    
    if notebook_mode:
        _c('OK', f'笔记模式已启用（风格={notebook_style}）- 将保存帧截图并生成结构化笔记')

    if not model:
        _c('ERR', f'未指定视觉模型（task.model 或 default_model）：{video_path}')
        return

    started = time.time()
    _c('INFO', f'分析：{video_path}')

    # ===== 抽帧相关配置（必须在抽帧前完成定义，避免「先用后定义」崩溃）=====
    # 硬字幕 OCR 开关
    ocr_enabled = bool(task.get('ocr_enabled', defaults.get('ocr_enabled', DEFAULT_OCR_ENABLED)))
    # 内容去重
    dedup_enabled = bool(task.get('dedup_enabled', defaults.get('dedup_enabled', DEFAULT_DEDUP_ENABLED)))
    dedup_threshold = int(task.get('dedup_threshold', defaults.get('dedup_threshold', DEFAULT_DEDUP_THRESHOLD)))
    dedup_config = {'enabled': True, 'threshold': dedup_threshold} if dedup_enabled else None
    if dedup_enabled:
        _c('INFO', f'内容去重已启用（阈值={dedup_threshold}）')
    # 增强版镜头检测
    scene_enhanced = bool(task.get('scene_enhanced', defaults.get('scene_enhanced', False)))
    if scene_enhanced and sample_mode == 'scene':
        _c('INFO', '增强版镜头检测已启用（综合多维度）')
    # 帧质量过滤
    quality_filter_enabled = bool(task.get('quality_filter_enabled', defaults.get('quality_filter_enabled', DEFAULT_QUALITY_FILTER_ENABLED)))
    quality_config = None
    if quality_filter_enabled:
        quality_config = {
            'enabled': True,
            'blur_threshold': float(task.get('quality_blur_threshold', defaults.get('quality_blur_threshold', DEFAULT_QUALITY_BLUR_THRESHOLD))),
            'dark_threshold': float(task.get('quality_dark_threshold', defaults.get('quality_dark_threshold', DEFAULT_QUALITY_DARK_THRESHOLD))),
            'bright_threshold': float(task.get('quality_bright_threshold', defaults.get('quality_bright_threshold', DEFAULT_QUALITY_BRIGHT_THRESHOLD))),
            'static_threshold': float(task.get('quality_static_threshold', defaults.get('quality_static_threshold', DEFAULT_QUALITY_STATIC_THRESHOLD))),
        }
        _c('INFO', f'帧质量过滤已启用（模糊阈值={quality_config["blur_threshold"]}，亮度范围={quality_config["dark_threshold"]}-{quality_config["bright_threshold"]}）')
    # 笔记模式：帧截图保存目录
    _img_dir = None
    if notebook_mode:
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        if output_dir:
            _img_dir = os.path.join(output_dir, f'{base_name}_frames')
        else:
            _img_dir = f'{os.path.splitext(video_path)[0]}_frames'

    # 检测并配置GPU加速
    gpu_available = auto_configure_gpu(task, defaults)
    
    if vision_backend == 'mlx' or summary_backend == 'mlx':
        _c('INFO', f'MLX 后端启用（视觉={vision_backend} / 汇总={summary_backend}）；'
                   f'首次使用会自动下载模型，请耐心等待。')
    
    # 如果GPU可用且OCR启用，自动配置OCR使用GPU
    if gpu_available and ocr_enabled:
        _c('INFO', 'GPU可用，OCR将尝试使用GPU加速')
        # 注意：PaddleOCR的GPU配置在init_ocr函数中处理

    # 1) 主要参数
    meta = probe_metadata(video_path)
    _c('OK', f'参数 → 时长 {meta["duration_hms"]} ({meta["duration_sec"]}s) | '
             f'分辨率 {meta["resolution"]} | {meta["fps"]}fps | 编码 {meta["codec"]} | {meta["size_mb"]}MB')

    # 1.5) 音频转写异步化：提交到全局单工位执行器后立即返回，不阻塞主流程。
    # 主流程继续做抽帧 → 逐帧识别 → OCR（等 Ollama 时 CPU 空闲，whisper 恰好补位），
    # 直到汇总前才取结果（Future.result）。
    asr_future = None
    if include_audio:
        _c('INFO', f'音频转写已提交后台队列（faster-whisper / {whisper_model}），不阻塞主流程…')
        asr_future = submit_transcribe_async(video_path, whisper_model, whisper_language)

    # 2) 抽帧（按 sample_mode 选择抽样策略；可「时间换精度」）
    # 检查是否需要分段处理（长视频）
    segment_enabled = bool(task.get('segment_enabled', defaults.get('segment_enabled', DEFAULT_SEGMENT_ENABLED)))
    segment_duration = float(task.get('segment_duration', defaults.get('segment_duration', DEFAULT_SEGMENT_DURATION)))
    segment_overlap = float(task.get('segment_overlap', defaults.get('segment_overlap', DEFAULT_SEGMENT_OVERLAP)))
    
    frames = []
    if segment_enabled and meta['duration_sec'] > segment_duration:
        # 长视频分段处理
        _c('INFO', f'长视频分段处理已启用（每段{segment_duration}秒，重叠{segment_overlap}秒）')
        segments = split_video_for_processing(video_path, segment_duration, segment_overlap)
        
        all_frame_results = []
        for seg_idx, (seg_start, seg_end) in enumerate(segments):
            _c('INFO', f'  处理第 {seg_idx+1}/{len(segments)} 段（{seg_start:.1f}s - {seg_end:.1f}s）')
            
            seg_frames = extract_frames_from_segment(
                video_path, seg_start, seg_end,
                max(0.5, interval), max_frames, sample_mode,
                scene_threshold, scene_probe, min_frames,
                save_images=notebook_mode, image_dir=_img_dir,
                quality_config=quality_config,
                dedup_config=dedup_config,
                scene_enhanced=scene_enhanced
            )
            
            if seg_frames:
                all_frame_results.extend(seg_frames)
                _c('INFO', f'    段 {seg_idx+1} 抽取 {len(seg_frames)} 帧')
        
        # 去重（跨段去重）
        if dedup_config and dedup_config.get('enabled', False) and all_frame_results:
            _c('INFO', f'跨段去重：对 {len(all_frame_results)} 帧进行去重...')
            # 重新计算哈希并去重
            deduplicated = []
            hashes = []
            for ts, b64 in all_frame_results:
                try:
                    img_data = base64.b64decode(b64)
                    img_array = np.frombuffer(img_data, dtype=np.uint8)
                    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                    if frame is not None:
                        hash_val = _perceptual_hash(frame)
                        if hash_val:
                            is_dup = False
                            for existing_hash in hashes:
                                if _hamming_distance(hash_val, existing_hash) < dedup_threshold:
                                    is_dup = True
                                    break
                            if not is_dup:
                                deduplicated.append((ts, b64))
                                hashes.append(hash_val)
                        else:
                            deduplicated.append((ts, b64))
                except Exception:
                    deduplicated.append((ts, b64))
            
            _c('OK', f'跨段去重完成：{len(all_frame_results)} 帧 → {len(deduplicated)} 帧')
            frames = deduplicated
        else:
            frames = all_frame_results
        
        _c('OK', f'分段处理完成：共抽取 {len(frames)} 帧')
    else:
        # 普通处理（不分段的原有逻辑）
        _min_hint = f'，最少保底 {min_frames} 帧' if min_frames else ''
        if sample_mode == 'interval':
            _c('INFO', f'抽帧方式：时间跨度，每 {interval:g}s 一帧（安全上限 {MAX_FRAMES_HARD_CAP} 帧{_min_hint}）')
        elif sample_mode == 'count':
            _c('INFO', f'抽帧方式：固定张数，全片均匀 {max_frames} 帧')
        elif sample_mode == 'scene':
            _c('INFO', f'抽帧方式：镜头切换检测（每 {scene_probe:g}s 探测，相关度<{scene_threshold:g} 判为新镜头，上限 {MAX_FRAMES_HARD_CAP} 帧{_min_hint}）')
        else:
            _c('INFO', f'抽帧方式：自动（≤{max_frames} 帧且不密于 {interval:g}s{_min_hint}）')

        frames = extract_frames(
            video_path, max(0.5, interval), max_frames, sample_mode,
            scene_threshold, scene_probe, min_frames,
            save_images=notebook_mode, image_dir=_img_dir,
            quality_config=quality_config,
            dedup_config=dedup_config,
            scene_enhanced=scene_enhanced
        )

    if not frames:
        _c('ERR', '未能从视频解码出任何帧')
        if asr_future is not None:
            asr_future.cancel()  # 还在排队则直接取消，避免白跑
        return
    _c('INFO', f'已抽取 {len(frames)} 帧，使用视觉模型 [{vision_backend}] {model} 逐帧识别…')
    
    # 如果启用了去重，在extract_frames中已经处理，这里不需要重复处理
    if dedup_enabled:
        _c('INFO', f'内容去重已启用（阈值={dedup_threshold}）')

    # 3) 逐帧描述（支持并发加速）- 笔记模式使用专用提示词
    if notebook_mode:
        fp = notebook_frame_prompt(language, notebook_style)
        _c('INFO', f'使用笔记模式提示词（风格={notebook_style}）')
    else:
        fp = frame_prompt(language, custom_prompt)
    
    concurrency = int(task.get('concurrency', defaults.get('concurrency', DEFAULT_CONCURRENCY)))
    concurrency = max(1, min(concurrency, MAX_CONCURRENCY))  # 安全上限，避免 Ollama 过载
    
    if concurrency > 1 and len(frames) > 1:
        # 并发处理
        _c('INFO', f'使用并发模式分析帧（并发数={concurrency}）...')
        
        # 检查缓存
        cache_enabled = bool(task.get('cache_enabled', defaults.get('cache_enabled', DEFAULT_CACHE_ENABLED)))
        cache_dir = task.get('cache_dir', defaults.get('cache_dir', DEFAULT_CACHE_DIR))
        
        if cache_enabled:
            _c('INFO', '检查缓存...')
            cached_results = []
            uncached_frames = []
            
            for i, (ts, b64) in enumerate(frames):
                cache_key = _get_cache_key(video_path, ts, model, fp)
                cached = _load_cache(cache_dir, cache_key)
                if cached:
                    cached_results.append((i, ts, cached))
                    _c('INFO', f'  帧 {i + 1}/{len(frames)} @ {ts:.1f}s [缓存]')
                else:
                    uncached_frames.append((i, ts, b64))
            
            if uncached_frames:
                _c('INFO', f'缓存命中 {len(cached_results)} 帧，需分析 {len(uncached_frames)} 帧')
                # 并发分析未缓存的帧
                uncached_results = process_frames_concurrently(
                    [(ts, b64) for _, ts, b64 in uncached_frames], 
                    vision_backend, ollama_url, model, fp, frame_max_tokens,
                    concurrency=concurrency, stream_log=stream_log
                )
                
                # 保存结果到缓存
                for (i, ts, b64), (_, _, desc) in zip(uncached_frames, uncached_results):
                    cache_key = _get_cache_key(video_path, ts, model, fp)
                    _save_cache(cache_dir, cache_key, desc)
                    cached_results.append((i, ts, desc))
            else:
                _c('INFO', '全部帧命中缓存，跳过分析')
            
            # 按索引排序
            frame_results = sorted(cached_results, key=lambda x: x[0])
        else:
            # 不使用缓存，直接并发分析
            frame_results_raw = process_frames_concurrently(
                frames, vision_backend, ollama_url, model, fp, frame_max_tokens,
                concurrency=concurrency, stream_log=stream_log
            )
            # 转换为原来的格式
            frame_results = [(i, ts, desc) for i, ts, desc in frame_results_raw]
    else:
        # 顺序处理（原有逻辑）
        _c('INFO', f'使用顺序模式分析帧...')
        
        # 检查缓存
        cache_enabled = bool(task.get('cache_enabled', defaults.get('cache_enabled', DEFAULT_CACHE_ENABLED)))
        cache_dir = task.get('cache_dir', defaults.get('cache_dir', DEFAULT_CACHE_DIR))
        
        frame_results: list[tuple[int, float, str]] = []
        for i, (ts, b64) in enumerate(frames):
            _c('INFO', f'  帧 {i + 1}/{len(frames)} @ {ts:.1f}s')
            
            # 尝试从缓存加载
            if cache_enabled:
                cache_key = _get_cache_key(video_path, ts, model, fp)
                cached = _load_cache(cache_dir, cache_key)
                if cached:
                    _c('INFO', f'    [缓存] {cached[:50]}...')
                    frame_results.append((i, ts, cached))
                    continue
            
            try:
                if stream_log:
                    # 流式：边识别边把描述吐到终端，实时可见
                    sys.stdout.write('\033[2m      ')  # 暗色缩进前缀
                    sys.stdout.flush()
                    desc = chat(
                        vision_backend, ollama_url, model, fp, images=[b64],
                        stream=True, on_delta=lambda d: (sys.stdout.write(d), sys.stdout.flush()),
                        max_tokens=frame_max_tokens,
                    ).strip()
                    sys.stdout.write('\033[0m\n')
                    sys.stdout.flush()
                else:
                    desc = chat(vision_backend, ollama_url, model, fp, images=[b64],
                                max_tokens=frame_max_tokens).strip()
                
                # 保存到缓存
                if cache_enabled:
                    cache_key = _get_cache_key(video_path, ts, model, fp)
                    _save_cache(cache_dir, cache_key, desc)
            except Exception as exc:
                desc = f'(帧识别失败：{exc})'
                _c('WARN', f'  帧 {i + 1} 识别失败：{exc}')
            frame_results.append((i, ts, desc))

    # 3.4) 把每一帧的识别结果写入日志文件（按帧汇总，便于事后核查每个视频帧的运行结果）
    for idx, ts, desc in frame_results:
        _log_frame_detail(video_path, idx, ts, desc)
    _log_to_file('OK', f'逐帧识别完成：共 {len(frame_results)} 帧结果已写入日志')

    # 3.5) 可选：硬字幕OCR（PaddleOCR）—— 先跑 OCR，让后台音频转写继续与之重叠
    ocr_results = {}
    if ocr_enabled:
        ocr_interval = float(task.get('ocr_interval', defaults.get('ocr_interval', DEFAULT_OCR_INTERVAL)))
        ocr_lang = task.get('ocr_lang', defaults.get('ocr_lang', DEFAULT_OCR_LANG))
        ocr_use_gpu = bool(task.get('ocr_use_gpu', defaults.get('ocr_use_gpu', False)))
        
        _c('INFO', f'硬字幕OCR识别（PaddleOCR / {ocr_lang}）…')
        ocr_results = ocr_video_frames(
            video_path, frames, 
            ocr_interval=ocr_interval,
            ocr_lang=ocr_lang,
            use_gpu=ocr_use_gpu
        )
        if ocr_results:
            total_text_regions = sum(len(v) for v in ocr_results.values())
            _c('OK', f'OCR识别完成（在 {len(ocr_results)} 帧中识别到 {total_text_regions} 个文本区域）')
        else:
            _c('WARN', '未识别到硬字幕文字（可能视频无字幕或 PaddleOCR 不可用）')

    # 3.6) 可选：音频转写结果回收——汇总必须用到，此处才真正等待（其余阶段均未阻塞）
    transcript, segments, srt_path = '', [], None
    if include_audio and asr_future is not None:
        if not asr_future.done():
            _c('INFO', '等待后台音频转写完成…')
        try:
            transcript, segments = asr_future.result()
        except Exception as exc:
            _c('WARN', f'后台音频转写异常：{exc}')
            transcript, segments = '', []
        if transcript:
            _c('OK', f'音频转写完成（{len(transcript)} 字，{len(segments)} 段）')
            if save_report:
                srt_path = write_srt(video_path, segments, output_dir=output_dir)
                if srt_path:
                    _c('OK', f'字幕已导出 → {srt_path}')
        else:
            _c('WARN', '未获得音频转写文本（可能无音轨或 faster-whisper 不可用）')

    # 4) 汇总（结合画面 + 音频 + OCR）- 笔记模式使用专用汇总提示词
    _c('INFO', f'使用 [{summary_backend}] {summary_model} 汇总…')
    joined = '\n'.join(f'- [{ts:.1f}s] {desc}' for _, ts, desc in frame_results)
    
    # 准备OCR文本（如果有）
    ocr_text = ''
    if ocr_results:
        ocr_text_parts = []
        for ts, text_regions in sorted(ocr_results.items()):
            texts = [r[4] for r in text_regions if r[4]]
            if texts:
                ocr_text_parts.append(f'[{ts:.1f}s] {" | ".join(texts)}')
        ocr_text = '\n'.join(ocr_text_parts)
    
    # 根据是否笔记模式选择不同的汇总提示词
    if notebook_mode:
        fuse_prompt = notebook_summary_prompt(language, meta, notebook_style, transcript, ocr_text)
    else:
        fuse_prompt = summary_prompt(language, meta, transcript, ocr_text)
    
    fuse_input = f'{fuse_prompt}\n\n## 画面帧描述\n{joined}'
    if transcript:
        fuse_input += f'\n\n## 音频转写\n{transcript}'
    if ocr_text:
        fuse_input += f'\n\n## 画面文字(OCR)\n{ocr_text}'
    
    try:
        if stream_log:
            sys.stdout.write('\033[2m')  # 暗色显示汇总过程
            sys.stdout.flush()
            summary = chat(
                summary_backend, ollama_url, summary_model, fuse_input,
                stream=True, on_delta=lambda d: (sys.stdout.write(d), sys.stdout.flush()),
                max_tokens=summary_max_tokens,
            ).strip()
            sys.stdout.write('\033[0m\n')
            sys.stdout.flush()
        else:
            summary = chat(summary_backend, ollama_url, summary_model, fuse_input,
                           max_tokens=summary_max_tokens).strip()
    except Exception as exc:
        summary = f'(汇总失败：{exc})'

    elapsed = round(time.time() - started, 2)
    _c('OK', f'完成：{video_path}（耗时 {elapsed}s）')

    # 5) 写报告（笔记模式传入notebook_mode参数以嵌入截图）
    if save_report:
        report_path = write_report(
            video_path, model, summary_model, meta, frame_results, summary, elapsed,
            transcript=transcript, srt_path=srt_path, output_dir=output_dir,
            ocr_results=ocr_results if ocr_enabled else None,
            notebook_mode=notebook_mode,
            notebook_style=notebook_style,
        )
        _c('OK', f'报告已保存 → {report_path}')


def write_report(video_path, model, summary_model, meta, frame_results, summary, elapsed,
                 transcript: str = '', srt_path: Optional[str] = None,
                 output_dir: Optional[str] = None,
                 ocr_results: Optional[dict] = None,
                 notebook_mode: bool = False,
                 notebook_style: str = 'education') -> str:
    """写分析报告，支持笔记模式（嵌入帧截图）。"""
    # 笔记模式使用不同的文件后缀
    if notebook_mode:
        report_path = f'{_out_base(video_path, output_dir)}.notebook.md'
    else:
        report_path = f'{_out_base(video_path, output_dir)}.analysis.md'
    
    # 计算相对路径（用于Markdown图片引用）
    def _img_rel_path(img_abs_path: str) -> str:
        """计算图片相对于报告文件的路径"""
        try:
            report_dir = os.path.dirname(os.path.abspath(report_path))
            return os.path.relpath(img_abs_path, report_dir)
        except Exception:
            return os.path.basename(img_abs_path)

    # 笔记模式：生成结构化笔记
    if notebook_mode:
        lines = [
            '# 📝 视频学习笔记 / Video Study Notes',
            '',
            f'> **视频**: `{os.path.basename(video_path)}`',
            f'> **生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'> **时长**: {meta["duration_hms"]} | **分辨率**: {meta["resolution"]}',
            f'> **分析模型**: {model}',
            '',
            '---',
            '',
        ]
        
        # 解析汇总内容，尝试提取知识点并嵌入截图
        # 在笔记模式中，summary已经是由notebook_summary_prompt生成的结构化笔记
        # 我们需要在处理后的报告中嵌入截图
        
        # 先添加汇总内容
        lines += [summary, '', '---', '']
        
        # 添加带截图的关键帧
        lines += ['## 🖼️ 关键帧截图 / Key Frame Screenshots', '']
        lines += ['> 以下是分析过程中抽取的关键帧截图，对应视频中的重要时间点。', '']
        
        for idx, ts, desc in frame_results:
            # 转换时间戳格式
            ts_str = time.strftime('%M:%S', time.gmtime(ts))
            
            lines += [f'### [{ts_str}] 帧 {idx + 1}', '']
            
            # 嵌入截图
            if ts in _FRAME_FILES:
                img_path, img_name = _FRAME_FILES[ts]
                rel_path = _img_rel_path(img_path)
                lines += [f'![截图@{ts_str}]({rel_path})', '']
            
            # 添加帧描述（简洁版）
            # 尝试从desc中提取关键信息
            desc_lines = desc.split('\n')
            key_info = []
            for line in desc_lines:
                if line.strip() and not line.startswith('#'):
                    key_info.append(line)
            
            if key_info:
                lines += ['**帧描述**:', '> ' + '\n> '.join(key_info[:3]), '']
            
            lines += ['---', '']
        
        # 如果有OCR结果，添加文字识别部分
        if ocr_results:
            lines += ['## 📝 画面文字识别 / On-screen Text', '']
            lines += ['| 时间 | 识别的文字 |', '|------|-----------|']
            for ts in sorted(ocr_results.keys()):
                text_regions = ocr_results[ts]
                texts = [r[4] for r in text_regions if r[4]]
                if texts:
                    ts_str = time.strftime('%M:%S', time.gmtime(ts))
                    lines += [f'| [{ts_str}] | {"<br>".join(texts)} |']
            lines += ['', '']
        
        # 如果有音频转写，添加文字稿部分
        if transcript:
            lines += ['## 🎤 音频转写 / Audio Transcript', '']
            if srt_path:
                lines += [f'> 字幕文件: `{os.path.basename(srt_path)}`', '']
            lines += [transcript, '']
        
    else:
        # 普通分析模式（原有逻辑）
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
        
        # 添加OCR结果
        if ocr_results:
            lines += ['---', '', '## 画面文字识别(OCR) / On-screen Text Recognition', '']
            lines += ['| 时间(s) | 识别的文字 |', '|---------|-----------|']
            for ts in sorted(ocr_results.keys()):
                text_regions = ocr_results[ts]
                texts = [r[4] for r in text_regions if r[4]]
                if texts:
                    lines += [f'| {ts:.1f} | {"<br>".join(texts)} |']
            lines += ['', '']
        
        # 逐帧描述部分
        lines += ['---', '', '## 逐帧描述 / Per-frame descriptions', '']
        for idx, ts, desc in frame_results:
            lines += [f'### 帧 {idx} @ {ts:.1f}s', '', desc, '']

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return report_path


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def _iter_videos(directory: str, recursive: bool = False) -> list[str]:
    """列出目录下所有「支持的」视频文件（按路径排序）。recursive=True 时含子目录。"""
    out: list[str] = []
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in SUPPORTED_EXTS:
                    out.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(directory):
            p = os.path.join(directory, fn)
            if os.path.isfile(p) and os.path.splitext(fn)[1].lower() in SUPPORTED_EXTS:
                out.append(p)
    return sorted(out)


def _expand_tasks(tasks: list[dict]) -> list[dict]:
    """把含 ``video_dir`` 的任务展开成「目录下每个视频一个任务」。

    - ``video_dir``：要扫描的目录（绝对路径或 ~ 开头）。
    - ``recursive``：是否递归子目录（默认 False）。
    - 其余键（model / sample_mode / backend / include_audio 等）原样继承到每个视频。
    - 不含 ``video_dir`` 的任务（用 ``video_path``）原样保留，向后兼容。
    """
    expanded: list[dict] = []
    for t in tasks:
        vdir = t.get('video_dir')
        if not vdir:
            expanded.append(t)
            continue
        vdir = os.path.expanduser(vdir)
        if not os.path.isdir(vdir):
            _c('ERR', f'目录不存在：{vdir}')
            continue
        recursive = bool(t.get('recursive', False))
        videos = _iter_videos(vdir, recursive)
        if not videos:
            _c('WARN', f'目录中未找到支持的视频：{vdir}')
            continue
        _c('INFO', f'目录 {vdir} 命中 {len(videos)} 个视频{"（含子目录）" if recursive else ""}')
        for vp in videos:
            nt = {k: v for k, v in t.items() if k not in ('video_dir', 'recursive')}
            nt['video_path'] = vp
            expanded.append(nt)
    return expanded


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_config = os.path.normpath(os.path.join(here, '..', 'video-tasks.json'))

    parser = argparse.ArgumentParser(description='离线视频分析任务运行器')
    parser.add_argument('--config', default=default_config, help='任务配置 JSON 路径')
    parser.add_argument('--ollama-url', default=None, help='覆盖 Ollama 地址')
    parser.add_argument('--backend', choices=['ollama', 'mlx'], default=None,
                        help='视觉(逐帧)后端：ollama | mlx（Apple Silicon 原生）')
    parser.add_argument('--summary-backend', choices=['ollama', 'mlx'], default=None,
                        help='汇总后端：ollama | mlx（默认跟随 --backend）')
    parser.add_argument('--mlx-model', default=None,
                        help=f'MLX 视觉模型 id（默认 {DEFAULT_MLX_VLM_MODEL}）')
    parser.add_argument('--mlx-summary-model', default=None,
                        help=f'MLX 文本汇总模型 id（默认 {DEFAULT_MLX_LM_MODEL}）')
    parser.add_argument('--video-dir', default=None,
                        help='分析该目录下的所有视频（忽略配置里的 tasks，按全局默认参数批量跑）')
    parser.add_argument('--recursive', action='store_true',
                        help='配合 --video-dir：递归扫描子目录')
    parser.add_argument('--skip-existing', action='store_true',
                        help='跳过已生成同名 .analysis.md 报告的视频（增量分析）')
    parser.add_argument('--log-file', default=None,
                        help='运行日志写入的文件路径（默认 logs/video_tasks.log）；'
                             '可指向 logs/backend.log 等同目录文件')
    parser.add_argument('--watch', action='store_true',
                        help='守护模式：持续监控 --video-dir，发现新视频自动分析（配合 --skip-existing 实现增量）')
    parser.add_argument('--watch-interval', type=int, default=300,
                        help='守护模式的扫描间隔（秒，默认300）。仅在 --watch 模式下生效')
    # 模糊视频清理相关开关
    parser.add_argument('--blur-delete', action='store_true',
                        help='开启「整段模糊检测」：判为模糊的视频记录到 delete.log 并生成删除脚本')
    parser.add_argument('--blur-threshold', type=float, default=None,
                        help='整段模糊判定阈值（平均拉普拉斯方差；低于即模糊，默认100）')
    parser.add_argument('--blur-delete-log', default=None,
                        help='模糊视频记录文件路径（默认 logs/delete.log）')
    parser.add_argument('--blur-delete-script', default=None,
                        help='生成的 rm -rf 删除脚本路径（默认 logs/delete.sh）')
    parser.add_argument('--blur-delete-skip', action='store_true', default=None,
                        help='判为模糊后跳过昂贵的逐帧/汇总分析（默认开启）')
    parser.add_argument('--task-workers', type=int, default=None,
                        help='同时处理的视频任务数（默认1=串行；2-3 可让一个视频等待 Ollama 时'
                             '另一个视频做抽帧/转写等 CPU 工作，提升整体 CPU 利用率）')
    args = parser.parse_args()

    if not os.path.isfile(args.config):
        _c('ERR', f'未找到配置文件：{args.config}')
        return 1

    with open(args.config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # CLI 覆盖到全局默认（任务级配置仍可逐条覆盖）
    if args.backend:
        cfg['backend'] = args.backend
    if args.summary_backend:
        cfg['summary_backend'] = args.summary_backend
    if args.mlx_model:
        cfg['mlx_model'] = args.mlx_model
    if args.mlx_summary_model:
        cfg['mlx_summary_model'] = args.mlx_summary_model
    if args.skip_existing:
        cfg['skip_existing'] = True

    # CLI 模糊清理开关覆盖到配置
    if args.blur_delete:
        cfg['blur_delete_enabled'] = True
        if args.blur_delete_skip is None:
            cfg['blur_delete_skip'] = True
    if args.blur_threshold is not None:
        cfg['blur_delete_threshold'] = args.blur_threshold
    if args.blur_delete_skip is not None:
        cfg['blur_delete_skip'] = args.blur_delete_skip

    # 模糊视频清理：记录文件与删除脚本路径（全局，供 run_task 内调用）
    global DELETE_LOG_PATH, DELETE_SCRIPT_PATH
    DELETE_LOG_PATH = cfg.get('blur_delete_log') or DEFAULT_BLUR_DELETE_LOG
    DELETE_SCRIPT_PATH = cfg.get('blur_delete_script') or DEFAULT_BLUR_DELETE_SCRIPT
    if args.blur_delete_log:
        DELETE_LOG_PATH = args.blur_delete_log
    if args.blur_delete_script:
        DELETE_SCRIPT_PATH = args.blur_delete_script
    DELETE_LOG_PATH = os.path.expanduser(DELETE_LOG_PATH)
    DELETE_SCRIPT_PATH = os.path.expanduser(DELETE_SCRIPT_PATH)

    # 日志文件：把运行日志（含每帧去重/质量过滤/识别结果）写入 logs/ 下的文件
    log_file = args.log_file or cfg.get('log_file')
    if not log_file:
        log_file = os.path.normpath(os.path.join(here, '..', 'logs', 'video_tasks.log'))
    set_log_file(log_file)
    _c('INFO', f'运行日志将写入：{log_file}')

    ollama_url = args.ollama_url or cfg.get('ollama_url') or os.getenv('OLLAMA_BASE_URL') or 'http://localhost:11434'

    # 任务来源：--video-dir 优先（扫描整目录），否则用配置里的 tasks。
    if args.video_dir:
        tasks = [{'video_dir': args.video_dir, 'recursive': args.recursive}]
    else:
        tasks = cfg.get('tasks', [])
    if not tasks:
        _c('WARN', 'video-tasks.json 中没有任务（tasks 为空），且未指定 --video-dir，跳过。')
        return 0

    # --watch 守护模式：持续监控目录，发现新视频自动分析。
    # 注意：watch 模式在任务展开之前拦截，允许目录初始为空（等待新视频出现）。
    if args.watch and args.video_dir:
        watch_dir = os.path.expanduser(args.video_dir)
        watch_interval = max(60, args.watch_interval)
        _c('OK', f'守护模式启动：监控目录 {watch_dir}，每 {watch_interval}s 扫描一次（Ctrl+C 退出）')

        # 确保配置中有 skip_existing
        cfg['skip_existing'] = True

        # 探活 Ollama（watch 模式也需要后端可用）
        try:
            urllib.request.urlopen(f'{ollama_url.rstrip("/")}/api/tags', timeout=10).read()
        except Exception as exc:
            _c('ERR', f'无法连接 Ollama（{ollama_url}）：{exc}')
            return 1
        _c('INFO', f'Ollama = {ollama_url}')

        device_cap = detect_device_capability()
        _c('INFO', f'设备能力探测：{_cap_to_labels(device_cap)}')

        import signal
        running = [True]

        def _signal_handler(sig, frame):
            _c('INFO', '收到退出信号，正在停止守护模式...')
            running[0] = False

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        while running[0]:
            try:
                videos = _iter_videos(watch_dir, args.recursive)
                new_videos = []
                for vp in videos:
                    report = f'{os.path.splitext(vp)[0]}.analysis.md'
                    if not os.path.isfile(report):
                        new_videos.append(vp)

                if new_videos:
                    _c('INFO', f'发现 {len(new_videos)} 个新视频，开始分析...')
                    for vp in new_videos:
                        if not running[0]:
                            break
                        task = {'video_path': vp}
                        # 继承全局配置
                        for k in ('model', 'summary_model', 'language', 'sample_mode',
                                  'max_frames', 'min_frames', 'include_audio', 'whisper_model',
                                  'concurrency', 'cache_enabled', 'gpu_enabled',
                                  'scene_enhanced', 'dedup_enabled', 'quality_filter_enabled'):
                            if k in cfg:
                                task[k] = cfg[k]
                        _c('INFO', f'==== 守护任务: {os.path.basename(vp)} ====')
                        try:
                            _reconcile_capabilities(task, cfg, device_cap)
                            run_task(task, cfg, ollama_url)
                        except Exception as exc:
                            _c('ERR', f'守护任务异常（{vp}）：{exc}')
                    _c('OK', f'本轮分析完成，{len(new_videos)} 个视频已处理')
                else:
                    _c('INFO', f'无新视频，等待下次扫描（{watch_interval}s）')

                # 等待下次扫描（每秒检查退出信号）
                for _ in range(watch_interval):
                    if not running[0]:
                        break
                    time.sleep(1)
            except Exception as exc:
                _c('ERR', f'守护模式异常：{exc}')
                time.sleep(10)

        _c('OK', '守护模式已停止。')
        generate_delete_script()
        return 0

    # 把含 video_dir 的任务展开成「每个视频一个任务」
    tasks = _expand_tasks(tasks)
    if not tasks:
        _c('WARN', '展开后没有可执行的视频任务，跳过。')
        return 0

    # 计算每个任务的有效后端，判断是否真的需要连 Ollama
    def _eff_backends(task: dict) -> tuple[str, str]:
        vb = str(task.get('backend') or cfg.get('backend') or 'ollama').lower()
        sb = str(task.get('summary_backend') or cfg.get('summary_backend') or vb).lower()
        return vb, sb

    need_ollama = any('ollama' in _eff_backends(t) for t in tasks)

    # 探活 Ollama（仅当确有任务使用 ollama 后端时）
    if need_ollama:
        try:
            urllib.request.urlopen(f'{ollama_url.rstrip("/")}/api/tags', timeout=10).read()
        except Exception as exc:
            _c('ERR', f'无法连接 Ollama（{ollama_url}）：{exc}')
            return 1
        _c('INFO', f'共 {len(tasks)} 个视频任务，Ollama = {ollama_url}')
    else:
        _c('INFO', f'共 {len(tasks)} 个视频任务，全部使用 MLX 后端（跳过 Ollama 探活）。')

    # P1-a：能力查询前置。先探测一次本机能力，再按位掩码把每个任务的请求能力
    # 与设备实际能力对齐（缺失即安全降级），避免 run_task 中途因缺依赖崩溃。
    device_cap = detect_device_capability()
    _c('INFO', f'设备能力探测：{_cap_to_labels(device_cap)}')

    def _run_one(i: int, task: dict) -> None:
        _c('INFO', f'==== 任务 {i}/{len(tasks)} ====')
        try:
            _reconcile_capabilities(task, cfg, device_cap)
            run_task(task, cfg, ollama_url)
        except Exception as exc:
            _c('ERR', f'任务异常（{task.get("video_path", "?")}）：{exc}')

    task_workers = args.task_workers if args.task_workers is not None else int(cfg.get('task_workers', 1) or 1)
    task_workers = max(1, min(task_workers, 4))
    # 笔记模式依赖全局 _FRAME_FILES，非线程安全，含此类任务时强制串行
    if task_workers > 1 and any(t.get('notebook_mode') for t in tasks):
        _c('WARN', '检测到 notebook_mode 任务，任务级并行已降级为串行（notebook 模式非线程安全）')
        task_workers = 1

    if task_workers > 1 and len(tasks) > 1:
        from concurrent.futures import ThreadPoolExecutor
        # 并行时关闭流式打印，避免多个任务的输出交错成乱码
        cfg['stream_log'] = False
        _c('INFO', f'任务级并行已启用（workers={task_workers}，已自动关闭流式打印）')
        with ThreadPoolExecutor(max_workers=task_workers) as pool:
            futures = [pool.submit(_run_one, i, task) for i, task in enumerate(tasks, 1)]
            for fu in futures:
                fu.result()
    else:
        for i, task in enumerate(tasks, 1):
            _run_one(i, task)
    _c('OK', '全部视频任务执行完毕。')
    generate_delete_script()
    close_log_file()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
