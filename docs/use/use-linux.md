# 本地大模型 · 视频分析使用文档（Linux / TencentOS Server）

本文为 macOS 版 `use.md` 的 Linux 实测对应版本。本项目在 Open WebUI 基础上集成「**本地离线视频分析**」：用本机 Ollama 视觉模型对本地视频抽帧识别画面，再用文本模型汇总成报告，全程离线、不上传。

> 关键前提：本机已安装并运行 **Ollama**，且至少有一个**视觉模型**（多模态，能看图）。普通文本模型（如 `qwen2.5:14b`）只能做文字汇总，**看不了画面**。

> 本机当前环境（已就绪）：
> - OS：TencentOS Server 3.2（类 RHEL 8，x86_64）
> - 视觉模型：`qwen2.5-vl:7b`；汇总模型：`qwen2.5:14b`（`ollama list` 可查）
> - 数据库：用 **SQLite**（`USE_MYSQL=0`），免外部依赖
> - Node：20.19.0（路径 `/root/.cbruntime/runtimes/node/20.19.0/bin`）；uv：`~/.local/bin`；ffmpeg：`/usr/local/bin`
> - **MLX 后端是 Apple Silicon 专属，本环境不适用**，统一走 Ollama 后端

---

## 〇、先决：把工具加进 PATH（每个新终端都要）

Linux 上 Node/ffmpeg/uv 不在系统默认 PATH，启动前先注入，否则 `npm` 会回退到系统旧版报 `EBADENGINE`：

```bash
export PATH="/root/.cbruntime/runtimes/node/20.19.0/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
node -v && ollama --version      # 自检
```

---

## 一、启动方式

统一入口脚本：`./start-video-analysis.sh`。脚本依次：检查 Ollama → 准备后端 venv → 启动后端 → 启动前端。

> 与 macOS 的差异：① 本环境默认用 **SQLite**（加 `USE_MYSQL=0`）；② 远程/沙箱里用 **`setsid` 后台常驻**，避免会话结束被回收。

### 1.1 后台常驻启动（推荐，SQLite + dev 热重载）

```bash
cd /data/workspace/my-webui
mkdir -p logs
setsid bash -c 'USE_MYSQL=0 \
  PATH="/root/.cbruntime/runtimes/node/20.19.0/bin:/usr/local/bin:$HOME/.local/bin:$PATH" \
  ./start-video-analysis.sh > logs/start.log 2>&1' < /dev/null >/dev/null 2>&1 &
disown
```

### 1.2 前台启动（Ctrl+C 即停，适合本地调试）

```bash
USE_MYSQL=0 ./start-video-analysis.sh              # SQLite + dev 热重载
MODE=prod USE_MYSQL=0 ./start-video-analysis.sh    # 生产模式，后端单端口对外
```

- dev 模式：前端 `http://localhost:5173`、后端 `http://localhost:9102`（端口占用自动顺延）
- prod 模式：单端口 `http://localhost:9102`
- 远程服务器访问需放通端口，或 SSH 转发：
  ```bash
  ssh -L 5173:localhost:5173 -L 9102:localhost:9102 user@host
  ```

### 1.3 常用可选参数（环境变量）

| 变量 | 默认 | 本环境建议 / 说明 |
|---|---|---|
| `MODE` | `dev` | `dev`(热重载) / `prod`(单端口) |
| `USE_MYSQL` | `1` | **本环境用 `0`**（SQLite，免外部依赖） |
| `BACKEND_PORT` | `9102` | dev 后端起始端口，占用自动累加 |
| `FRONTEND_PORT` | `5173` | dev 前端起始端口，占用自动累加 |
| `PROD_PORT` | `9102` | prod 单端口 |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama 地址 |
| `VISION_MODEL` | `qwen2.5-vl:7b` | 本机已就绪 |
| `PULL_MODEL` | `0` | `1` 时缺视觉模型自动 `ollama pull` |
| `RUN_VIDEO_TASKS` | `0` | `1` 时启动自动执行预定义任务（见第二节） |
| `VIDEO_TASKS_FILE` | `video-tasks.json` | 自定义任务文件路径 |

日志：`logs/start.log`、`logs/backend.log`、`logs/frontend.log`。
查看（去颜色码）：`sed 's/\x1b\[[0-9;]*m//g' logs/start.log | tail -20`

### 1.4 验证已启动

```bash
curl -sf http://localhost:9102/health                              # {"status":true}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/    # 200
ss -ltn | grep -E ':(5173|9102|11434)'
```

### 1.5 停止服务

```bash
pkill -f 'start-video-analysis'; pkill -f 'uvicorn open_webui'; pkill -f 'vite'
```

---

## 二、执行「分析视频」任务

两条路：**(A)(B) 用代码任务由脚本运行**；**(C) 在网页面板里操作**。

> Linux 注意：`video_path` 一律用 **Linux 绝对路径**（如 `/data/workspace/videos/demo.mp4`），不要用 macOS 的 `/Users/xxx/...`。

### 任务写在哪里？——`video-tasks.json`

项目根目录的 `video-tasks.json` 即「把任务写进代码」的地方（本环境示例，已改为 Linux 路径与已装模型）：

