# 本地大模型 · 视频分析使用文档

本项目在 Open WebUI 基础上集成了「**本地离线视频分析**」能力：用本机 Ollama 视觉模型（如
`minicpm-v` / `llava` / `qwen2.5-vl`）对本地视频抽帧识别画面，再用文本模型汇总成报告。
全程离线，不联网、不上传。

> 关键前提：本机已安装并运行 **Ollama**，且至少有一个**视觉模型**（能看图片的多模态模型）。
> 普通文本模型（如 `qwen2.5:14b`）只能做文字汇总，**看不了画面**。

---

## 一、启动方式

统一入口脚本：`./start-video-analysis.sh`（macOS / Linux）。脚本会依次：
检查 Ollama → 准备后端 venv → 启动后端 → 启动前端。

### 1.1 默认启动（dev 热重载 + MySQL）

```bash
./start-video-analysis.sh
```

- 后端：`http://localhost:9102`（端口被占用会自动顺延到 9103…）
- 前端：`http://localhost:5173`（被占用自动顺延，前端会自动连到实际后端端口）
- 数据库：默认使用 `.env` 里的 **MySQL**
- 访问地址：浏览器打开 **`http://localhost:5173`**

### 1.2 生产模式（构建前端，单端口对外）

```bash
MODE=prod ./start-video-analysis.sh
```

- 构建前端后由后端单端口提供：**`http://localhost:9102`**
- 访问地址：浏览器打开 **`http://localhost:9102`**

### 1.3 常用可选参数（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MODE` | `dev` | `dev`(热重载) / `prod`(单端口) |
| `USE_MYSQL` | `1` | `1` 用 `.env` 的 MySQL；`0` 改用 SQLite（免外部依赖） |
| `BACKEND_PORT` | `9102` | dev 后端起始端口，被占用自动累加 |
| `FRONTEND_PORT` | `5173` | dev 前端起始端口，被占用自动累加 |
| `PROD_PORT` | `9102` | prod 单端口 |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama 地址 |
| `VISION_MODEL` | `qwen2.5-vl:7b` | 推荐拉取的视觉模型名 |
| `PULL_MODEL` | `0` | `1` 时若缺视觉模型自动 `ollama pull` |
| `RUN_VIDEO_TASKS` | `0` | `1` 时启动会**自动执行**预定义视频任务（见第二节） |
| `VIDEO_TASKS_FILE` | `video-tasks.json` | 自定义任务文件路径 |

示例：用 SQLite + 自动拉取视觉模型启动
```bash
USE_MYSQL=0 PULL_MODEL=1 ./start-video-analysis.sh
```

退出：终端按 `Ctrl+C`，脚本会自动停止前端/后端。
日志：`logs/backend.log`、`logs/frontend.log`。

---

## 二、执行「分析视频」任务的三种方式

分析视频有两条路：**(A)(B) 通过代码任务由脚本运行**，**(C) 在网页面板里操作**。

### 任务写在哪里？——`video-tasks.json`

项目根目录的 `video-tasks.json` 就是「**把任务写在代码里**」的地方。结构如下：

