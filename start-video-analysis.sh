#!/usr/bin/env bash
# =============================================================================
# Open WebUI + 本地视频分析  一键启动脚本 (macOS / Linux)
# -----------------------------------------------------------------------------
# 启动顺序：Ollama(本地大模型)  →  后端(uvicorn, 端口 9102)  →  前端(vite, 5173)
#
# 用法：
#   ./start-video-analysis.sh              # 默认 dev 模式 (热重载) + MySQL + 后端 9102
#   MODE=prod ./start-video-analysis.sh    # 生产模式 (构建前端, 后端单端口 9102)
#   USE_MYSQL=0 ./start-video-analysis.sh  # 改用 SQLite (默认走 .env 里的 MySQL)
#   PULL_MODEL=1 ./start-video-analysis.sh # 视觉模型缺失时自动 ollama pull
#
# 退出：Ctrl+C 会自动停止后端 / 前端 (Ollama 若由本脚本拉起也会一并停止)
# =============================================================================
set -uo pipefail

# ── 可配置项 ─────────────────────────────────────────────────────────────────
MODE="${MODE:-dev}"                       # dev | prod
BACKEND_PORT="${BACKEND_PORT:-9102}"      # 起始端口(默认 9102); 被占用时自动累加, 并注入前端
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PROD_PORT="${PROD_PORT:-9102}"            # 生产单端口
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
VISION_MODEL="${VISION_MODEL:-qwen2.5-vl:7b}"
USE_MYSQL="${USE_MYSQL:-1}"               # 默认走 .env 中的 MySQL; 设 0 改用 SQLite
PULL_MODEL="${PULL_MODEL:-0}"

# ── 路径与日志 ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "$SCRIPT_DIR"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
OLLAMA_LOG="$LOG_DIR/ollama.log"

# ── 颜色输出 ─────────────────────────────────────────────────────────────────
c_info() { printf "\033[1;34m[INFO]\033[0m %s\n" "$*"; }
c_ok()   { printf "\033[1;32m[ OK ]\033[0m %s\n" "$*"; }
c_warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
c_err()  { printf "\033[1;31m[ERR ]\033[0m %s\n" "$*"; }

# ── 进程管理 / 退出清理 ──────────────────────────────────────────────────────
BACKEND_PID=""
FRONTEND_PID=""
OLLAMA_PID=""

cleanup() {
  echo
  c_info "正在停止服务..."
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null && c_ok "前端已停止 (pid $FRONTEND_PID)"
  [[ -n "$BACKEND_PID"  ]] && kill "$BACKEND_PID"  2>/dev/null && c_ok "后端已停止 (pid $BACKEND_PID)"
  [[ -n "$OLLAMA_PID"   ]] && kill "$OLLAMA_PID"   2>/dev/null && c_ok "Ollama 已停止 (pid $OLLAMA_PID)"
  exit 0
}
trap cleanup INT TERM

wait_for_url() {  # $1=url  $2=name  $3=timeout秒
  local url="$1" name="$2" timeout="${3:-60}" i=0
  while ! curl -sf "$url" >/dev/null 2>&1; do
    i=$((i+1))
    if (( i > timeout )); then c_err "$name 启动超时 (${timeout}s)"; return 1; fi
    sleep 1
  done
  return 0
}

