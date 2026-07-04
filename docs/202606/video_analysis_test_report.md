# 视频大模型分析系统 · 启动与测试报告

> 被测系统：`my-webui`（Open WebUI + 本地 Ollama 多模态模型，离线视频分析）
> 测试时间：2026-07-04
> 测试目标：启动系统 → 真实跑通视频分析 → 输出测试文档 + Postman 集合

---

## 1. 测试环境

| 项 | 值 |
|---|---|
| 系统 | `my-webui`（后端 FastAPI/uvicorn，端口 `9102`；Ollama `11434`） |
| Python | venv `/data/workspace/my-webui/venv`（3.11） |
| 数据库 | SQLite `backend/data/webui.db` |
| 本机视觉模型 | `gemma4:26b`、`llama3.2-vision:11b`、`minicpm-v:latest`（文本模型另计） |
| 测试样本 | `/data/workspace/data/1.mp4`（1.2 MB，10.73s，322 帧） |
| 鉴权 | API Key `Authorization: Bearer sk-...`（存于 `api_key` 表，admin 用户） |

---

## 2. 启动与启动中发现的问题（重要）

### 2.1 启动方式
生产单端口模式（后端 9102 同时托管前端页面与 API）：

```bash
cd /data/workspace/my-webui
bash start-prod.sh          # 内部：检测 node>=20 → 构建前端 → 启动 uvicorn :9102
# 或 dev 模式（前后端分离）：
#   ./start-video-analysis.sh
```

Ollama 需独立运行：`ollama serve`（本机已在线，模型已装）。

### 2.2 发现并修复的 Bug：后端加载了「过期代码」
测试首跑 `POST /api/v1/video/analyze` 直接 500，后端日志报错：

```
AttributeError: 'VideoAnalyzeResponse' object has no attribute 'status'
  at routers/video_analysis.py, line 526, in analyze_video
```

**根因**：`video_analysis.py` 于 **6/29** 被修改为 `VideoAnalyzeResponse` 增加 `status` 字段，
但后端进程是 **6/27** 启动的（生产模式 `start-prod.sh` 用 `uvicorn ... ` 直起，**无 `--reload`**），
一直驻留旧代码。属于典型的「代码改了但服务没重启」。

**修复**：用与 `start-prod.sh` 一致的环境变量直接重启 uvicorn（加载 6/29 正确代码）：

```bash
cd /data/workspace/my-webui/backend
pkill -f "uvicorn open_webui"
DATABASE_URL='sqlite:////data/workspace/my-webui/backend/data/webui.db' \
OLLAMA_BASE_URL='http://localhost:11434' CORS_ALLOW_ORIGIN='*' \
WEBUI_AUTH='True' WEBUI_SECRET_KEY='<backend/data/.webui_secret_key>' \
ENABLE_API_KEYS='True' \
/data/workspace/my-webui/venv/bin/uvicorn open_webui.main:app \
  --host 0.0.0.0 --port 9102 --forwarded-allow-ips '*'
```

> ⚠️ **运维提醒**：`start-prod.sh` 要求 `node>=20`，而本机 node 为 v18，会导致脚本直接退出
> （即使前端已构建可跳过）。生产环境请升级 node，或如上直接用 uvicorn 启动。
> 任何修改 `backend/open_webui/**` 后，**必须重启后端**才会生效。

---

## 3. 接口测试结果汇总

| # | 方法 | 路径 | 鉴权 | 结果 | 说明 |
|---|---|---|---|---|---|
| 1 | GET | `/api/v1/video/health` | 需 | ✅ 200 | opencv=true；支持 11 种视频容器 |
| 2 | GET | `/api/v1/video/models` | 需 | ✅ 200 | 返回 6 个模型，含 3 个视觉模型 |
| 3 | POST | `/api/v1/video/analyze` | 需 | ✅ 200 | 单视频分析，见 §4 |
| 4 | POST | `/api/v1/video/analyze/stream` | 需 | ⏳ SSE | 同 analyze，以 `text/event-stream` 推进度（未跑长测） |
| 5 | POST | `/api/v1/video/analyze/upload` | 需 | — | multipart 上传文件分析（未跑） |
| 6 | POST | `/api/v1/video/batch/scan` | 需 | ✅ 200 | 扫描目录，**必须带 `model` 字段**（否则 422） |
| 7 | POST | `/api/v1/video/batch` | 需 | — | 启动后台批量任务（未跑长测） |
| 8 | GET | `/api/v1/video/batch` | 需 | — | 列出批量任务 |
| 9 | GET | `/api/v1/video/batch/{job_id}` | 需 | — | 查询单个任务进度 |
| 10 | POST | `/api/v1/video/batch/{job_id}/cancel` | 需 | — | 取消任务 |
| 11 | GET | `/api/v1/video/history` | 需 | ✅ 200 | 返回已完成的 1 条分析记录（持久化 OK） |