```json
{
  "ollama_url": "http://localhost:11434",
  "default_model": "minicpm-v:latest",
  "default_summary_model": "qwen2.5:14b",
  "language": "zh",
  "include_audio": false,
  "whisper_model": "base",
  "whisper_language": null,
  "tasks": [
    {
      "video_path": "/Users/even/mine/some/72.mp4",
      "model": "minicpm-v:latest",
      "summary_model": "qwen2.5:14b",
      "language": "zh",
      "frame_interval": 5,
      "max_frames": 16,
      "include_audio": true,
      "whisper_model": "base",
      "prompt": null,
      "save_report": true
    }
  ]
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `video_path` | 本地视频**绝对路径**（支持 `~`）。格式：mp4/mov/mkv/avi/webm/flv/m4v/mpg/mpeg/wmv/ts |
| `model` | 视觉模型（必须能看图，如 `minicpm-v:latest` / `llava:latest`） |
| `summary_model` | 汇总用文本模型；留空＝同 `model` |
| `language` | `zh` / `en` 输出语言 |
| `sample_mode` | 抽帧策略：`count`(固定张数) / `interval`(按时间跨度) / `scene`(镜头切换检测) / `auto`(默认,兼容旧行为) |
| `frame_interval` | 抽帧间隔（秒）。`interval` 模式下＝每隔几秒抽一帧；`auto` 下为"最密间隔" |
| `max_frames` | 抽帧张数。`count`/`auto` 模式生效；安全上限 2000。**调大＝更细但更慢** |
| `include_audio` | 是否转写音轨（声音/台词/旁白），默认 false |
| `whisper_model` | 语音模型：`tiny`/`base`/`small`/`medium`/`large-v3`（越大越准越慢，中文建议 `small`+） |
| `whisper_language` | 强制语种（如 `zh`/`en`）；`null` 自动检测 |
| `prompt` | 自定义逐帧提示词；`null` 用内置提示 |
| `save_report` | 是否生成 `<视频名>.analysis.md`（默认写到视频同目录，可被 `output_dir` 改写） |
| `output_dir` | 报告/字幕输出目录。`null`＝写到视频同目录（旧行为）；填路径（支持 `~`，自动创建）＝统一写到该目录，仍用视频主名 |
| `stream_log` | 是否在终端**实时**打印逐帧识别描述与汇总结果（默认 `true`） |

要分析多个视频，往 `tasks` 数组里再加对象即可。顶层的 `default_*` / `include_audio` / `whisper_*` /
`sample_mode` / `output_dir` / `stream_log` 为所有任务的默认值，单个任务里同名字段可覆盖。

#### 抽帧策略：固定张数 vs 时间跨度

| 模式 | 行为 | 适用 |
|---|---|---|
| `count` | 全片**均匀抽 `max_frames` 帧**（无视 `frame_interval`） | 想固定开销、快速预览 |
| `interval` | **每 `frame_interval` 秒抽 1 帧**（无视 `max_frames`，长视频更完整） | 长视频、不想漏场景切换 |
| `scene` | **镜头切换检测**：每 `scene_probe` 秒探测一次，HSV 直方图相关度低于 `scene_threshold` 即判为新镜头并抽帧 | 影视剧/多镜头，按"剧情切换"抽帧，不漏镜头也不浪费静止段 |
| `auto` | ≤ `max_frames` 帧且不密于 `frame_interval`（旧默认行为） | 兼容、折中 |

> `scene` 模式专属参数：`scene_probe`（探测间隔秒，默认 1.0，越小越细越慢）、
> `scene_threshold`（0~1 相关度阈值，默认 0.6，**调小=更不敏感、关键帧更少**；调大=更敏感、关键帧更多）。受安全上限 2000 帧保护。

例：1 小时视频「每分钟一帧」→ `"sample_mode": "interval", "frame_interval": 60`（约 60 帧）。
帧越多越细，但逐帧调用模型，耗时随帧数近似线性增长（安全上限 2000 帧）。

#### 声音处理

- 把任务的 `include_audio` 设为 `true`。脚本会用本地 **faster-whisper**
  （内部用 PyAV 直接从视频解码音轨，**无需安装 ffmpeg**）转写台词/旁白，
  和画面描述一起喂给汇总模型，让结论"声画结合"，并额外生成 `<视频名>.srt` 字幕。
- 中文若错别字偏多，把 `whisper_model` 从 `base` 提到 `small` 或 `medium`（int8 下依然较快）。
- 报告 `<视频名>.analysis.md` 含：**主要参数（时长/分辨率/帧率/编码/总帧数/大小）** +
  结构化分析（结合音频）+ 音频转写 + 逐帧描述。主要参数由 OpenCV 真实解码得出。

---

### 方式 A：启动 Web 服务时自动跑任务

适合「写好任务 → 启动系统就自动分析」。

```bash
RUN_VIDEO_TASKS=1 ./start-video-analysis.sh
```

脚本在启动后端/前端之前，会先把 `video-tasks.json` 里的任务逐个跑完（直连 Ollama），
随后照常启动 Web 服务。终端会实时打印每帧进度与最终报告路径。

---

### 方式 B：只跑任务，不启动 Web（推荐做批量分析）

直连 Ollama，无需启动后端、无需登录：

```bash
# 用默认 video-tasks.json
./venv/bin/python scripts/video_tasks.py

# 指定任务文件 / 覆盖 Ollama 地址
./venv/bin/python scripts/video_tasks.py --config video-tasks.json --ollama-url http://localhost:11434
```

执行完即在各视频同目录生成 `.analysis.md` 报告。

---

### 方式 C：在网页面板里操作（交互式）

1. 启动服务（见第一节），浏览器打开 `http://localhost:5173`（prod 模式为 `http://localhost:9102`）。
2. 左侧边栏点 **「Video Analysis」**（摄像机图标），或直接访问 **`/video`**。
3. 在左侧表单填写：
   - **Local video path**：本地绝对路径，如 `/Users/even/mine/some/72.mp4`
   - **Vision model**：选视觉模型（`minicpm-v:latest` / `llava:latest`）
   - **Summary model**：留空＝同视觉模型，或选 `qwen2.5:14b` 做中文汇总
   - 可调：Interval(抽帧间隔) / Max frames / Concurrency / Language / 是否转写音轨 / 是否保存报告
