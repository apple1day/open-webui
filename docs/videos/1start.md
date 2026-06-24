I need to understand the full scope here: building an offline video analysis system using a local LLM through Ollama and Open WebUI, with the main implementation going into the `code_deal/open-webui` project and documentation saved to `22.md`. Let me start by examining the existing solution in `11.md` and then explore how to integrate local video processing capabilities.
先了解现有方案内容、`22.md` 当前状态，以及 open-webui 项目结构，再制定改造方案。
I see that 22.md is empty, so I'm going to look through the open-webui project structure to figure out how to connect video analysis capabilities with ollama.
I need to dig into the backend to see how ollama is currently integrated and figure out where video analysis capabilities would fit in.
I'm diving into the open-webui project to understand how the ollama router works so I can add a video analysis feature. I need to examine the ollama.py router to see how it communicates with ollama, check main.py to understand router registration, and review requirements.txt to see what libraries are available for video processing.
I need to understand how the ollama router handles vision models for multimodal chat, since the user is asking about video analysis. Vision models like llava and qwen2-vl can process images, so video analysis would involve extracting frames and passing them to these models. Let me examine the ollama.py router, the chat completions API, and how images are currently handled in requests, plus check what dependencies are available.
The project already has the video processing libraries I need—opencv, av, and pillow—plus ollama integration. I'm planning to build a new backend router that handles video analysis by accepting a local video path, extracting keyframes with opencv or av, and encoding the frames to base64 for the vision model.

Now I need to understand how the ollama router exposes its generate and chat endpoints, and how main.py registers routers so I can integrate this properly. I should also check how the OLLAMA_BASE_URLS configuration is accessed and how generate_chat_completion works in the existing ollama.py module. The user mentioned putting code changes in package.json, but that's just the npm manifest—the actual implementation should go in the open-webui backend with documentation in 22.md.
Let me try searching for different patterns like "router =" and function definitions to locate where the routing logic is defined in the ollama.py file.
The search tool isn't finding matches, so I'm reconsidering the approach. I think the issue is that the directory parameter needs an actual directory path rather than a file, so I should point it to the routers folder instead of the ollama.py file I tried earlier.
The search tool seems to be returning empty results across the board—could be an issue with ripgrep not handling this directory properly, maybe it's gitignored or there's a virtual environment in the way. Let me run a basic search to verify the tool is actually working.
The backend folder is being skipped by ripgrep due to gitignore rules, so I'll read the ollama.py file directly to find the chat completion endpoint. Rather than searching through the whole file, I'm going to build a self-contained router that communicates with ollama over HTTP.
I'm wondering if `code_deal` might be gitignored at the workspace root, which would explain why ripgrep isn't finding it and why the project layout didn't expand it. Let me check the workspace .gitignore to see if that's the case.
ripgrep 受 `.gitignore` 影响搜不到该目录，我用命令行强制搜索。
I'm looking at how `generate_chat_completion` retrieves the ollama URL and examining the configuration for OLLAMA_BASE_URLS, along with how the router state manages these URLs.
I see that the ollama chat endpoint accepts base64-encoded images in the ChatMessage, so for video analysis I'd extract frames, encode them, and send them through the `/api/chat` endpoint with the images attached, then combine the results. Now I need to check the config to understand the OLLAMA_BASE_URLS setup and how main.py registers routers and stores the config in app.state.

config, particularly looking at the status/config section and the helper functions like `_resolve_api_config` and `get_api_key`.
I need to look at how main.py sets up the routers and initializes app.state.config so I can properly register my new router alongside the existing ones.
I'm setting up a new router for video analysis that accepts a local video path, model name, and optional parameters like frame sampling and max frame count. It'll extract frames using OpenCV, encode them as base64 JPEGs, then send each frame to the ollama vision model to generate per-frame descriptions.

After collecting all frame analyses, I'll aggregate them into a final summary via a text or vision model and return structured JSON with the option to save a markdown report. I'm planning to use `get_session` and the configured ollama base URLs for direct calls, keeping it self-contained.

For the endpoints, I'll create POST `/api/v1/video/analyze` that takes video_path, model, prompt, frame_interval, max_frames, save_report, and language parameters, returning frames data, summary, and report path if generated. I'll also add a GET `/api/v1/video/health` for a quick status check. To handle paths flexibly, I'll support both absolute paths and paths relative to UPLOAD_DIR.

