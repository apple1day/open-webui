The user wants to add audio processing with faster-whisper to `video_tasks.py` and ensure frame extraction is properly configurable—they're noting that `frame_interval` and `max_frames` already exist in the config but want to verify or expand the capability for extracting more frames with longer processing times.

I'll add audio transcription using faster-whisper (which can decode audio directly from video files via PyAV), introduce config options for `include_audio`, `whisper_model`, and optionally `whisper_language`, and create a transcription function that can generate SRT files and include transcript text in the summary output.

For the frame cap, I'll raise it from 64 to something more generous like 2000 to let users trade processing time for more thorough analysis, while keeping a sane upper bound to prevent memory issues.

The implementation will modify `run_task` to handle the new audio config fields, update frame extraction to use the higher cap, add transcription after frame extraction, integrate the transcript into the summary prompt, and include both the transcript and SRT file path in the final report.

I'm building a helper function to handle transcription using faster-whisper with CPU optimization (device="cpu", compute_type="int8" for Mac compatibility), which will return both the full transcript text and individual segments, then generate an SRT file with proper timestamp formatting.

I also need to update the config schema in video-tasks.json to document the new fields and the frame count tradeoff, and update the documentation in use.md to explain these new capabilities to users.

For the frame cap, I'm setting it to 2000 for the standalone script since it runs locally and sequentially — each frame is roughly 50KB as base64, so 2000 frames would be around 100MB in memory, which is acceptable, though I'll note that 2000 sequential vision model calls will be slow and warn users about the time cost.

Let me review the current video_tasks.py file to ensure I make precise edits with the exact current text.
太好了，效率确实是抽帧方案的核心优势。你的两个诉求都能实现：

1. **加声音处理**：用已装好的 `faster-whisper`（无需额外装 ffmpeg，它内部用 PyAV 直接从视频解码音轨）转写成文本，并和画面描述一起喂给汇总模型，还能顺手导出 `.srt` 字幕。
2. **可配置抽帧数量 + 用时间换精度**：当前 `frame_interval`/`max_frames` 已可配，但有 64 帧硬上限。我把上限放宽并明确"帧越多越慢越细"。

先重新读一下当前脚本，确保精确改动：
I'm planning the code changes needed: updating the module docstring to note faster-whisper as optional, raising the frame limit with a new constant, adding transcription and SRT writing functions, modifying the summary prompt to work with transcripts, integrating transcription into the main task runner, and updating the report to include a transcript section. Writing transcription function... Now I'm deciding where to place the transcription step in the workflow—it makes sense to run it after frame extraction so progress shows first, then pass the transcript and subtitle file path to the report writer. I'll extract the audio settings from the task config, run the transcription if enabled, and log the results before moving to the summary generation. Updating the docstring to clarify that the tool now reads actual video metadata using OpenCV without external dependencies, extracts frames for visual analysis, optionally transcribes audio with faster-whisper, and generates both a markdown report and SRT subtitles when audio processing is enabled. I'm also noting that faster-whisper is optional but required for audio transcription, and it handles audio decoding internally without needing system ffmpeg.
已确认现状。开始改造 `scripts/video_tasks.py`。

**1) 更新文件说明 + 依赖说明：**
**2) 放宽抽帧上限 + 新增 Whisper 默认模型常量：**
**3) 新增音频转写 + SRT 导出函数**（插在 `ollama_chat` 之后、提示词区之前）：
**4) `summary_prompt` 支持结合音频转写：**
**5) `run_task`：读取音频/帧数配置 + 转写音轨 + 结合声画汇总**：
**6) `write_report` 加入音频转写小节：**
现在更新 `video-tasks.json`，加上音频相关字段和更高的抽帧示例：
校验语法 + lint，并实测 Whisper 在你机器上能否加载（用最小模型 tiny 探测，不实际转写长视频）：
Everything's working so far—the Python compiles, JSON is valid, and the Whisper model loaded successfully. Now I'm running a linter check on the Python file to catch any style or syntax issues.
全部通过：Python 编译 OK、JSON 合法、Whisper 在你机器上成功加载（CPU int8）。最后跑一下 lint：
最后同步更新使用文档 `docs/use/use.md` 的字段表，加入音频与抽帧调优说明：
全部完成并通过验证。