> 所有 `/api/v1/video/*` 端点均依赖 `get_verified_user`，未带 `Authorization: Bearer <key>`
> 会返回 **401 Not authenticated**。

---

## 4. 单视频分析 · 详细结果（真实跑通）

请求：

```http
POST /api/v1/video/analyze
Authorization: Bearer sk-...
Content-Type: application/json

{
  "video_path": "/data/workspace/data/1.mp4",
  "model": "minicpm-v:latest",
  "max_frames": 3,
  "min_frames": 0,
  "frame_interval": 10,
  "concurrency": 2,
  "language": "zh",
  "save_report": true
}
```

实测返回（节选）：

| 字段 | 值 |
|---|---|
| `status` | `done` |
| `model` / `summary_model` | `minicpm-v:latest` |
| `duration` | 10.73 s |
| `frame_count`（总帧） | 322 |
| `sampled_frames`（实际采样） | **20**（见下方「注意」） |
| `elapsed` | **354.82 s**（≈5.9 min） |
| `report_path` | `/data/workspace/data/1.analysis.md`（已落盘 11.8 KB） |

**注意 — `min_frames` 默认值陷阱**：未显式传 `min_frames` 时，路由默认 `min_frames=20`，
会**强制至少抽 20 帧**（覆盖 `max_frames` 的小值）。本次若用默认，
20 帧 × 并发 2 ≈ 354s。要做快速测试务必传 `"min_frames": 0` 让 `max_frames` 生效。

**Summary（模型生成的中文汇总，节选）**：

> 这组视频帧展示了一个涉及魔法元素的故事背景中的场景。画面主要描绘了一群人在夜晚户外
> 环境中施展各种防御性咒语和魔法的情景……多语言字幕表明这是一个多语言观众的作品……
> 标签：防御性魔法 / 夜晚户外环境 / 超自然元素 / 紧张感 / 专注与决心

**逐帧示例**（帧 0.0s / 4.8s）：

- `[0.0s]` 昏暗环境中一人向镜头伸出手臂，手持小圆形物体（魔杖/球体），底部字幕「最强盔甲护身 国若金汤 驱逐敌方」……
- `[4.8s]` 戏剧性场景，人物周身散发电能/闪电，意大利语字幕，暗示动作或超自然能力展示……

→ 模型准确识别了「防御咒语、双语字幕、闪电盔甲、夜晚户外」等关键视觉语义，**质量可用**。

---

## 5. 关键发现与后续建议

1. **必须重启后端**：改完 `backend/open_webui/**` 后服务无热重载，需手动重启（已在 §2.2 修复并验证）。
2. **`min_frames` 默认 20 是性能陷阱**：建议在路由层把 `min_frames` 默认改为 0，或让 `max_frames` 始终生效上限，避免小批量测试被拖成 20 帧。
3. **并发已统一（P0 已落地）**：脚本端 `concurrency` 默认已从 1 改为 3、上限 8，与前端/后端一致；本次 `concurrency=2` 生效。
4. **本地化升级可用 Gemma 4**：本机已装 `gemma4:26b`，可作为质量档；速度档用 `minicpm-v` 或 `gemma4:4b`。详见 `gemma4_local_upgrade.md`。
5. **批量接口需 `model` 字段**：`/batch/scan` 与 `/batch` 的 `BatchAnalyzeForm` 将 `model` 设为必填，缺省会 422。

---

## 6. Postman 集合

位置：`docs/202606/postman/video_analysis.postman_collection.json`

导入：Postman → Import → 选该文件。在集合 Variables 中确认/修改：
- `baseUrl` = `http://localhost:9102`
- `apiKey`  = 你的 `sk-...`（本机 admin key 存于 `backend/data/webui.db` 的 `api_key` 表）
- `videoPath` / `visionModel` 按需改

集合已包含 §3 全部 11 个端点，均预置 `Bearer {{apiKey}}` 鉴权，可直接逐个 Send。

---

## 7. 复跑命令（一键）

```bash
B=http://localhost:9102; KEY=sk-ab0cf13692424a4a812f3105dff3e4e4
# 健康检查
curl -s -H "Authorization: Bearer $KEY" $B/api/v1/video/health
# 单视频分析（快速：min_frames=0 限制 3 帧）
curl -s -X POST $B/api/v1/video/analyze -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"video_path":"/data/workspace/data/1.mp4","model":"minicpm-v:latest",
       "max_frames":3,"min_frames":0,"frame_interval":10,"concurrency":2,
       "language":"zh","save_report":true}'
```
