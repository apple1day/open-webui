# my-webui 环境搭建与安装文档（macOS / Apple Silicon）

本文档记录从拉取代码到本地跑通的完整流程，并包含**离线视频分析（Ollama / MLX 两种后端）**的部署步骤。
适用环境：macOS（Apple Silicon，如 M1/M2/M3/M5）。Linux 步骤类似，差异处会标注。

> 重要前提（踩坑总结）：
> - 本项目的 Python 虚拟环境由 **`uv`** 创建，**venv 内不带 `pip`**。装包一律用 `uv pip install`，**不要用 `pip` / `pip3`**。
> - **不要使用 Homebrew Python 3.14**：其 `pyexpat` 与系统库不兼容，`pip3` 直接报错。项目要求 **Python 3.11 / 3.12**。
> - 国内网络访问 HuggingFace 超时时，使用镜像 `export HF_ENDPOINT=https://hf-mirror.com`，**无需 VPN**。

---

## 0. 版本要求

| 组件 | 要求 | 说明 |
|---|---|---|
| Python | `>= 3.11, < 3.13` | 实测使用 3.11；MLX 也需要 ≤3.13 |
| Node.js | `>= 18.13.0, <= 22.x` | 见 `package.json` 的 `engines` |
| uv | 任意较新版本 | Python 环境与依赖管理 |
| Ollama | 任意较新版本 | 本地大模型推理（视频分析后端之一） |
| ffmpeg | 建议安装 | 部分视频/音频解码更稳 |
| MySQL（可选） | 5.7+/8.0 | 默认走 MySQL；也可改用 SQLite |

---

## 1. 安装基础工具（Homebrew）

```bash
# 若未安装 Homebrew，先安装：https://brew.sh
brew install uv node ffmpeg

# 安装 Ollama（本地大模型）
brew install ollama
# 或下载安装包：https://ollama.com/download
```

> 校验：`uv --version`、`node -v`、`ffmpeg -version`、`ollama --version` 均应正常输出。

---

## 2. 拉取代码

```bash
git clone <仓库地址> my-webui
cd my-webui
# 切到目标分支（按需）
git checkout videos
```

---

## 3. 创建 Python 虚拟环境（uv，3.11）

```bash
cd my-webui

# 用 uv 创建 3.11 虚拟环境到 ./venv（uv 会自动下载对应 CPython）
uv venv --python 3.11 venv

# 安装后端依赖（注意：用 uv pip，并显式指定 venv 的 python）
uv pip install --python ./venv/bin/python -r backend/requirements.txt
```

> 说明：
> - 该 venv 内**没有 `pip`**，所有安装请用 `uv pip install --python ./venv/bin/python ...`。
> - 运行任何 Python 脚本请用 `./venv/bin/python xxx.py`（不要用系统 `python3`，那是 3.14）。
> - 校验：`./venv/bin/python --version` 应输出 `Python 3.11.x`。

---

## 4. 安装前端依赖

```bash
cd my-webui
npm install
```

---

## 5. 配置环境变量（.env，可选）

项目支持 MySQL 或 SQLite：

- **默认走 MySQL**：需本机已启动 MySQL，并在 `.env` 配好 `DATABASE_URL`。
- **改用 SQLite（免外部依赖）**：启动脚本加 `USE_MYSQL=0` 即可（见第 7 节）。

`WEBUI_SECRET_KEY` 由启动脚本自动生成并持久化到 `backend/data/.webui_secret_key`，一般无需手动设置。

---

## 6. 准备本地模型（Ollama）

```bash
# 启动 Ollama 服务（若未随系统自启）
ollama serve &

# 拉取视觉模型（视频逐帧识别）与文本模型（汇总）
ollama pull qwen2.5-vl:7b      # 或 minicpm-v / llava
ollama pull qwen2.5:14b        # 文本汇总（内存紧张可换 qwen2.5:7b）

# 查看已安装模型
ollama list
```

---

## 7. 一键启动（推荐）

项目提供 `start-video-analysis.sh`，按「Ollama → 后端 → 前端」顺序拉起：

```bash
cd my-webui

# 开发模式（热重载）+ 默认 MySQL
./start-video-analysis.sh

# 改用 SQLite（免 MySQL）
USE_MYSQL=0 ./start-video-analysis.sh

# 生产模式（构建前端，后端单端口 9102）
MODE=prod ./start-video-analysis.sh

# 视觉模型缺失时自动拉取
PULL_MODEL=1 ./start-video-analysis.sh
```

启动后访问：

- 前端：`http://localhost:5173`（端口被占用会自动顺延）
- 视频分析面板：左侧栏 `Video Analysis` 或 `/video`
- 后端 API：`http://localhost:9102/api/v1/video`

> 端口 5173 / 9102 被占用时脚本会自动累加并注入前端，无需手动改。

---

## 8. 离线视频分析

### 8.1 批量任务（无需登录、无需后端）

编辑根目录 `video-tasks.json`，配置要分析的视频与参数，然后：

```bash
# 直接用 venv 的 python 运行任务运行器
./venv/bin/python scripts/video_tasks.py

# 或随启动脚本自动执行一遍
RUN_VIDEO_TASKS=1 ./start-video-analysis.sh
```

输出：在视频同目录生成 `<name>.analysis.md`（开启音频时另生成 `<name>.srt` 字幕）。

### 8.2 抽帧策略（`sample_mode`）