## 本次升级内容

### 🎧 1. 新增声音处理能力（faster-whisper）
- `scripts/video_tasks.py` 新增 `transcribe_audio()` + `write_srt()`：用本地 faster-whisper 转写音轨，**内部用 PyAV 直接从视频解码，无需装 ffmpeg**。
- 转写文本会和逐帧画面描述**一起喂给汇总模型**（`qwen2.5:14b`），实现"声画结合"分析。
- 开启后额外生成 `<视频名>.srt` 字幕，报告里新增「## 音频转写」小节。
- 模型在进程内缓存复用；实测 Whisper 在你机器上加载成功（CPU int8）。

### 🎞️ 2. 抽帧数量可配，时间换精度
- 抽帧上限从 **64 放宽到 2000**，并对超限做保护提示。
- 通过 `max_frames`（调大）或 `frame_interval`（调小）即可抽更多帧，耗时随帧数线性增长。

### 启用方式
在 `video-tasks.json` 里（已帮你把 `72.mp4` 的 `include_audio` 设为 `true`）：

```json
{ "include_audio": true, "whisper_model": "base", "max_frames": 120, "frame_interval": 2 }
```

然后照常运行：
```bash
./venv/bin/python scripts/video_tasks.py
# 或启动时自动跑： RUN_VIDEO_TASKS=1 ./start-video-analysis.sh
```

校验结果：`py_compile` 通过、lint 0 错误、JSON 合法、Whisper 加载成功。文档 `docs/use/use.md` 的字段表与「声音处理/抽帧调优」说明已同步更新。

> 关于你提到的**完整视频理解（Gemini 类）、硬字幕 OCR、动作捕捉（MediaPipe/OpenPose）、模拟学习（Isaac/MuJoCo）**——这些都超出当前"抽帧+VLM"本地管线的范畴，属于独立 CV/RL 体系。如果以后想做**硬字幕 OCR**（PaddleOCR 每 1~2 秒扫一遍画面拼文本），我可以再给你加一个独立步骤，它和现在的音频转写是并列模块，互不影响。需要的话随时说。




FC2-PPV-2763672_1 00_27_00-00_54_00.mp4
FC2-PPV-2763672_1 00_54_00-01_20_00.mp4
FC2-PPV-2763672_1 01_20_00-01_47_53.mp4
FC2-PPV-2763672_2.mp4

./venv/bin/python scripts/video_tasks.py --config docs/json/task-1-4.json --ollama-url http://localhost:11434


I need to refactor the sampling configuration to let users choose between two distinct modes: either a fixed frame count or a time-based interval. The current logic blends both together, but the user wants explicit control over which approach to use. I should also look at how other tools handle frame sampling to inform the design. Refactoring the timestamp computation logic... I need to stay focused on the primary ask: implementing the sampling mode config so users can choose between frame-count or time-interval sampling. I'll add the environment variable guard at the top of the file as they suggested since it's low-risk, then walk through the extract_frames function to wire up the sampling logic properly and surface the effective sampling rate in the logs. For the broader improvements question, I'll mention scene-change detection as a concrete next step but keep the scope tight. Refining extract_frames to handle sample_mode with three strategies: 'count' mode divides duration evenly across max_frames, 'interval' mode uses the specified frame_interval and caps total frames, and 'auto' mode balances both constraints by taking the larger of frame_interval or the even distribution across max_frames. Refining the frame sampling logic...

I'm consolidating the step and limit calculations so that all modes follow a unified loop: compute the step size and maximum frame count upfront, then iterate while staying under both the duration and frame limit. For interval mode, I'm ensuring the step respects the minimum frame interval and caps total frames if needed. For count mode, I'm distributing frames evenly across the duration. Auto mode picks the larger of the two constraints. Then in run_task, I'll log which sampling mode is active with its parameters, and let extract_frames handle the actual frame extraction without redundant warnings.

I'm also adding environment variable setup at the top of the file to suppress the macOS dylib warning before importing cv2.
数据很漂亮！52 分钟视频 + 1 万字转写 6.6 分钟跑完，声画全模态确实跑通了。针对你的核心诉求——**让抽帧策略可配（固定张数 vs 时间跨度）**，我来升级，并顺手把你提到的 dylib 隐患在脚本里挡掉。

