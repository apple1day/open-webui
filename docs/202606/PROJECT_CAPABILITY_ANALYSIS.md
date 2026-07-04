# my-webui 项目整体能力分析

> **定位**：本报告对 `my-webui` 视频大模型分析系统做一份**整体能力画像**——项目定位、技术栈、架构、完整能力清单、研发进度、安全债与演进方向。
>
> **数据来源**：全部结论基于 2026-07-04 的代码实证扫描（`scripts/video_tasks.py`、`backend/open_webui/routers/video_analysis.py`、`src/routes/(app)/video/`、`analyze-remote.sh`），关键结论均标注**文件 + 行号**。
>
> **关联文档**：
> - `MEDIA_ASSET_MANAGEMENT_SOLUTION.md` — 介质全生命周期管理方案（对标 Netflix / 腾讯视频 / AWS / C2PA）
> - `tvkplayer_architecture_playbook.md` — TVKPlayer 三大机制 → my-webui 优化翻译
> - `with3.md` / `with4.md` — 历次会话分析
>
> **版本**：v1.0 · 日期：2026-07-04 · 状态：评审稿

---

## 一、项目定位

`my-webui` 是**基于 Open WebUI 二次开发的本地离线视频理解系统**：给定本地视频，系统自动抽帧、用视觉大模型逐帧描述、转写音轨、识别硬字幕，最后由文本大模型结合声画汇总成结构化分析报告。

核心特征：**完全离线、直连本机 Ollama、无登录无后端即可运行脚本**。它不面向 C 端观众做"防盗版"，而是面向 B 端研究 / 合规做"AI 分析溯源"（详见 `MEDIA_ASSET_MANAGEMENT_SOLUTION.md` §三）。

---

## 二、技术栈

| 层 | 技术 | 用途 | 实证 |
|----|------|------|------|
| 抽帧 / 解码 | OpenCV（headless） | 读取视频参数、抽帧；不依赖 ffmpeg | `video_tasks.py:40-45` 注释 + 导入 |
| 视觉理解 | Ollama `qwen2.5vl:7b` | 逐帧视觉描述 | `video_tasks.py` 头部设计说明 |
| 文本汇总 | Ollama `qwen2.5:14b` | 结合声画汇总 | `video_tasks.py:1459/1533` 提示词 |
| 音频转写 | faster-whisper（可选） | 音轨转写 + `.srt` 输出，内部用 PyAV 解码 | `video_tasks.py:12-13` |
| 硬字幕 OCR | PaddleOCR（可选） | 抽帧硬字幕识别 | `video_tasks.py:73-78` |
| 后端 | FastAPI（Open WebUI router） | REST / SSE 流式 | `video_analysis.py` 16 个端点 |
| 前端 | SvelteKit | `/video` 面板 + 历史页 | `src/routes/(app)/video/` |
| 运行环境 | TencentOS 容器（Go 1.20 / Node 18 / Python 3.11），无 systemd | 本地离线部署 | 历史文档 |

---

## 三、系统架构

系统存在**两套并行实现**，能力严重不对等（详见 §四），外加前端面板：

```
                 ┌────────────────────────────────────────────────────┐
                 │  前端 Svelte 面板  /video (+ history 子页)          │
                 │  src/routes/(app)/video/+page.svelte               │
                 │  lib/apis/video/index.ts（fetch 封装）              │
                 └───────────────────────┬────────────────────────────┘
                                         │ REST / SSE
                 ┌───────────────────────▼────────────────────────────┐
                 │  实现 A：后端 API                                   │
                 │  backend/open_webui/routers/video_analysis.py       │
                 │  16 端点：analyze/batch/stream/history/health       │
                 │  内存任务字典 _BATCH_JOBS（上限 20）                │
                 └───────────────────────┬────────────────────────────┘
                                         │ 调用（部分高级能力未覆盖）
   ┌─────────────────────────────────────▼──────────────────────────┐
   │  实现 B：离线脚本  scripts/video_tasks.py（2318 行）             │
   │  auto/count/interval/scene 抽帧 + 质量过滤 + pHash 去重          │
   │  + 增强镜头检测 + whisper + OCR + 笔记模式 + 长视频分段          │
   │  配置驱动：video-tasks.json                                     │
   └─────────────────────────────────────┬──────────────────────────┘
                                         │ 直连
                                 ┌───────▼────────┐
                                 │  本机 Ollama   │
                                 │  VLM + LM      │
                                 └────────────────┘
```

