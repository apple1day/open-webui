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