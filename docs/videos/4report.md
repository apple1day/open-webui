I'm looking at the video task script to understand what needs to be done.
我先看一下视频分析脚本的实现，了解抽帧、报告保存和日志输出的逻辑。
我理解了这个脚本的功能。用户提出了三个问题：首先是关于抽帧运行的分析，看看帧数提升（从默认16帧到53帧）后需要更新哪些操作；其次是关于输出目录的问题，目前分析报告被强制写入视频同目录，需要支持自定义输出路径；第三个问题似乎是关于在生成日志时直接实现某个功能，但这部分被截断了。

现在我需要查看文档和配置架构来理解上下文，然后为这三个问题提供具体的实现方案。
现在我已经完整理解了脚本。让我看一下相关文档，确认配置项的说明位置。
I'm looking at the user's three requests: the frame extraction is working correctly with 53 frames extracted at 60-second intervals from a 52-minute video, which is the expected behavior. For the custom output directory feature, I need to add support for an `output_dir` parameter so reports and subtitles can be written to a location other than the video's directory.

For real-time display of analysis results, I need to modify the per-frame output to show the actual description content instead of just the frame number and timestamp, and potentially add streaming support for the summary output so results appear as they're generated rather than waiting for completion.

I'm planning the implementation: add `output_dir` resolution in the task runner, update the report and subtitle writing functions to accept a directory parameter, enable real-time frame description printing, and add streaming capability for the summary output so users see results as they're being analyzed.

Now I'm implementing the streaming version of the Ollama chat function...
我已经完整理解了脚本逻辑。下面先给出**分析结论**，再落地三处改进。

## 分析结论

针对你这次运行 `task-1-5cout.json`（52 分钟视频、`interval` 模式每 60s 一帧 → 抽出 53 帧）：

| 你的诉求 | 当前代码现状 | 需要更新 |
|---|---|---|
| ① 抽帧提升数量 | `interval` 模式工作正常，53 帧符合预期（`limit = 时长//60 + 1`，硬上限 2000） | 无需改，运行正确 |
| ② 报告不存视频同目录、能否填自定义目录 | `write_report`/`write_srt` 用 `os.path.splitext(video_path)` **强制写到视频同目录**，无自定义目录入口 | 需新增 `output_dir` 配置项 |
| ③ 日志实时显示分析结果 | 逐帧只打印 `帧 i/n @ ts`，**不显示识别内容**；汇总用 `stream=False` 一次性返回，过程中看不到 | 需打印逐帧描述 + 汇总流式输出 |

下面落地改动。

### 改动 1：`ollama_chat` 增加流式支持
### 改动 2：`run_task` 解析 `output_dir`，逐帧实时显示描述，汇总流式输出
Now the per-frame loop to show descriptions in real-time:
Now the summary step (stream output) and pass `output_dir` to writers:
I need to refactor the `write_srt` function call to include the output directory parameter, then update both `write_srt` and `write_report` functions to accept and use that directory.
Now update SRT writing call and the `write_srt`/`write_report` functions to honor `output_dir`:
No lint errors. Now let me update your config file to demonstrate `output_dir` and update the docs.
Now update the docs to describe the two new fields:
I need to clarify that `output_dir` and `stream_log` are also valid top-level defaults alongside the other documented ones, and I should update the documentation around line 113-114 to reflect this.
Now let me verify the final state of the modified `run_task` section to make sure everything is consistent:
All changes are in place and consistent.

## 完成总结

我按你的三个诉求改了 `scripts/video_tasks.py`（并同步更新了 `docs/use/use.md` 和你的 `task-1-5cout.json`）：

**① 抽帧数量** — 你这次 53 帧是 `interval` 模式正常表现（`时长//60 + 1`），代码无需改。若想更细可：调小 `frame_interval`（如 30），或换 `count` 模式调大 `max_frames`，硬上限 2000。

**② 自定义输出目录** — 新增 `output_dir` 配置项（顶层默认 + 单任务可覆盖）：
- `null`／不填 → 维持旧行为，报告/字幕写到**视频同目录**；
- 填路径（支持 `~`，自动创建）→ 报告 `.analysis.md` 和字幕 `.srt` 统一写到该目录，文件名仍用视频主名。

**③ 日志实时显示分析结果** — 新增 `stream_log`（默认 `true`）：
- 逐帧识别时，模型描述**流式逐字**打印到终端（暗色缩进），不再只显示 `帧 i/n @ ts`；
- 汇总阶段同样**流式输出**，可实时看到结论生成过程；
- 设为 `false` 可回到只打印进度行的安静模式。

底层 `ollama_chat` 增加了 `stream=True` + `on_delta` 回调（解析 Ollama 的 NDJSON 流），非流式调用行为完全不变。

直接重跑即可看到效果：

```bash
./venv/bin/python scripts/video_tasks.py --config docs/json/task-1-5cout.json --ollama-url http://localhost:11434
```

如果要把报告集中输出，比如改到 `docs/reports/`，在 JSON 顶层加 `"output_dir": "~/code/code_deal/my-webui/docs/reports"` 即可。需要我帮你直接在配置里设一个具体的输出目录吗？