---

## 四、完整能力清单（代码实证）

### 4.1 离线脚本 `scripts/video_tasks.py`（2318 行）—— 分析深度全在它身上

| 能力 | 机制 | 实证（行号） |
|------|------|------------|
| **四种抽帧模式** | `auto / count / interval / scene` 分支 | `:216 / :228 / :233 / :636 / :645` |
| **帧质量过滤** | 模糊 / 过暗 / 过曝 / 静态帧阈值 | `DEFAULT_QUALITY_*` `:121-125`；分支 `:1740` |
| **感知哈希去重** | pHash 计算 + `deduplicate_frames` | `_HAVE_PERCEPTUAL_HASH :62-70`；`_perceptual_hash :1071`；`deduplicate_frames :1258` |
| **三维增强镜头检测** | HSV 直方图 + 帧间差值 + 边缘变化 | `scene_enhanced :187 / :218`；`_detect_scene_timestamps_enhanced :478` |
| **音频转写 + .srt** | faster-whisper，独立写字幕 | `transcribe_audio :1011`；头部 `:12-13` |
| **硬字幕 OCR** | PaddleOCR | `_HAVE_PADDLE_OCR :73-78`；`ocr_video_frames :1386` |
| **笔记模式** | `education / general / meeting` 三风格 | `DEFAULT_NOTEBOOK_MODE :117`；`DEFAULT_NOTEBOOK_STYLE :118`；提示词 `:1459 / :1533` |
| **长视频分段 + 跨段去重** | `split_video_for_processing` + 跨段去重 | `:136`；跨段去重 `:1807-1834` |
| **并发推理** | `ThreadPoolExecutor`，`max_workers=concurrency` | `process_frames_concurrently :821 / :840` |
| **GPU / MLX 后端** | 检测 nvidia/amd/apple/mps；`--backend mlx` | `detect_gpu :1175`；`auto_configure_gpu :1221`；`DEFAULT_MLX_* :95-96`；CLI `:2266` |
| **文件级缓存跳过** | `skip_existing` + `DEFAULT_CACHE_*` | `:1705`；`:110-111` |
| **CLI 参数** | `--config / --ollama-url / --backend / --video-dir / --recursive / --skip-existing` 等 | `main :2263-2280` |

> **已统一（P0）**：脚本端 `concurrency` 原默认 **1（顺序处理）**，现已改为 `DEFAULT_CONCURRENCY=3` 并 `max(1, min(..., MAX_CONCURRENCY=8))` 限 8，与前端表单、后端路由 `DEFAULT_CONCURRENCY=3` 一致（见 `scripts/video_tasks.py` 的 `DEFAULT_CONCURRENCY` / `MAX_CONCURRENCY`）。

### 4.2 后端 API `backend/open_webui/routers/video_analysis.py`（约 1400+ 行）—— 编排能力全在它身上

共 **16 个 REST 端点**：

| 端点 | 行号 | 说明 |
|------|------|------|
| `GET /health` | `:470` | 健康检查 |
| `POST /upload-and-analyze` | `:494` | 上传即分析 |
| `POST /upload-and-analyze/stream` | `:571` | 上传即分析（SSE） |
| `GET /models` | `:664` | 可用模型列表 |
| `POST /analyze` | `:686` | 单视频分析 |
| `POST /analyze/stream` | `:700` | 单视频分析（SSE 流式） |
| `POST /batch/scan` | `:954` | 扫描目录生成批量任务 |
| `POST /batch` | `:975` | 创建批量任务 |
| `GET /batch` | `:1055` | 批量任务列表 |
| `GET /batch/{job_id}` | `:1074` | 任务详情 |
| `POST /batch/{job_id}/cancel` | `:1083` | 取消 |
| `POST /batch/{job_id}/pause` | `:1094` | 暂停 |
| `POST /batch/{job_id}/resume` | `:1106` | 恢复（**batch job 级**） |
| `POST /batch/{job_id}/retry` | `:1118` | 重试（**batch job 级**） |
| `POST /analyze/upload` | `:1211` | 供 `analyze-remote.sh` 调用 |
| `GET /history` | `:1398` | 历史记录 |