I'll use the first configured base URL with an optional override in the request for simplicity and offline compatibility, then write out the complete module.
理解了整个架构。现在我清楚 open-webui 的后端结构、ollama 路由（`/api/chat` 支持 `images` base64 多模态）、可用依赖（`opencv-python-headless`、`av`、`pillow` 已在 requirements 里）。

下面我新增一个**离线本地视频分析路由**，复用本地 ollama 视觉模型抽帧分析，并注册到 `main.py`。

先创建后端路由文件：
Now register the router in `main.py`:
I'm checking that the session_pool module exports get_session correctly and verifying it's being used with await in ollama.py. Now I need to run a quick syntax check to make sure the Python compiles.
Let me verify the new module compiles cleanly:
Let me verify that main.py compiles after adding the import, using py_compile to check the syntax without needing to load all the dependencies.
Now I'll write the documentation to `22.md`:
完成。我已基于 `11.md` 私域大模型方案，在 open-webui 项目里落地了一套**完全离线的本地视频分析能力**（本机 Ollama 视觉模型 + 抽帧理解），并把方案文档写入 `22.md`。

## 改动总览

**1. 新增后端路由 `backend/open_webui/routers/video_analysis.py`**
- 读取本地视频（支持绝对路径 / 相对 `UPLOAD_DIR`，带目录穿越防护）
- OpenCV 抽帧（`asyncio.to_thread` 不阻塞事件循环，均匀采样、长边降采样到 768px、JPEG+base64）
- 逐帧调用本机 Ollama `/api/chat`（`images=[base64]`，复用 `ChatMessage.images` 多模态能力）
- 用 `summary_model` 把逐帧描述融合为结构化报告（概述/时间线/看点/标签），可落盘 `<视频名>.analysis.md`
- 暴露 3 个接口：`GET /health`、`GET /models`、`POST /analyze`，统一挂在 `/api/v1/video`

**2. 注册路由 `backend/open_webui/main.py`**
- 导入块追加 `video_analysis`
- 在 audio 路由后注册：`app.include_router(video_analysis.router, prefix='/api/v1/video', tags=['video'])`

**3. 方案文档 `python2/视频私域大模型方案/22.md`**
- 含架构图、离线环境准备（ollama + 视觉模型）、接口契约、curl 调用示例、性能建议、与 `11.md` 的对应关系。

代码已通过 `py_compile` 与 lint 校验，无新增错误。

## 关于改动位置的说明
你提到"主要代码改动放在 `package.json`"——`package.json` 只是前端依赖清单，无法承载这类后端逻辑。本次实际改动落在它所属的 **open-webui 项目后端**（`backend/open_webui/`），这是正确且唯一合理的位置；前端依赖无需变动。所需依赖（`opencv-python-headless`、`pillow`、`av`）也都已在 `backend/requirements.txt` 中。

## 快速验证
```bash
ollama serve && ollama pull qwen2.5-vl:7b
cd code_deal/open-webui && source ./venv/bin/activate && open-webui serve --port 9102
curl -s http://localhost:9102/api/v1/video/analyze -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"video_path":"/path/demo.mp4","model":"qwen2.5-vl:7b","language":"zh"}' | jq
```

需要的话，我可以继续：把 `/analyze` 改成 SSE 流式回传进度、加并发抽帧推理、或接 `faster-whisper` 融合音轨理解。
好的，方案与代码都已就绪。简单回顾一下交付内容：

- **后端路由** `backend/open_webui/routers/video_analysis.py`：本地视频抽帧 → Ollama 视觉模型逐帧理解 → 汇总成结构化报告，全程离线。
- **注册** 已加到 `backend/open_webui/main.py`，接口挂在 `/api/v1/video`。
- **文档** 已写入 `python2/视频私域大模型方案/22.md`。

如果你接下来要实际跑起来，建议先做这两步快速验证：

1. 起服务并拉模型
```bash
ollama serve && ollama pull qwen2.5-vl:7b
cd code_deal/open-webui && source ./venv/bin/activate && open-webui serve --port 9102
```

2. 探测能力（确认 OpenCV 可用）
```bash
curl -s http://localhost:9102/api/v1/video/health -H "Authorization: Bearer $TOKEN" | jq
```

需要我继续做以下任一项，随时说：
- 改成 SSE 流式回传分析进度
- 逐帧推理改并发提速
- 接入 `faster-whisper` 融合音轨理解
- 在前端加一个「视频分析」面板入口