4. 点 **「Analyze video」**，右侧实时显示进度、逐帧描述与最终报告。

> ⚠️ 注意：**不要在普通聊天框里输入视频路径让模型分析**。聊天模型读不到你本机文件、
> 也不能执行命令，那样只会得到「建议你用 ffprobe / apt-get…」之类无效回答。
> 要真正分析视频，请用上面的 `/video` 面板（方式 C）或代码任务（方式 A/B）。

---

## 三、速查

| 需求 | 命令 |
|---|---|
| 默认启动（MySQL + dev） | `./start-video-analysis.sh` |
| 单端口生产模式 | `MODE=prod ./start-video-analysis.sh` |
| 改用 SQLite | `USE_MYSQL=0 ./start-video-analysis.sh` |
| 启动并自动跑任务 | `RUN_VIDEO_TASKS=1 ./start-video-analysis.sh` |
| 只跑任务不开 Web | `./venv/bin/python scripts/video_tasks.py` |
| 网页交互分析 | 打开 `http://localhost:5173/video` |
| 缺视觉模型时拉取 | `ollama pull qwen2.5-vl:7b`（或 `minicpm-v`） |

依赖确认（应全部 OK）：

```bash
./venv/bin/python -c "import cv2, faster_whisper, aiomysql, pymysql; print('deps OK')"
```

### 2026-6-25
```shell

./venv/bin/python scripts/video_tasks.py --config docs/json/even-some3.json --ollama-url http://localhost:11434

# even-path.json

# 更新版本，增加抽帧次数
./venv/bin/python scripts/video_tasks.py --config docs/json/task-1-5cout.json --ollama-url http://localhost:11434


  "ollama_url": "http://localhost:11434",
  "default_model": "minicpm-v:latest",
  "default_summary_model": "qwen2.5:14b",  
  "language": "zh",
  "include_audio": false,  //  是否转写音轨（声音/台词/旁白），默认 false |
  "whisper_model": "base",
  "whisper_language": null,
  "sample_mode": "auto",
  "tasks": [
    {
      "video_path": "/Users/even/mine/some/FC2-PPV-1035070.mp4",
      "model": "minicpm-v:latest",
      "summary_model": "qwen2.5:14b",
      "language": "zh",
      "sample_mode": "interval", // | 抽帧策略：`count`(固定张数) / `interval`(按时间跨度) / `auto`(默认,兼容旧行为) |
      "frame_interval": 60, // 抽帧间隔（秒）。`interval` 模式下＝每隔几秒抽一帧；`auto` 下为"最密间隔" |
      "max_frames": 16, // 抽帧张数。`count`/`auto` 模式生效；安全上限 2000。**调大＝更细但更慢** |
      "include_audio": true, // 是否转写音轨（声音/台词/旁白），默认 false |
      "whisper_model": "small", // 语音模型：`tiny`/`base`/`small`/`medium`/`large-v3`（越大越准越慢，中文建议 `small`+） 
      "prompt": null, // 自定义逐帧提示词；`null` 用内置提示 |
      "save_report": true // | 是否在视频同目录生成 `<视频名>.analysis.md` |
    }
  
./venv/bin/python scripts/video_tasks.py --config docs/json/task-1-6.json 


# 15：39
#开始使用mlx直接运行，这里还是基于ollama，看起来也可以脱离ollama进行
./venv/bin/python scripts/video_tasks.py --backend mlx --summary-backend ollama

# 16:33 再次进行，前面因为安装失败了。
./venv/bin/python scripts/video_tasks.py --backend mlx --summary-backend ollama


./venv/bin/python scripts/video_tasks.py --config docs/json/even-path.json --ollama-url http://localhost:11434

# 删除解析为模糊的视频

./venv/bin/python scripts/video_tasks.py --config docs/json/even-path.json --ollama-url http://localhost:11434

cd /Users/even/mine/some 
mv *.md *.srt /Users/even/mine/my-webui/logs/md

#下班后可以拉取这个大模型，或者中午的时候拉取
ollama pull qwen3-coder:30b

```