```json
{
  "ollama_url": "http://localhost:11434",
  "default_model": "qwen2.5-vl:7b",
  "default_summary_model": "qwen2.5:14b",
  "language": "zh",
  "include_audio": false,
  "whisper_model": "base",
  "whisper_language": null,
  "tasks": [
    {
      "video_path": "/data/workspace/videos/demo.mp4",
      "model": "qwen2.5-vl:7b",
      "summary_model": "qwen2.5:14b",
      "language": "zh",
      "sample_mode": "interval",
      "frame_interval": 60,
      "max_frames": 16,
      "include_audio": true,
      "whisper_model": "small",
      "prompt": null,
      "save_report": true
    }
  ]
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `video_path` | 本地视频**绝对路径**。格式：mp4/mov/mkv/avi/webm/flv/m4v/mpg/mpeg/wmv/ts |
| `model` | 视觉模型（须能看图，本机为 `qwen2.5-vl:7b`） |
| `summary_model` | 汇总文本模型；留空＝同 `model`（本机 `qwen2.5:14b`） |
| `language` | `zh` / `en` 输出语言 |
| `sample_mode` | 抽帧策略：`count`(固定张数) / `interval`(按时间) / `scene`(镜头切换) / `auto`(默认) |
| `frame_interval` | 抽帧间隔（秒）。`interval` 模式＝每隔几秒抽一帧 |
| `max_frames` | 抽帧张数。`count`/`auto` 生效；安全上限 2000。调大＝更细更慢 |
| `include_audio` | 是否转写音轨，默认 false |
| `whisper_model` | 语音模型：`tiny`/`base`/`small`/`medium`/`large-v3`（中文建议 `small`+） |
| `whisper_language` | 强制语种；`null` 自动检测 |
| `prompt` | 自定义逐帧提示词；`null` 用内置 |
| `save_report` | 是否生成 `<视频名>.analysis.md`（默认视频同目录） |
| `output_dir` | 报告/字幕输出目录；`null`＝视频同目录 |
| `stream_log` | 终端实时打印逐帧与汇总（默认 `true`） |

#### 抽帧策略

| 模式 | 行为 | 适用 |
|---|---|---|
| `count` | 全片均匀抽 `max_frames` 帧 | 固定开销、快速预览 |
| `interval` | 每 `frame_interval` 秒抽 1 帧 | 长视频、不漏场景 |
| `scene` | 镜头切换检测（`scene_probe` 探测、`scene_threshold` 阈值） | 多镜头按剧情抽帧 |
| `auto` | ≤ `max_frames` 且不密于 `frame_interval` | 折中兼容 |

例：1 小时视频每分钟一帧 → `"sample_mode":"interval","frame_interval":60`（约 60 帧）。耗时随帧数近似线性增长。

#### 声音处理

- 任务 `include_audio` 设 `true`，用本地 **faster-whisper**（PyAV 直接解码音轨）转写台词，和画面描述一起喂汇总模型，并生成 `<视频名>.srt`。
- 中文错字多就把 `whisper_model` 从 `base` 提到 `small`/`medium`。
- 报告含：主要参数（时长/分辨率/帧率/编码/总帧数/大小）+ 结构化分析 + 音频转写 + 逐帧描述。

---

### 方式 A：启动 Web 时自动跑任务

```bash
RUN_VIDEO_TASKS=1 USE_MYSQL=0 ./start-video-analysis.sh
```

启动后端/前端前，先把 `video-tasks.json` 任务逐个跑完（直连 Ollama），再照常起 Web。

### 方式 B：只跑任务不开 Web（推荐批量分析）

```bash
# 默认 video-tasks.json
./venv/bin/python scripts/video_tasks.py

# 指定任务文件 / 覆盖 Ollama 地址
./venv/bin/python scripts/video_tasks.py --config video-tasks.json --ollama-url http://localhost:11434
```

完成后在各视频同目录生成 `.analysis.md`。

### 方式 C：网页面板交互

1. 启动服务后浏览器打开 `http://localhost:5173`（prod 为 `http://localhost:9102`；远程用 SSH 转发）。
2. 左侧栏点 **「Video Analysis」**（摄像机图标）或访问 **`/video`**。
3. 填写：**Local video path**（Linux 绝对路径）、**Vision model**（`qwen2.5-vl:7b`）、**Summary model**（`qwen2.5:14b`），可调抽帧间隔/最大帧数/并发/语言/是否转音轨/是否保存报告。
4. 点 **「Analyze video」**，右侧实时显示进度与报告。

> ⚠️ 不要在普通聊天框里输入视频路径让模型分析——聊天模型读不到本机文件。要分析视频请用 `/video` 面板或代码任务。

---

## 三、速查

| 需求 | 命令 |
|---|---|
| 后台常驻启动（SQLite） | 见 1.1 的 `setsid` 命令 |
| 前台启动（SQLite + dev） | `USE_MYSQL=0 ./start-video-analysis.sh` |
| 单端口生产模式 | `MODE=prod USE_MYSQL=0 ./start-video-analysis.sh` |
| 启动并自动跑任务 | `RUN_VIDEO_TASKS=1 USE_MYSQL=0 ./start-video-analysis.sh` |
| 只跑任务不开 Web | `./venv/bin/python scripts/video_tasks.py` |
| 网页交互分析 | 打开 `http://localhost:5173/video` |
| 查看已装模型 | `ollama list` |
| 缺视觉模型时拉取 | `ollama pull qwen2.5-vl:7b` |

依赖确认（应全部 OK）：

```bash
./venv/bin/python -c "import cv2, faster_whisper, aiomysql, pymysql; print('deps OK')"
```

> Linux 特有提醒：
> - 每开新终端先执行 〇 节的 `export PATH=...`，否则 `npm`/`ffmpeg` 找不到。
> - 后端若报 `chromadb requires sqlite3 >= 3.35.0`，见 `setup-linux.md` 第 4 节（pysqlite3 修复）。
> - 本环境用 SQLite，所有启动命令都带 `USE_MYSQL=0`。



./venv/bin/python scripts/video_tasks.py --config docs/json/yun-1.json --ollama-url http://localhost:11434
