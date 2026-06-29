# 视频分析系统 — 工作进度与技术交接文档

> 最后更新：2026-06-29　｜　用途：上下文恢复 / 工作交接

---

## 一、运行环境（已确认）

| 项 | 值 |
|---|---|
| OS | TencentOS Server 3.2 (CentOS 8 兼容)，**容器环境无 systemd** |
| Go | 1.20.4 (`/opt/codev/golang/bin/go`)，**不支持 go.mod 声明 1.21+** |
| Node | v18.12.0 (`/opt/codev/nodejs/bin`) |
| Python(venv) | `my-webui/venv/bin/python` = 3.11，已装 cv2 4.13.0 + numpy 2.4.6 |
| Ollama | 运行中 `http://localhost:11434`，模型：`qwen2.5vl:7b`(视觉)、`qwen2.5:14b`、`gemma4:26b`、`qwen3:8b` |
| 测试视频 | `/data/workspace/data/`：1.mp4(1.2M)、test_01.mp4(2.2G)、qiji臻彩效果.mov(2G) 等 |

---

## 二、本次会话已完成的工作

### 2.1 已部署的两套系统（均成功运行）
1. **分布式电商平台** `/data/workspace/gin_web/shop_im_express_users`
   - 装好 Redis/MySQL8/MongoDB6；容器内 MySQL 需 `cp /usr/libexec/mysqld /tmp/mysqld` 才能执行
   - 所有 go.mod 从 1.21 降到 1.20；jwt/v5 从 5.3.1 降到 5.2.0（5.3.1 需 go1.21 的 slices 包）
   - 5 个 Go 服务 + 2 前端全部启动；产出 `deploy/auto-deploy.sh` + `deploy/DEPLOYMENT_GUIDE.md`
   - vite.config.js 改 `allowedHosts: true` + `host:'0.0.0.0'` 解决域名访问
2. **汇率系统** `/data/workspace/gin_web/rate_system/exchange-rate`
   - go.mod 1.25.0→1.20；端口实际是 **8088**（config.go），非脚本写的 8080
   - 产出 `daemon-start.sh` + `API_DOCUMENTATION.md`

### 2.2 视频分析系统 P0 止血修复（★当前主线，已完成并验证）

**项目**：`/data/workspace/my-webui`（基于 Open WebUI 二次开发的离线视频理解引擎）

**两套并行实现**（重要架构债，能力不对等）：
- A. 后端 API：`backend/open_webui/routers/video_analysis.py`（仅基础抽帧+音频）
- B. 离线脚本：`scripts/video_tasks.py`（99KB，全部高级功能；`video-tasks.json` 驱动）

**已修复的 4 个崩溃级 bug**（均已 `py_compile` + venv py3.11 双验证通过）：

1. **[B] `_FRAME_FILES` 注解冲突（致命，全脚本无法编译）**
   - 第 192/616 行函数内 `global _FRAME_FILES`，第 293 行模块级用了带注解赋值
   - CPython 规则：被任何函数 global 过的名字，模块级不能再注解赋值
   - 修复：293 行 `_FRAME_FILES: dict[...]={}` → `_FRAME_FILES = {}  # type: dict[float, tuple[str, str]]`
   - **意味着脚本 B 此前从未真正跑通过**

2. **[B] `run_task()` 变量"先用后定义"**
   - `ocr_enabled`(1734行用/2003行定义)、`_img_dir`/`quality_config`/`dedup_config`/`scene_enhanced`(1759/1818行用/1828-1865行定义)
   - 修复：把这些配置定义块**整体提前**到抽帧之前（`started=time.time()` 之后、GPU检测之前）；删除 2003 行重复定义

3. **[B] `run_task()` 重复抽帧**
   - 原 1818 行(else分支)抽一次，1867-1874 行又抽一次（覆盖+浪费一倍解码）
   - 修复：删除 1867-1874 第二次 `extract_frames`，保留 `if not frames` 检查

4. **[A] 后端 `VideoAnalyzeResponse` 缺 `status` 字段**
   - 694/716/928 三处访问 `result.status=='done'` → AttributeError，导致 /analyze、流式、批量收尾崩溃、历史永远写不进
   - 修复：模型加 `status: str = 'done'`

**其他发现**：`analyze-remote.sh` 第25行硬编码 API Key（`sk-ab0cf136...`），建议清理+清 git 历史。

---

## 三、系统机制与功能（评估结论）

### 处理流水线
```
probe_metadata → extract_frames(auto/count/interval/scene + 质量过滤 + pHash去重)
→ 逐帧VLM描述(Ollama/MLX, ThreadPool并发, 文件级缓存)
→ whisper音频转写(可选) → PaddleOCR硬字幕(可选)
→ qwen2.5汇总 → write_report(.analysis.md / .notebook.md)
```

### 与主流视频平台的三大本质差距
1. **时序理解缺失**：逐帧独立描述后文字拼接，丢失帧间动态/事件因果（主流用原生视频模型）
2. **无检索能力**：报告是孤立 Markdown，无向量库、无法跨视频自然语言检索定位
3. **工程可靠性弱**：后端内存字典(上限20、重启丢)、脚本顺序跑、无断点续跑/失败重试

---

## 四、P1 落地计划（★下一步，进行中）

**目标**：支撑"海量本地视频"，在不破坏现有可用链路的前提下增量改造 `scripts/video_tasks.py`。

### P1-a 持久化任务队列（优先，风险低）
- SQLite 任务表，`video_path` 为幂等键，状态机 `pending/running/done/failed`
- 在 `main()` 外层包裹：展开任务→注册入库(已done跳过)→执行前标 running→成功 done/异常 failed+错误信息
- 支持 `--resume`(只跑未完成)、`--retry-failed`
- 进程重启可恢复
- **不侵入 run_task 核心逻辑**，只在外层包裹

### P1-b 分级并发调度（次优，改动大）
- 流水线拆分：解码(CPU/IO) → VLM推理(GPU串行) → 汇总(GPU) → 写盘(IO)，各级独立并发度

### 关键代码定位（video_tasks.py，修复后行号会有偏移）
- `def run_task(task, defaults, ollama_url)` ~1660：单任务编排
- `def _expand_tasks(...)` ~662 区域：video_dir 展开为逐视频任务
- `def main()` ~2274：读 video-tasks.json → 展开 → 遍历 run_task
- 启动入口：`start-video-analysis.sh` 第182-192行，`RUN_VIDEO_TASKS=1` 时调脚本

### video-tasks.json 当前配置
- task1: `video_dir=/data/workspace/data` 非递归，scene+enhanced，dedup+quality过滤，audio+whisper small
- task2: `1.mp4` notebook_mode education 风格
- 全局：concurrency=3, cache_enabled, gpu_enabled, skip_existing

---

## 五、P2/P3 后续方向（评估已给出，未开工）
- P2：RAG-over-video 向量检索（最大价值杠杆）、结构化 JSON 输出、轻量时序增强(prompt注入时间戳+镜头边界)
- P3：内容指纹去重、合规审核钩子、可观测看板、升级原生视频模型(已有 qwen2.5vl:7b)
- 架构：合并 A/B 两套实现为 `video_core` 公共模块