**1) 在导入 cv2/av 之前抑制 macOS libav 重复加载告警（消除潜在崩溃风险）：**
**2) 重写抽帧逻辑，支持三种抽样模式：**
**3) `run_task` 读取 `sample_mode` 并按模式抽帧、打印方式说明：**
**4) 更新 `video-tasks.json`，把 `72.mp4` 示例改成"每分钟一帧 + small 模型"，并展示两种模式：**
校验 + lint，并实测三种 `sample_mode` 在你的 `72.mp4`（616s）上的抽帧数量是否符合预期（只抽帧，不调模型）：
The sampling modes are working correctly across different configurations—fixed count gives 16 frames, time-based intervals calculate the expected frame counts based on video duration, and auto mode also produces 16 frames. The interval mode properly ignores the max_frames parameter since it's using time-based sampling instead.

Now I'll run the linter to check for any issues, update the documentation, and then work through the mainstream approaches analysis.
三种模式实测全部符合预期：

| 模式 | 参数 | 616s 视频抽帧数 |
|---|---|---|
| `count` | max_frames=16 | **16 帧**（固定张数） |
| `interval` | 每 60s | **11 帧**（按时长，无视 max_frames） |
| `interval` | 每 30s | **21 帧** |
| `auto` | 旧行为 | **16 帧** |

lint 检查 + 同步更新文档：
全部完成、验证通过（编译 OK、lint 0 错误、JSON 合法、三种模式实测正确）。

## 升级内容

### 抽帧策略现在可配（`sample_mode`）
在 `video-tasks.json` 里每个任务（或顶层默认）写：

| 模式 | 含义 | 例子 |
|---|---|---|
| `"count"` | 固定张数，全片均匀抽 `max_frames` 帧 | `sample_mode:"count", max_frames:16` |
| `"interval"` | **时间跨度**，每 `frame_interval` 秒抽一帧 | `sample_mode:"interval", frame_interval:60`（每分钟一帧） |
| `"auto"` | 旧默认行为（≤max_frames 且不密于 interval） | 兼容用 |

我已把 `72.mp4` 示例改成 `interval` / 每 60s 一帧 + `whisper_model: small`（中文更准）。安全上限 2000 帧，超长自动均匀铺满。

### 顺手修掉的隐患
脚本顶部（导入 cv2/av 之前）加了 `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` 和 `KMP_DUPLICATE_LIB_OK`，抑制你日志里那条 libav 重复加载告警与潜在崩溃。HF 限流警告无需处理（模型已缓存到 `~/.cache/huggingface`，再跑不联网）。

---

## 主流抽帧方案对比（你现在在哪、还能往哪走）

| 方案 | 原理 | 优点 / 代价 | 你的现状 |
|---|---|---|---|
| **均匀抽帧** Uniform | 按固定张数/间隔等距取 | 简单快；会漏场景切换 | ✅ 已支持（count/interval） |
| **关键帧/场景切分** Scene-cut | 用帧间差异(如 PySceneDetect/直方图差)在"画面突变处"取帧 | 抓住每个镜头，几乎不漏剧情；需额外算差异 | ⬜ 可加（最推荐的下一步） |
| **内容去重** Embedding dedup | 对帧算特征(CLIP/感知哈希)，丢掉高度相似帧 | 省模型调用、避免重复描述 | ⬜ 可加 |
| **运动/音频引导** | 在"动作剧烈"或"有人说话"的时间段加密抽帧 | 详略得当；逻辑更复杂 | ⬜ 进阶 |
| **硬字幕 OCR** | 每 1~2s 扫画面用 PaddleOCR 抠字 | 补齐烧录字幕；与音频转写互补 | ⬜ 独立模块可加 |

**最务实的下一步是「场景切分抽帧」**：用 `scenedetect`（或自写帧间直方图差）找镜头边界，每个镜头取 1 帧 + 时间过长再补抽。这样 1 小时视频可能只产生 30~80 个"有信息量"的帧，比固定间隔更聪明，既不漏镜头也不浪费模型调用。

要的话我可以新增一个 `"sample_mode": "scene"`，用帧间差异自动找切换点抽帧——你说一声我就接上。
