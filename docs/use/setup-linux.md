# my-webui 安装文档（Linux / TencentOS Server · x86_64）

本文为 macOS 版 `setup.md` 的 Linux 实测对应版本，记录在 TencentOS Server 3.2（类 RHEL 8，x86_64）上从拉代码到跑通的完整流程，含离线视频分析（Ollama 后端）。

> 重要前提（本环境实测踩坑）：
> - venv 由 **`uv`** 创建，**不带 `pip`**，装包一律 `uv pip install`，不要用 `pip`/`pip3`。
> - 系统默认 `python3` 可能是 **3.6.8**，不满足要求；用 `uv` 复用系统 CPython 3.11（本机 3.11.13）。
> - **系统 `sqlite3` 过低（3.26.0 < 3.35.0）会让 `chromadb` 启动崩溃**，用 `pysqlite3-binary` + `sitecustomize.py` 透明修复（不改源码）。macOS 不会遇到此坑。
> - 系统 `node` 可能过低（v18.12.0 < 要求 18.13.0），`npm install` 会报 `EBADENGINE`，需装 Node ≥18.13 且 ≤22.x（本文 20.19.0）。
> - **默认源没有 `ffmpeg`**（`dnf install ffmpeg` 报 `No match`），用官方静态构建包装到 `/usr/local/bin`。
> - HuggingFace 超时时用镜像 `export HF_ENDPOINT=https://hf-mirror.com`，无需 VPN。

---

## 0. 版本要求

| 组件 | 要求 | 本机实测 |
|---|---|---|
| OS | Linux x86_64（el8 系列） | TencentOS Server 3.2 |
| Python | `>= 3.11, < 3.13` | 3.11.13（uv 复用 `/usr/bin/python3.11`） |
| Node.js | `>= 18.13.0, <= 22.x` | 20.19.0（系统 18.12.0 过低） |
| uv | 任意较新版本 | 0.11.24 |
| Ollama | 任意较新版本 | 0.30.10（11434 运行中） |
| ffmpeg | 建议安装 | 7.0.2 静态构建 |
| MySQL（可选） | 5.7+/8.0 | 本文用 SQLite（`USE_MYSQL=0`） |
| sqlite3（运行库） | `>= 3.35.0`（chromadb 要求） | 系统 3.26.0 → pysqlite3-binary 3.45.3 修复 |

---

## 1. 安装基础工具（无 Homebrew，用 dnf / 官方脚本）

### 1.1 uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version          # uv 0.11.x
```

### 1.2 Node.js（≥18.13，≤22.x）

```bash
# 方式一：NodeSource（el8）
curl -fsSL https://rpm.nodesource.com/setup_20.x | bash - && dnf install -y nodejs
# 方式二：官方预编译包（免 root）
cd /opt && curl -LO https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-x64.tar.xz
tar xf node-v20.19.0-linux-x64.tar.xz
export PATH="/opt/node-v20.19.0-linux-x64/bin:$PATH"
node -v && npm -v
```

> 后续涉及 `npm`/`node` 的命令都要保证 PATH 指向这个 Node 20。

### 1.3 ffmpeg（静态构建）

```bash
cd /tmp
curl -L -o ffmpeg-static.tar.xz https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar xf ffmpeg-static.tar.xz
d=$(find . -maxdepth 1 -type d -name 'ffmpeg-*-static' | head -1)
cp "$d/ffmpeg" "$d/ffprobe" /usr/local/bin/
ffmpeg -version | head -1
```

### 1.4 Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh   # 若未安装
ollama --version
```
