#!/usr/bin/env bash
# =============================================================================
# 远程视频分析客户端 —— 把「本机视频」上传到分析服务器，拿回离线分析报告
# -----------------------------------------------------------------------------
# 这个脚本运行在【你自己/同事的电脑】上，只依赖 curl。
# 它把视频上传到服务器的 /api/v1/video/analyze/upload 接口，服务器用本地大模型
# 离线分析后把报告返回。无需服务器文件系统访问权限。
#
# 准备：向管理员要一个 API Key（形如 sk-xxxx），设到环境变量或脚本里。
#
# 用法：
#   ./analyze-remote.sh /path/to/video.mp4
#   ./analyze-remote.sh /path/to/video.mp4 --audio          # 同时转写音频
#   MODEL=minicpm-v:latest ./analyze-remote.sh clip.mov
#   ARGS: --audio  --max N  --min N  --lang zh|en  --save  --json
#
# 配置(必须用环境变量注入，脚本不保留任何默认值/密钥/内网地址)：
#   API_BASE   必填，分析服务器地址，例如 https://your-analysis-host:9102
#   API_KEY    必填，服务器 API Key (sk-...)
#   MODEL / SUMMARY_MODEL  可选，留空由服务器自动选用可用视觉模型
# =============================================================================
set -uo pipefail

# 安全：API_BASE / API_KEY 均不得硬编码提交。请在运行环境通过环境变量注入，例如：
#   export API_BASE=https://your-analysis-host:9102
#   export API_KEY=sk-xxxx
# 脚本本身不保留任何密钥或内网地址。
API_BASE="${API_BASE:-}"   # 必填：分析服务器地址，通过环境变量注入
API_KEY="${API_KEY:-}"    # 必填：服务器 API Key，通过环境变量注入，切勿提交到版本库
MODEL="${MODEL:-}"
SUMMARY_MODEL="${SUMMARY_MODEL:-}"

VIDEO=""; LANG_OPT="zh"; MAXF="16"; MINF="20"; AUDIO="false"; SAVE="false"; RAW_JSON="0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --audio) AUDIO="true"; shift;;
    --save)  SAVE="true"; shift;;
    --json)  RAW_JSON="1"; shift;;
    --max)   MAXF="$2"; shift 2;;
    --min)   MINF="$2"; shift 2;;
    --lang)  LANG_OPT="$2"; shift 2;;
    -*)      echo "未知参数: $1"; exit 2;;
    *)       VIDEO="$1"; shift;;
  esac
done

[[ -z "$VIDEO" ]] && { echo "用法: $0 /path/to/video.mp4 [--audio --max N --min N --lang zh|en --save --json]"; exit 2; }
[[ -f "$VIDEO" ]] || { echo "[ERR] 文件不存在: $VIDEO"; exit 1; }
[[ -z "$API_BASE" ]] && { echo "[ERR] 未设置 API_BASE（服务器地址）。请用环境变量注入：export API_BASE=https://your-host:9102"; exit 2; }
[[ -z "$API_KEY" ]]  && { echo "[ERR] 未设置 API_KEY。请用环境变量注入：export API_KEY=sk-xxxx（切勿硬编码到脚本/版本库）"; exit 2; }

echo "[INFO] 上传分析: $VIDEO  ->  $API_BASE"
echo "[INFO] 参数: lang=$LANG_OPT max_frames=$MAXF min_frames=$MINF audio=$AUDIO save=$SAVE model=${MODEL:-auto}"

FORM=(-F "file=@${VIDEO}" -F "language=${LANG_OPT}" -F "max_frames=${MAXF}" \
      -F "min_frames=${MINF}" -F "include_audio=${AUDIO}" -F "save_report=${SAVE}")
[[ -n "$MODEL" ]] && FORM+=(-F "model=${MODEL}")
[[ -n "$SUMMARY_MODEL" ]] && FORM+=(-F "summary_model=${SUMMARY_MODEL}")

TMP="$(mktemp)"
CODE="$(curl -s -m 1800 -o "$TMP" -w '%{http_code}' \
  -X POST "${API_BASE}/api/v1/video/analyze/upload" \
  -H "Authorization: Bearer ${API_KEY}" "${FORM[@]}")"

if [[ "$RAW_JSON" == "1" ]]; then
  cat "$TMP"; echo; rm -f "$TMP"; exit 0
fi

if [[ "$CODE" != "200" ]]; then
  echo "[ERR] 服务器返回 HTTP $CODE:"; cat "$TMP"; echo; rm -f "$TMP"; exit 1
fi

# 优先用 python 美化输出，没有 python 就直接打印 JSON
if command -v python3 >/dev/null 2>&1; then
  python3 - "$TMP" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("\n===== 视频离线分析报告 =====")
print(f"模型: {d.get('model')}  汇总: {d.get('summary_model')}")
print(f"时长: {d.get('duration')}s  抽帧: {d.get('sampled_frames')}  耗时: {d.get('elapsed')}s")
if d.get('report_path'):
    print(f"服务器报告: {d['report_path']}")
print("\n--- 摘要 ---")
print(d.get('summary',''))
if d.get('transcript'):
    print("\n--- 音频转写 ---"); print(d['transcript'])
PY
else
  cat "$TMP"
fi
rm -f "$TMP"