| 模式 | 含义 |
|---|---|
| `count` | 全片均匀抽 `max_frames` 帧 |
| `interval` | 每 `frame_interval` 秒抽 1 帧（长视频更完整） |
| `scene` | 镜头切换检测，画面骤变处抽帧 |
| `auto` | ≤ `max_frames` 帧且不密于 `frame_interval` |

---

## 9. 使用 MLX 后端（Apple Silicon 原生，可选）

MLX 是苹果自研框架，在 Apple Silicon 上利用统一内存零拷贝 + Metal，相比 Ollama(GGUF) 通常**更快、占用更低**。

### 9.1 安装 MLX（装进同一个 venv）

```bash
cd my-webui
uv pip install --python ./venv/bin/python -U mlx mlx-vlm mlx-lm

# 校验（应输出 Metal GPU available: True）
./venv/bin/python -c "import mlx.core as mx; print('Metal GPU available:', mx.metal.is_available())"
```

### 9.2 预下载模型（走国内镜像，无需 VPN）

```bash
export HF_ENDPOINT=https://hf-mirror.com
# 视觉模型（约 5.3GB，下载一次后离线可用）
./venv/bin/hf download mlx-community/Qwen2.5-VL-7B-Instruct-4bit
```

> 模型默认缓存到 `~/.cache/huggingface/`。
> 想长期生效，把 `export HF_ENDPOINT=https://hf-mirror.com` 写进 `~/.zshrc`。

### 9.3 运行（视觉走 MLX，汇总可选 Ollama 或 MLX）

```bash
cd my-webui
export HF_ENDPOINT=https://hf-mirror.com

# 方案 A：视觉走 MLX、汇总仍用本地 Ollama 的 qwen2.5:14b
./venv/bin/python scripts/video_tasks.py --backend mlx --summary-backend ollama

# 方案 B：全 MLX（断开 Ollama 也能跑，会另下一个文本模型）
./venv/bin/python scripts/video_tasks.py --backend mlx --summary-backend mlx

# 方案 C：更省内存/更快，换 2B 视觉模型
./venv/bin/python scripts/video_tasks.py --backend mlx --summary-backend ollama \
  --mlx-model mlx-community/Qwen2-VL-2B-Instruct-4bit
```

### 9.4 MLX 相关 CLI / 配置项

CLI 参数（也可写进 `video-tasks.json` 的全局或单任务级，任务级优先）：

| CLI | config 键 | 含义 | 默认 |
|---|---|---|---|
| `--backend` | `backend` | 视觉(逐帧)后端：`ollama` / `mlx` | `ollama` |
| `--summary-backend` | `summary_backend` | 汇总后端：`ollama` / `mlx` | 跟随 `backend` |
| `--mlx-model` | `mlx_model` | MLX 视觉模型 id | `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` |
| `--mlx-summary-model` | `mlx_summary_model` | MLX 文本模型 id | `mlx-community/Qwen2.5-7B-Instruct-4bit` |
| — | `frame_max_tokens` / `summary_max_tokens` | MLX 生成上限 | 300 / 1200 |

`video-tasks.json` 示例：

```json
{
  "backend": "mlx",
  "summary_backend": "ollama",
  "mlx_model": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
  "default_summary_model": "qwen2.5:14b",
  "tasks": [
    { "video_path": "/绝对路径/示例.mp4", "sample_mode": "interval", "frame_interval": 60, "include_audio": true }
  ]
}
```

---

## 10. 常见问题（FAQ）

**Q1：`zsh: command not found: pip` / `python`（已 activate venv）**
A：本 venv 由 uv 创建、不带 pip。装包用 `uv pip install --python ./venv/bin/python ...`；跑脚本用 `./venv/bin/python ...`。

**Q2：`pip3` 报 `pyexpat ... Symbol not found` 崩溃**
A：那是 Homebrew Python 3.14 损坏，不要用它。坚持用项目 venv（3.11）+ uv。

**Q3：MLX 模型下载 `ConnectTimeout` / 卡住**
A：huggingface.co 被墙。设 `export HF_ENDPOINT=https://hf-mirror.com` 走镜像即可，无需 VPN。下载支持断点续传，中断后重跑同一命令会续传。

**Q4：跑整片视频很久没结束**
A：正常。10 分钟视频抽多帧逐帧推理 + 大模型汇总会耗时数分钟。首帧加载模型后会常驻内存，后续帧更快。

**Q5：内存吃紧（24GB）**
A：避免同时常驻 Ollama 14B 与 MLX 7B。可选全 MLX、或把视觉模型换成 2B（`Qwen2-VL-2B-Instruct-4bit`）。

---

## 11. 快速命令速查

```bash
# 创建环境
uv venv --python 3.11 venv
uv pip install --python ./venv/bin/python -r backend/requirements.txt
npm install

# 装 MLX（可选）
uv pip install --python ./venv/bin/python -U mlx mlx-vlm mlx-lm
export HF_ENDPOINT=https://hf-mirror.com
./venv/bin/hf download mlx-community/Qwen2.5-VL-7B-Instruct-4bit

# 启动服务
./start-video-analysis.sh                 # 默认
USE_MYSQL=0 ./start-video-analysis.sh      # 用 SQLite

# 离线视频分析
./venv/bin/python scripts/video_tasks.py                                    # Ollama
./venv/bin/python scripts/video_tasks.py --backend mlx --summary-backend ollama  # MLX 视觉
```