任务状态管理：**内存字典** `_BATCH_JOBS: dict = {}`（`:797`，注释"内存注册表，随进程生命周期"），上限 `_BATCH_JOBS_MAX = 20`（`:798`）裁剪。`VideoAnalyzeResponse` 含 `status: str = 'done'`（`:114`）。

> **两套实现的关系**：脚本 B 拥有**全部分析深度**（scene_enhanced/dedup/quality_filter/notebook/segment），后端 A 拥有**全部编排能力**（批处理 scan/batch/pause/resume/retry、SSE 流式、历史）。两者是"分析深度 vs 编排能力"的不对等，而非简单的"B 全 A 无"——这也是后续 `video_core` 整合要解决的核心架构债。

### 4.3 前端 `src/routes/(app)/video/`

| 文件 | 体量 | 功能 |
|------|------|------|
| `+page.svelte` | 27.8 KB | 主面板：videoPath / model / frameInterval / maxFrames / minFrames / **concurrency（默认 3）** / includeAudio / whisperModel / saveReport / prompt；SSE 进度、日志、导出 md/html/pdf |
| `history/+page.svelte` | 7.25 KB | 历史记录子页 |
| `lib/apis/video/index.ts` | 6.97 KB | fetch 封装：`getVideoHealth / getVideoModels / analyzeVideo / analyzeVideoStream(SSE) / scanVideoDirectory / startVideoBatch / getVideoBatch / listVideoBatches / cancel/pause/resume/retry / getVideoHistory / deleteVideoHistory`；类型 `VideoAnalyzeForm / BatchAnalyzeForm / VideoHistoryItem` |

> **前端 - 后端 - 脚本 三端 concurrency 已统一（P0）**：默认 3、上限 8，缺口已消除。详见 `gemma4_local_upgrade.md`。

---

## 五、研发进度（基于代码实证）

### ✅ P0 止血修复 — 已全部落地
1. 后端 `VideoAnalyzeResponse` 补齐 `status: str = 'done'`（`:114`）→ `/analyze`、流式、批量收尾不再崩溃。
2. 脚本变量顺序修复：`quality_config` / `_img_dir` 在抽帧前定义；`if 分段 / else 普通` 二选一分支消除重复抽帧。

### ⏳ P1 — 进行中 / 未落地（实证确认）
在 `video_tasks.py` 全局搜索 `sqlite / TaskQueue / --resume / --retry-failed / video_core` —— **均 0 命中**（`routers/` 同样未发现 `TaskQueue / sqlite / video_core`）。

| P1 项 | 状态 | 实证 |
|-------|------|------|
| 持久化任务队列（SQLite） | ❌ 未落地 | 脚本无 `--resume`；后端仅 `batch` job 级 resume（`:1106`） |
| 全局断点续跑 `--resume` | ❌ 未落地 | 仅 batch job 级，非全局任务级 |
| 全局失败重试 `--retry-failed` | ❌ 未落地 | 仅 batch job 级 retry（`:1118`） |
| `video_core` 公共模块 | ❌ 未落地 | 两套实现仍各自维护 |
| 资源感知分级并发 | ⚠️ 部分 | 脚本 `concurrency` 默认 1；前端默认 3，未做解码/VLM/汇总/写盘四级拆分 |

**进度结论**：
- **功能堆叠完成度 ≈ 80%**：高级分析能力齐全，P0 崩溃点已清零，链路稳定可跑通。
- **工程可靠性完成度 ≈ 55%**：缺任务持久化 / 全局断点续跑 / 全局重试，进程重启即丢状态——这正好卡在"海量本地视频"的命门上。

---

## 六、已知安全债