I need to implement four enhancements: SSE streaming for the /analyze endpoint, concurrent frame processing, integrating faster-whisper for audio, and adding a video analysis panel to the frontend. Let me start by exploring the frontend structure to see how to add this new panel, then check how faster-whisper is used in the audio module and understand the current API client setup.
全部实现。先摸清前端结构和现有 `faster-whisper` 用法，再动手。
I need to explore the audio API client structure and the routes directory to figure out where to add a new frontend page for the audio functionality.
I'm checking the constants file for the API base URL configuration and examining how the audio router integrates faster-whisper along with the overall routes structure.
I'm checking how faster-whisper is used in the audio.py file to see if I can set up local transcription.
I'm looking at how the transcription endpoint handles the model around lines 620-660 so I can replicate that behavior locally when processing audio extracted from video.
I can use PyAV to extract audio from video files into a temporary wav or mp3, then pass it to faster-whisper for transcription. Actually, faster-whisper's transcribe method accepts file paths directly, so I can simplify this workflow.

Since faster-whisper already uses PyAV internally to decode audio from any media file including video, I can just pass the video path directly to the model without extracting audio separately. I'll reuse the existing `set_faster_whisper_model` and `request.app.state.faster_whisper_model` for this.

Now I need to look at the routes structure to add a frontend page and update the sidebar with a new entry.
I'm exploring the page structure by looking at an example calendar page, then tracing through the sidebar component to understand where navigation links are defined and how to add a new one.
I'm looking at the sidebar structure to understand how the pinned items and navigation entries are configured, then I'll add a "Video Analysis" option to the menu.
I'm examining how the icon rendering logic branches based on the iconType parameter in that section of the code.
The sidebar approach requires changes across multiple places—the pinned items config, visibility logic, metadata, and icon rendering in both collapsed and expanded states—which is doable but risky to modify. A safer alternative would be adding a standalone `/video` route page instead.

I'm planning the full implementation: the backend needs concurrency control for frame inference using a semaphore, an SSE streaming endpoint to yield progress events, and audio transcription via faster-whisper that feeds into the summary. On the frontend, I'll create an API client, add the video route constant, build the panel UI page, and add the sidebar menu entry. I'll start by rewriting the backend router with concurrency, SSE streaming using JSON lines format, and audio transcription support.

For the core logic, I'm designing an async helper function that runs the analysis steps and accepts an optional progress callback for events. The SSE endpoint will use this with a callback that emits streaming events, while the regular JSON endpoint returns the full result. For ordered frame results with concurrency limits, I'll use asyncio.gather with a semaphore and collect results by index.

Writing the backend router...
现在我清楚了所有结构。开始实现。先重写后端路由，加入**并发抽帧推理、SSE 流式进度、faster-whisper 音轨理解**：
Backend done. Now the frontend. Add the API base URL constant, API client, route page, and sidebar entry.
I'm checking an existing page like the calendar to understand the structure and see what imports I need, like i18n and stores.
Now let me check an existing page's structure to match conventions:
I'm building the video analysis page as a self-contained Svelte component with a form for input, a model selector dropdown, SSE-based progress tracking, and result rendering—keeping it simple by displaying the summary text in a styled container rather than adding complex markdown dependencies.
I'm checking that the SidebarIcon component exists at the expected path and verifying the Spinner component accepts the className prop that the calendar page is using.

Now I need to add the video menu entry to the sidebar. I'll update DEFAULT_PINNED_ITEMS to include 'video' alongside the existing 'notes' and 'workspace', add a case for 'video' in the isMenuItemVisible function to make it visible to logged-in users, add the video entry to getMenuItemMeta, and then add the corresponding icon rendering in both places where the icon switch blocks appear.
Verify `Spinner` props and read the second icon block in the sidebar:
Now I'll make the sidebar edits. First the data definitions:
Now add the video icon to both icon switch blocks:
Now check lints on all changed frontend files:
The lints are clean and the SidebarIcon import path checks out since the calendar page already uses it. Now I need to update the documentation to mark those four capabilities as implemented rather than future work, and add usage examples for the SSE stream, concurrency, and audio features.

