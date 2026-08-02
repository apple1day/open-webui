#!/usr/bin/env bash
# =============================================================================
# 一键启动「生产单端口模式」 —— 后端(9102) 同时托管前端页面 + API
# -----------------------------------------------------------------------------
# 访问地址（同事只需一个端口）：http://<本机IP>:9102/   (视频面板 /video)
# 原理：构建前端到 build/，后端 main.py 检测到 FRONTEND_BUILD_DIR 即在 / 挂载 SPA。
#
# 与原始 Linux 版的区别（macOS 兼容）：
#   1) pick_node_bin 增加 nvm / homebrew 等 macOS 常见路径，并回退到 PATH 中的 node；
#   2) 后台启动用 nohup ... </dev/null & 替代 macOS 没有的 setsid；
#   3) 默认 USE_MYSQL=1，并使用 .env 中的 DATABASE_URL（库名 webui）。
#
# 用法：
#   ./start-prod.sh                  # 构建(若缺)并启动；MySQL(webui); 单端口 9102
#   FORCE_BUILD=1 ./start-prod.sh    # 强制重新构建前端
#   PROD_PORT=9102 USE_MYSQL=0 ./start-prod.sh   # 改用 SQLite
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "$SCRIPT_DIR"

PROD_PORT="${PROD_PORT:-9102}"
USE_MYSQL="${USE_MYSQL:-1}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
LOG_DIR="$SCRIPT_DIR/logs"; mkdir -p "$LOG_DIR"
BACKEND_LOG="$LOG_DIR/backend.log"
BUILD_LOG="$LOG_DIR/build.log"
BUILD_DIR="$SCRIPT_DIR/build"

c_info() { printf "\033[1;34m[INFO]\033[0m %s\n" "$*"; }
c_ok()   { printf "\033[1;32m[ OK ]\033[0m %s\n" "$*"; }
c_warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
c_err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*"; }

# ── 1) node>=20（兼容 macOS：nvm / homebrew / 系统路径，并回退 PATH）────────────
pick_node_bin() {
  local cand major
  for cand in \
    "$HOME/.nvm/versions/node"/*/bin \
    /opt/homebrew/opt/node*/bin \
    /usr/local/opt/node*/bin \
    /usr/local/node*/bin \
    /root/.workbuddy/binaries/node/versions/*/bin \
    /root/.cbruntime/runtimes/node/*/bin ; do
    [[ -x "$cand/node" ]] || continue
    major="$("$cand/node" -p 'process.versions.node.split(".")[0]' 2>/dev/null)"
    [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 20 )) && { echo "$cand"; return 0; }
  done
  # 回退：PATH 中的 node
  if command -v node >/dev/null 2>&1; then
    major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)"
    [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 20 )) && { dirname "$(command -v node)"; return 0; }
  fi
  return 1
}
NODE_BIN="$(pick_node_bin || true)"
if [[ -z "$NODE_BIN" ]]; then
  c_err "未找到 node>=20，无法构建前端。"
  c_err "macOS 可安装：brew install node  或  nvm install 20"
  exit 1
fi
export PATH="$NODE_BIN:$PATH"
c_ok "使用 node: $(node -v)  ($NODE_BIN)"

# ── 2) 依赖 ───────────────────────────────────────────────────────────────────
if [[ ! -x "$SCRIPT_DIR/node_modules/.bin/vite" ]]; then
  c_warn "node_modules 缺失，npm install ..."
  npm install --engine-strict=false --no-audit --no-fund 2>&1 | tail -5
fi

# ── 3) 构建前端(跳过 pyodide:fetch 大下载) ────────────────────────────────────
if [[ "${FORCE_BUILD:-0}" == "1" || ! -f "$BUILD_DIR/index.html" ]]; then
  c_info "构建前端(vite build)，日志: $BUILD_LOG ..."
  if node_modules/.bin/vite build > "$BUILD_LOG" 2>&1; then
    c_ok "前端构建完成 → $BUILD_DIR"
  else
    c_err "前端构建失败，详见: tail -n 40 $BUILD_LOG"; tail -n 20 "$BUILD_LOG"; exit 1
  fi
else
  c_ok "已存在构建产物 $BUILD_DIR/index.html (FORCE_BUILD=1 可强制重建)"
fi