port_in_use() {  # $1=port  -> 0 表示被占用
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

find_free_port() {  # $1=起始端口  -> 输出第一个空闲端口(自动累加)
  local p="$1"
  while port_in_use "$p"; do
    p=$((p+1))
  done
  echo "$p"
}

# ── Step 1: Ollama ───────────────────────────────────────────────────────────
c_info "Step 1/4  检查 Ollama ..."
if ! command -v ollama >/dev/null 2>&1; then
  c_err "未找到 ollama，请先安装：https://ollama.com/download"
  exit 1
fi
if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  c_ok "Ollama 已在运行：$OLLAMA_URL"
else
  c_info "拉起 ollama serve (日志: $OLLAMA_LOG)"
  nohup ollama serve >"$OLLAMA_LOG" 2>&1 &
  OLLAMA_PID=$!
  wait_for_url "$OLLAMA_URL/api/tags" "Ollama" 30 || { cat "$OLLAMA_LOG"; exit 1; }
  c_ok "Ollama 启动成功 (pid $OLLAMA_PID)"
fi

# ── 列出本机已有模型 + 识别视觉模型 ──────────────────────────────────────────
INSTALLED_MODELS=()
while IFS= read -r _m; do
  [[ -n "$_m" ]] && INSTALLED_MODELS+=("$_m")
done < <(ollama list 2>/dev/null | awk 'NR>1 && $1!="" {print $1}')
MODEL_COUNT=${#INSTALLED_MODELS[@]}

c_info "本机已安装 $MODEL_COUNT 个 Ollama 模型："
DETECTED_VISION=""
HAS_CONFIGURED=0
if (( MODEL_COUNT > 0 )); then
  for _m in "${INSTALLED_MODELS[@]}"; do
    printf "        • %s\n" "$_m"
    [[ "$_m" == "$VISION_MODEL" ]] && HAS_CONFIGURED=1
    if [[ -z "$DETECTED_VISION" ]] && \
       printf '%s' "$_m" | grep -Eiq 'vl|llava|bakllava|minicpm-v|moondream|vision|cogvlm'; then
      DETECTED_VISION="$_m"
    fi
  done
else
  c_warn "本机暂无任何模型"
fi

# 交互式选择（仅在终端可交互时）
choose_model_interactive() {
  echo
  c_warn "未检测到视觉模型 (qwen2.5-vl / llava / minicpm-v 等)，视频抽帧分析需要它。"
  echo   "  请选择操作："
  echo   "    [1] 联网拉取推荐视觉模型: $VISION_MODEL"
  echo   "    [2] 从已有模型中选一个 (非视觉模型无法识别画面, 仅供联调/文本汇总)"
  echo   "    [3] 跳过, 直接启动 (稍后在网页面板里选模型)"
  read -r -p "  输入序号 [默认 3]: " _choice
  case "${_choice:-3}" in
    1)
      c_info "拉取 $VISION_MODEL (首次较慢)..."
      ollama pull "$VISION_MODEL" && c_ok "已拉取 $VISION_MODEL" || c_warn "拉取失败, 可稍后手动拉取"
      ;;
    2)
      if (( MODEL_COUNT > 0 )); then
        local idx=1
        for _m in "${INSTALLED_MODELS[@]}"; do printf "    %d) %s\n" "$idx" "$_m"; idx=$((idx+1)); done
        read -r -p "  选择模型序号 [默认 1]: " _mi
        _mi="${_mi:-1}"
        if [[ "$_mi" =~ ^[0-9]+$ ]] && (( _mi >= 1 && _mi <= MODEL_COUNT )); then
          VISION_MODEL="${INSTALLED_MODELS[$((_mi-1))]}"
          c_ok "已选用模型: $VISION_MODEL"
          c_warn "(若非视觉模型, 画面识别会失败, 仅供联调)"
        else
          c_warn "输入无效, 跳过模型选择。"
        fi
      else
        c_warn "无已有模型可选, 跳过。"
      fi
      ;;
    *) c_info "跳过模型准备, 直接启动。" ;;
  esac
}

if (( HAS_CONFIGURED == 1 )); then
  c_ok "视觉模型已就绪并选用：$VISION_MODEL"
elif [[ -n "$DETECTED_VISION" ]]; then
  VISION_MODEL="$DETECTED_VISION"
  c_ok "检测到视觉模型并选用：$VISION_MODEL"
elif [[ "$PULL_MODEL" == "1" ]]; then
  c_info "拉取视觉模型 $VISION_MODEL (首次较慢)..."
  ollama pull "$VISION_MODEL" || c_warn "拉取失败, 可稍后手动: ollama pull $VISION_MODEL"
elif [[ -t 0 ]]; then
  choose_model_interactive
else
  c_warn "未发现视觉模型 ${VISION_MODEL}，且当前为非交互环境。"
  c_warn "请先: ollama pull $VISION_MODEL  (或加 PULL_MODEL=1 重跑本脚本)"
fi
export VISION_MODEL

# ── Step 2: 后端环境 ─────────────────────────────────────────────────────────
c_info "Step 2/4  准备后端 (Python venv) ..."
if [[ ! -f "$SCRIPT_DIR/venv/bin/activate" ]]; then
  c_err "未找到 venv，请先按 docs 创建虚拟环境并安装依赖"
  exit 1
fi
# shellcheck disable=SC1091
source "$SCRIPT_DIR/venv/bin/activate"

# 数据库：默认 SQLite，免外部依赖；USE_MYSQL=1 时沿用 .env 的 MySQL
if [[ "$USE_MYSQL" != "1" ]]; then
  export DATABASE_URL="sqlite:///${SCRIPT_DIR}/backend/data/webui.db"
  c_info "使用 SQLite: $DATABASE_URL"
else
  c_info "使用 .env 中的 MySQL 配置 (需 MySQL 已启动)"
fi
export OLLAMA_BASE_URL="$OLLAMA_URL"
export CORS_ALLOW_ORIGIN="*"
export WEBUI_AUTH="${WEBUI_AUTH:-True}"

# WEBUI_SECRET_KEY: 直接用 uvicorn 启动时不会自动生成, 缺失会导致后端启动后立即退出。
# 这里持久化到 data/.webui_secret_key, 保证重启后登录态/会话不失效。
SECRET_FILE="$SCRIPT_DIR/backend/data/.webui_secret_key"
if [[ -z "${WEBUI_SECRET_KEY:-}" ]]; then
  if [[ -f "$SECRET_FILE" ]]; then
    WEBUI_SECRET_KEY="$(cat "$SECRET_FILE")"
  else
    mkdir -p "$(dirname "$SECRET_FILE")"
    WEBUI_SECRET_KEY="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 43)"
    echo "$WEBUI_SECRET_KEY" > "$SECRET_FILE"
    chmod 600 "$SECRET_FILE" 2>/dev/null || true
    c_info "已生成并持久化 WEBUI_SECRET_KEY → $SECRET_FILE"
  fi