I'll make targeted edits to the doc: updating the code changes table to include concurrency, SSE, and audio parameters, adding the new `/analyze/stream` endpoint, documenting the request fields, and adding a frontend usage section. Then I'll update section 7 to reflect that these are now complete.
全部前后端改动通过 lint。最后更新方案文档 `22.md`，把这四项新增能力写进去:
Now update the usage section (add SSE/audio examples + frontend) and rewrite the "future work" section since these are now done:
Final compile check on backend and lint on touched files:
四项增强全部完成，编译与 lint 均通过。

## 本次新增/改动汇总

### 后端 `backend/open_webui/`
- **`routers/video_analysis.py`（重写）**
    - **并发抽帧推理**：`asyncio.Semaphore` 控制并发度（`concurrency`，上限 8），多帧并行调用 Ollama，结果按帧序回收。
    - **SSE 流式进度**：新增 `POST /analyze/stream`，通过 `asyncio.Queue` 实时推送 `extract/extracted/audio/frame/summarize/result/error` 事件。
    - **音轨理解**：`include_audio=true` 时复用 `audio.py` 的 `set_faster_whisper_model` 与 `app.state.faster_whisper_model`，本地转写并与抽帧推理**并行**，转写文本融合进汇总报告。
    - 报告 `.analysis.md` 增加「音频转写」分区。
- `main.py`：已注册路由（上一轮完成）。

### 前端 `src/`
- `lib/constants.ts`：新增 `VIDEO_API_BASE_URL`。
- `lib/apis/video/index.ts`（新增）：`getVideoHealth` / `getVideoModels` / `analyzeVideo` / `analyzeVideoStream`（解析 SSE 流、逐事件回调）。
- `routes/(app)/video/+page.svelte`（新增）：完整「视频分析」面板——路径输入、模型与参数选择（间隔/帧数/并发/语言/音轨/whisper）、实时进度条与日志、报告/转写/逐帧分区渲染。
- `lib/components/layout/Sidebar.svelte`：侧边栏新增 `video` 入口（`DEFAULT_PINNED_ITEMS`、`isMenuItemVisible`、`getMenuItemMeta` 及两处图标 switch）。

### 文档
- `python2/视频私域大模型方案/22.md`：更新接口、请求/响应、SSE 事件说明、前端面板用法、性能建议（含 `concurrency`），并标注四项能力已实现。

## 快速验证
```bash
# 后端
cd code_deal/open-webui && source ./venv/bin/activate && open-webui serve --port 9102
# 前端
npm install && npm run dev

# SSE 流式分析（含音轨）
curl -N http://localhost:9102/api/v1/video/analyze/stream \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"video_path":"/path/demo.mp4","model":"qwen2.5-vl:7b","concurrency":4,"include_audio":true}'
```

登录后左侧栏点击 **Video Analysis**（或访问 `/video`）即可使用面板。

注意事项：音轨转写依赖本机已下载的 faster-whisper 模型（首次会按 `whisper_model` 拉取，离线环境需提前缓存到 `WHISPER_MODEL_DIR`）。

I need to understand how the backend and frontend start up so I can create a unified startup script. Let me check the existing documentation and package configuration to see what commands are currently used.
先确认项目的启动方式（后端、前端、依赖位置），再生成一键脚本。
I'm checking what's in the project directory—looking for the virtual environment, environment variables file, and any existing startup scripts.
I need to look at the environment configuration and startup scripts to understand how the backend launches.
I'm noticing the .env file has a formatting issue where lines 24-26 use backslash continuation, which causes the DATABASE_URL to include the command itself — that's technically malformed for environment files but that's what's there. Now I need to look at the backend startup scripts to see how they're handling this.
I'm seeing the architecture now — Ollama running the vision model locally, open-webui as the backend server, and Vite for the frontend dev environment. There's also a malformed `.env` file where the backslash continuation on lines 24-26 is breaking the `DATABASE_URL` value.

I should create a clean one-click bash script that handles all three services: starting Ollama, activating the Python virtual environment and launching the backend on port 9102, and spinning up the frontend dev server. The tricky part is figuring out whether to run in production mode (where the backend serves the built frontend) or dev mode (where Vite runs separately on 5173), since the new `/video` page needs to be built and tested.
In dev mode, the frontend running on Vite at port 5173 actually calls the backend on port 8080, not 9102—this is hardcoded in the constants file. So to test the new /video page locally, I need to make sure the backend is running on port 8080.