# ── 4) 后端环境 ───────────────────────────────────────────────────────────────
[[ -f "$SCRIPT_DIR/venv/bin/activate" ]] || { c_err "缺少 venv，请先按 docs 创建并装依赖"; exit 1; }
# shellcheck disable=SC1091
source "$SCRIPT_DIR/venv/bin/activate"

if [[ "$USE_MYSQL" != "1" ]]; then
  export DATABASE_URL="sqlite:///${SCRIPT_DIR}/backend/data/webui.db"
  c_info "数据库: SQLite ($DATABASE_URL)"
else
  # 优先读取 .env 中的 DATABASE_URL（库名 webui），否则用默认
  if [[ -f "$SCRIPT_DIR/.env" ]]; then
    DATABASE_URL="$(grep -E '^[[:space:]]*DATABASE_URL=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | sed -E "s/^[[:space:]]*['\"]|['\"]$//g")"
  fi
  DATABASE_URL="${DATABASE_URL:-mysql+aiomysql://root:123456@127.0.0.1:3306/webui}"
  # 探测 MySQL 端口是否可达；不可达（如容器内无 MySQL 或被权限限制）则自动回退 SQLite，
  # 避免脚本因缺库直接失败。有 MySQL 的环境行为不变。
  if ! (exec 3<>/dev/tcp/127.0.0.1/3306) 2>/dev/null; then
    c_warn "未检测到 MySQL(127.0.0.1:3306)，自动回退 SQLite。"
    DATABASE_URL="sqlite:///${SCRIPT_DIR}/backend/data/webui.db"
  else
    exec 3>&- 2>/dev/null
  fi
  export DATABASE_URL
  if [[ "$DATABASE_URL" == sqlite* ]]; then
    c_info "数据库: SQLite ($DATABASE_URL)"
  else
    c_info "数据库: MySQL ($DATABASE_URL)"
  fi
fi
export FRONTEND_BUILD_DIR="$BUILD_DIR"
export OLLAMA_BASE_URL="$OLLAMA_URL"
export CORS_ALLOW_ORIGIN="*"
export WEBUI_AUTH="${WEBUI_AUTH:-True}"
# 开启 API Key，便于以 agent/脚本方式远程调用（Authorization: Bearer sk-...）
export ENABLE_API_KEYS="${ENABLE_API_KEYS:-True}"

SECRET_FILE="$SCRIPT_DIR/backend/data/.webui_secret_key"
if [[ -z "${WEBUI_SECRET_KEY:-}" ]]; then
  if [[ -f "$SECRET_FILE" ]]; then
    WEBUI_SECRET_KEY="$(cat "$SECRET_FILE")"
  else
    mkdir -p "$(dirname "$SECRET_FILE")"
    WEBUI_SECRET_KEY="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 43)"
    echo "$WEBUI_SECRET_KEY" > "$SECRET_FILE"; chmod 600 "$SECRET_FILE" 2>/dev/null || true
  fi
fi
export WEBUI_SECRET_KEY

# ── 5) 重启后端(单端口, 无 --reload) ──────────────────────────────────────────
c_info "停止旧后端 ..."
pkill -f 'uvicorn open_webui' 2>/dev/null && sleep 2 || true
: > "$BACKEND_LOG"
cd "$SCRIPT_DIR/backend"
# macOS 无 setsid：用 nohup + </dev/null 脱离终端后台运行
# 注意：venv/bin/uvicorn 的 shebang 指向已失效的旧绝对路径（项目曾迁移目录），
# 因此用 `python -m uvicorn` 调用，可绕过损坏的 shebang。
nohup "$SCRIPT_DIR/venv/bin/python" -m uvicorn open_webui.main:app \
  --host 0.0.0.0 --port "$PROD_PORT" --forwarded-allow-ips '*' \
  > "$BACKEND_LOG" 2>&1 </dev/null &
cd "$SCRIPT_DIR"

c_info "后端启动中(首次建表/加载较慢) ..."
for i in $(seq 1 90); do
  if curl -sf -o /dev/null "http://localhost:$PROD_PORT/health"; then
    # 确认根路径返回页面而非 Not Found
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PROD_PORT/")"
    c_ok "服务就绪 → http://localhost:$PROD_PORT/  (根路径 HTTP $code)"
    c_ok "视频面板: http://localhost:$PROD_PORT/video"
    exit 0
  fi
  sleep 1
done
c_warn "等待超时，请查看: tail -n 60 $BACKEND_LOG"
tail -n 30 "$BACKEND_LOG" 2>/dev/null