| # | 问题 | 证据 | 等级 |
|---|------|------|------|
| 1 | `analyze-remote.sh` **硬编码 API Key** | 第 25 行 `API_KEY="${API_KEY:-sk-ab0cf13692424a4a812f3105dff3e4e4}"`（明文 `sk-` 密钥）；第 24 行 `API_BASE` 指向内网 `http://jiaininghou-any9.devcloud.woa.com:9102`；第 45 行仅弱告警但未移除 | 🔴 高 |
| 2 | 分析报告 / 抽帧截图**明文落盘** | 产物 `.analysis.md` / `.srt` / 抽帧 `.jpg` 全明文 | 🟡 中 |
| 3 | `~/.video_analysis_cache` **明文缓存** | 缓存目录默认开启 | 🟡 中 |
| 4 | 任务状态仅内存 | `_BATCH_JOBS` 重启即丢（见 §五） | 🟡 中 |

> 安全债修复路径见 `MEDIA_ASSET_MANAGEMENT_SOLUTION.md` §4.4（加密 + KMS）+ 附录 C；P0 即包含清理项 1。

---

## 七、关键缺口与风险

| 维度 | 现状 | 风险 |
|------|------|------|
| **工程可靠性** | 内存字典 + 文件跳过，无持久化队列 | 海量视频一上量即因中断丢进度 / 难运维 |
| **两套实现** | 脚本 B 与后端 A 重复维护 | 改一处漏一处，长期维护成本高 |
| **可发现性** | 文件名 + 路径，无媒资 ID | 无法检索、无法跨视频定位 |
| **时序理解** | 逐帧独立描述后文本拼接 | 丢失帧间动态 / 因果，无"事件"概念 |
| **检索能力** | 仅孤立 Markdown | 无向量库、无法跨视频自然语言检索 |
| **介质安全** | 全链路零加密 / 零水印 / 零版权保护 | 处理有版权/隐私视频时裸奔 |
| **内容安全** | 无审核 / 无 Deepfake 检测 | 敏感内容无前置拦截 |

---

## 八、演进方向（指向已有方案）

已有的两份方案已给出完整路线图，本分析不重复，仅做索引：

- **介质安全与全生命周期**：`MEDIA_ASSET_MANAGEMENT_SOLUTION.md` — 采集/编目/转码/加密/分发/水印/审核/归档 八大子系统 + 4 阶段路线图（P0 已完成，P1 起）。
- **架构机制借鉴**：`tvkplayer_architecture_playbook.md` — TVKPlayer 三大机制翻译成 P1-a（能力位掩码+设备查询）/ P1-b（Feature 化）/ P1-c（资源对象化）。

**最优先建议**（共识）：立即推进 **P1-a 最小切片**（能力位掩码 + `detect_device_capability()` + 降级逻辑，零风险、不动 `run_task` 核心），并顺手**清理 `analyze-remote.sh` 硬编码 API Key**（安全债 #1）。

---

## 附录 A：关键文件索引

| 文件 | 行数 | 角色 |
|------|------|------|
| `scripts/video_tasks.py` | ~2318 | 离线分析脚本（分析深度） |
| `backend/open_webui/routers/video_analysis.py` | ~1400+ | 后端 API（编排能力） |
| `src/routes/(app)/video/+page.svelte` | 27.8 KB | 前端主面板 |
| `src/routes/(app)/video/history/+page.svelte` | 7.25 KB | 历史页 |
| `src/lib/apis/video/index.ts` | 6.97 KB | 前端 API 封装 |
| `video-tasks.json` | 1.76 KB | 离线任务配置 |
| `analyze-remote.sh` | 3.63 KB | 远程上传客户端（含安全债） |

## 附录 B：实证方法

本报告所有能力声明均来自 2026-07-04 一次性只读扫描（`search_file` / `search_content` / `read_file`），由 code-explorer 子代理执行并交叉核对，关键结论标注文件 + 行号。如需复核，可直接打开上述文件对应行。

---

**报告结束**

> 本分析为 `my-webui` 视频大模型系统的整体能力画像，作为后续架构改造与方案落地的基线。与 `MEDIA_ASSET_MANAGEMENT_SOLUTION.md`、`tvkplayer_architecture_playbook.md` 互为补充。