I'm weighing two approaches: dev mode with hot reload (backend on 8080, frontend on 5173) which is best for quickly seeing changes, or production mode where I build the frontend and serve everything together from one port. The dev approach is cleaner for testing, but the user's existing .env configuration uses port 9102 with MySQL, and there's also an issue with the DATABASE_URL being malformed. I'm thinking the most practical solution is to create a flexible script that defaults to dev mode with the backend on 8080, but I need to handle the .env configuration issue first. Writing the startup script...
I should verify that uvicorn and open-webui are actually installed in the virtual environment's bin directory.
The venv is set up with open-webui and uvicorn, but node_modules is missing so I need to add npm install to the setup script for a smooth one-click experience. The backend runs on port 8080 while the frontend dev server on 5173 connects to it there.

I'm going to write a comprehensive bash script that handles the full startup: checking if ollama is running and starting it if needed, verifying the vision model exists, activating the venv and launching the backend with logging and health checks, installing dependencies and starting the frontend dev server, then opening the browser with cleanup handlers to shut everything down gracefully. I need to be careful about the malformed .env file potentially breaking the backend with MySQL configuration, so I'll either override the DATABASE_URL with a local sqlite path or let their existing env take precedence. The .env file actually parses those MySQL URLs correctly despite the line continuations, so the backend would attempt to connect to MySQL and crash if it's not running. I'll make the script default to SQLite by exporting `DATABASE_URL` before loading the .env, since exported environment variables take precedence over dotenv values. I can add a `USE_MYSQL=1` flag for users who want to keep their MySQL setup. I'll also set `BACKEND_PORT` to 8080 by default to match the frontend constant, and include a `MODE=prod` option for production builds, but keep the focus on dev mode for testing. Now I'll write the script and make it executable.
已经了解全部启动要素。关键点：

- **后端**：`venv` 已装好 `open-webui`/`uvicorn`；
- **前端**：`node_modules` **缺失**，需先 `npm install`；
- **端口约束**：`src/lib/constants.ts` 第 6 行在 dev 模式下把后端地址**硬编码为 `:8080`**，所以 dev 模式下后端必须跑在 `8080`，前端 vite 在 `5173`；
- **Ollama**：需本机 `ollama serve` + 视觉模型；
- **数据库**：`.env` 里是 MySQL，为做到真正一键免依赖，脚本默认用 SQLite（可用 `USE_MYSQL=1` 切回）。

下面生成一键启动脚本：
已生成一键启动脚本：`code_deal/open-webui/start-video-analysis.sh`（已 `chmod +x`，语法校验通过）。

## 直接运行

```bash
cd /Users/even/code/code_deal/open-webui
./start-video-analysis.sh
```

启动后会自动打开浏览器 `http://localhost:5173`，左侧栏点 **Video Analysis**（或访问 `/video`）即可看效果。`Ctrl+C` 一键停止全部服务。

## 脚本做了什么（4 步全自动）

1. **Ollama**：检测是否在跑，没跑就 `ollama serve`，并检查视觉模型 `qwen2.5-vl:7b`；
2. **后端**：激活 `venv`，dev 模式用 `uvicorn ... --reload` 跑在 **8080**（前端 dev 写死连 `:8080`，故不能改）；
3. **前端**：`node_modules` 缺失时自动 `npm install`，再 `npm run dev` 跑在 **5173**；
4. **健康检查**：逐个等 `/health`、端口就绪后才提示访问地址，日志写到 `logs/`。

## 几个开关（环境变量）

| 用法 | 作用 |
|------|------|
| `./start-video-analysis.sh` | 默认 dev 模式，热重载，最适合看效果 |
| `MODE=prod ./start-video-analysis.sh` | 构建前端 + 后端单端口 `9102` |
| `USE_MYSQL=1 ./...` | 沿用 `.env` 的 MySQL（默认用 SQLite，免依赖） |
| `PULL_MODEL=1 ./...` | 视觉模型缺失时自动 `ollama pull` |

## 两点提醒

- **首跑较慢**：`node_modules` 当前缺失，第一次会执行 `npm install`（依赖很多，请耐心等）。
- **视觉模型**：离线环境若本机没有 `qwen2.5-vl:7b`，脚本只会告警不阻塞；要先用模型分析视频，请先 `ollama pull qwen2.5-vl:7b`（或加 `PULL_MODEL=1`）。

需要我把它改成 `prod` 默认、或顺便加上音轨用的 `faster-whisper` 模型预下载检查吗？