fi
export WEBUI_SECRET_KEY

# 端口自动累加: 8080 / 5173 / 9102 被占用时自动顺延, 避免启动卡死
if [[ "$MODE" == "prod" ]]; then
  NEW_PROD_PORT="$(find_free_port "$PROD_PORT")"
  [[ "$NEW_PROD_PORT" != "$PROD_PORT" ]] && c_warn "端口 $PROD_PORT 被占用, 改用 $NEW_PROD_PORT"
  PROD_PORT="$NEW_PROD_PORT"
else
  NEW_BACKEND_PORT="$(find_free_port "$BACKEND_PORT")"
  [[ "$NEW_BACKEND_PORT" != "$BACKEND_PORT" ]] && c_warn "后端端口 $BACKEND_PORT 被占用, 改用 $NEW_BACKEND_PORT"
  BACKEND_PORT="$NEW_BACKEND_PORT"
  NEW_FRONTEND_PORT="$(find_free_port "$FRONTEND_PORT")"
  [[ "$NEW_FRONTEND_PORT" != "$FRONTEND_PORT" ]] && c_warn "前端端口 $FRONTEND_PORT 被占用, 改用 $NEW_FRONTEND_PORT"
  FRONTEND_PORT="$NEW_FRONTEND_PORT"
fi

# ── Step 3/4: 按模式启动 ─────────────────────────────────────────────────────
if [[ "$MODE" == "prod" ]]; then
  # 生产：构建前端 → 后端单端口对外提供静态页 + API
  c_info "Step 3/4  构建前端 (prod) ..."
  if [[ ! -d "$SCRIPT_DIR/node_modules" ]]; then
    c_info "首次安装前端依赖 npm install ..."
    npm install
  fi
  npm run build
  c_ok "前端构建完成"

  c_info "Step 4/4  启动后端 (端口 $PROD_PORT) ..."
  cd "$SCRIPT_DIR/backend"
  open-webui serve --port "$PROD_PORT" 2>&1 | tee "$BACKEND_LOG" &
  BACKEND_PID=$!
  wait_for_url "http://localhost:$PROD_PORT/health" "后端" 90 || exit 1
  c_ok "服务已就绪 → http://localhost:$PROD_PORT  (视频分析: /video)"
  command -v open >/dev/null 2>&1 && open "http://localhost:$PROD_PORT"
else
  # dev：后端 BACKEND_PORT(热重载) + 前端 vite FRONTEND_PORT
  # 把后端实际端口注入前端(constants.ts 会读取 VITE_BACKEND_PORT)
  export VITE_BACKEND_PORT="$BACKEND_PORT"
  c_info "Step 3/4  启动后端 (dev, 端口 $BACKEND_PORT, 热重载) ..."
  cd "$SCRIPT_DIR/backend"
  uvicorn open_webui.main:app \
    --host 0.0.0.0 --port "$BACKEND_PORT" \
    --forwarded-allow-ips '*' --reload \
    >"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  cd "$SCRIPT_DIR"
  wait_for_url "http://localhost:$BACKEND_PORT/health" "后端" 90 || { tail -n 40 "$BACKEND_LOG"; exit 1; }
  c_ok "后端就绪 → http://localhost:$BACKEND_PORT (日志: $BACKEND_LOG)"

  c_info "Step 4/4  启动前端 (vite dev, 端口 $FRONTEND_PORT) ..."
  if [[ ! -d "$SCRIPT_DIR/node_modules" ]]; then
    c_info "首次安装前端依赖 npm install (较慢, 请耐心等待) ..."
    npm install
  fi
  npm run dev -- --host --port "$FRONTEND_PORT" --strictPort >"$FRONTEND_LOG" 2>&1 &
  FRONTEND_PID=$!
  wait_for_url "http://localhost:$FRONTEND_PORT" "前端" 120 || { tail -n 40 "$FRONTEND_LOG"; exit 1; }
  c_ok "前端就绪 → http://localhost:$FRONTEND_PORT (日志: $FRONTEND_LOG)"

  echo
  c_ok "============================================================"
  c_ok " 访问:  http://localhost:$FRONTEND_PORT"
  c_ok " 视频分析面板: 左侧栏 Video Analysis  或  /video"
  c_ok " 当前选用模型: $VISION_MODEL"
  c_ok " 后端 API:     http://localhost:$BACKEND_PORT/api/v1/video"
  c_ok "============================================================"
  command -v open >/dev/null 2>&1 && open "http://localhost:$FRONTEND_PORT"
fi

echo
c_info "服务运行中... 按 Ctrl+C 停止。实时日志:"
c_info "  tail -f $BACKEND_LOG"
c_info "  tail -f $FRONTEND_LOG"
wait
