#!/usr/bin/env bash
# =============================================================================
# 一键启动「前端 (vite dev)」 —— 供局域网/域名访问
# -----------------------------------------------------------------------------
# 解决本环境两大坑：
#   1) 系统 node 过低(v18.12 < 18.13)，自动探测并使用已安装的 node>=20；
#   2) node_modules 可能被环境清理，缺失时自动 npm install（--engine-strict=false）。
#
# 用法：
#   ./start-frontend.sh                 # 后台启动，日志 logs/frontend.log
#   FRONTEND_PORT=5173 BACKEND_PORT=9102 ./start-frontend.sh
#   FOREGROUND=1 ./start-frontend.sh    # 前台运行(Ctrl+C 停)
#
# 访问：http://<本机域名/IP>:5173/   视频面板 /video
# 注意：dev 模式前端会调 http://<同域名>:<BACKEND_PORT> 的后端，
#       所以同事侧需同时放通 前端端口 与 后端端口(默认 9102)。
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "$SCRIPT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-9102}"
LOG_DIR="$SCRIPT_DIR/logs"; mkdir -p "$LOG_DIR"
FRONTEND_LOG="$LOG_DIR/frontend.log"

c_info() { printf "\033[1;34m[INFO]\033[0m %s\n" "$*"; }
c_ok()   { printf "\033[1;32m[ OK ]\033[0m %s\n" "$*"; }
c_warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
c_err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*"; }

# ── 1) 探测 node>=20（系统 node 太低无法跑 vite5）──────────────────────────────
pick_node_bin() {
  local cand
  # 候选目录：workbuddy / cbruntime 安装的 node
  for cand in \
    /root/.workbuddy/binaries/node/versions/*/bin \
    /root/.cbruntime/runtimes/node/*/bin \
    /usr/local/node*/bin ; do
    [[ -x "$cand/node" ]] || continue
    local major; major="$("$cand/node" -p 'process.versions.node.split(".")[0]' 2>/dev/null)"
    if [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 20 )); then
      echo "$cand"; return 0
    fi
  done
  return 1
}

NODE_BIN="$(pick_node_bin || true)"
if [[ -z "$NODE_BIN" ]]; then
  c_err "未找到 node>=20。请先安装(例如官方包解压到 /usr/local)，或让 IDE 安装 node 20。"
  c_err "当前系统 node: $(command -v node && node -v 2>/dev/null)"
  exit 1
fi
export PATH="$NODE_BIN:$PATH"
c_ok "使用 node: $(node -v)  ($NODE_BIN)"

# ── 2) 依赖：缺失则安装 ────────────────────────────────────────────────────────
if [[ ! -x "$SCRIPT_DIR/node_modules/.bin/vite" ]]; then
  c_warn "node_modules/vite 缺失(可能被环境清理)，开始 npm install ..."
  npm install --engine-strict=false --no-audit --no-fund 2>&1 | tail -5
  if [[ ! -x "$SCRIPT_DIR/node_modules/.bin/vite" ]]; then
    c_err "npm install 后仍无 vite，请检查 logs。"; exit 1
  fi
  c_ok "依赖安装完成"
fi

# ── 3) 启动 vite（跳过 npm run dev 里的 pyodide:fetch 大下载）──────────────────
export VITE_BACKEND_PORT="$BACKEND_PORT"   # constants.ts 会读取它拼后端地址
c_info "后端端口(注入前端): $BACKEND_PORT"
c_info "前端端口: $FRONTEND_PORT, 监听 0.0.0.0(域名访问见 vite.config.ts allowedHosts)"

run_vite() {
  exec node_modules/.bin/vite dev --host 0.0.0.0 --port "$FRONTEND_PORT" --strictPort
}

if [[ "${FOREGROUND:-0}" == "1" ]]; then
  c_ok "前台启动(Ctrl+C 停)..."
  run_vite
else
  # 先停掉旧的同端口 vite，避免 strictPort 冲突
  pkill -f "vite dev --host 0.0.0.0 --port $FRONTEND_PORT" 2>/dev/null && sleep 1 || true
  : > "$FRONTEND_LOG"
  setsid bash -c "export PATH='$NODE_BIN:\$PATH'; export VITE_BACKEND_PORT='$BACKEND_PORT'; \
    node_modules/.bin/vite dev --host 0.0.0.0 --port '$FRONTEND_PORT' --strictPort \
    > '$FRONTEND_LOG' 2>&1" </dev/null >/dev/null 2>&1 &
  disown
  c_info "前端后台启动中，日志: $FRONTEND_LOG"
  for i in $(seq 1 40); do
    if curl -sf -o /dev/null "http://localhost:$FRONTEND_PORT/"; then
      c_ok "前端已就绪 → http://localhost:$FRONTEND_PORT/  (视频面板 /video)"
      exit 0
    fi
    sleep 1
  done
  c_warn "等待超时，请查看日志: tail -n 40 $FRONTEND_LOG"
  tail -n 20 "$FRONTEND_LOG" 2>/dev/null
fi
