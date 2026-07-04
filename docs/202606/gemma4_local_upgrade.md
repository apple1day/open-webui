# Gemma 4 本地化升级验证报告（P0）

> 目标：验证用 Google 近期发布的本地多模态大模型 **Gemma 4** 经 Ollama 替换当前 VLM，
> 评估其逐帧描述质量与延迟/显存代价，给出本地化升级建议。
> 配套代码改动见 `scripts/video_tasks.py`（并发默认值统一 + `KNOWN_LOCAL_VLM_MODELS` 候选登记）。

## 1. 背景

- 当前默认 VLM：`minicpm-v:latest`（~5.2GB，轻量多模态）。
- Google **Gemma 4**（2026-04 发布，Apache 2.0 可商用）是开源**多模态**模型家族，
  含 2.3B / 4B / 26B MoE / 31B Dense 等档位；2026-06 又推出量化感知训练（QAT）checkpoint，
  使 12B 多模态可在 16GB 内存笔记本本地跑。运行路径支持 **Ollama 一键部署**、llama.cpp、vLLM、
  Google AI Edge / LiteRT-LM（端侧）。
- Ollama 与现有 `ollama_chat()` 接口完全兼容（同一 `/api/chat`），因此替换是**即插即用**的。

## 2. 本机验证（真实运行）

环境：Ollama 在线，已安装 `minicpm-v:latest`(5.2GB) 与 `gemma4:26b`(17GB)。
样本：`/data/workspace/data/1.mp4`（10.7s，322 帧），抽 10%/50%/90% 三帧做视觉描述对比。

| 模型 | 体积 | 帧1 | 帧2 | 帧3 | 平均/帧 |
|---|---|---|---|---|---|
| `minicpm-v:latest` | 5.2GB | 48.4s | 34.8s | 33.9s | **~39s** |
| `gemma4:26b` | 17GB | 201.8s | 160.5s | 171.2s | **~178s** |

**质量观察**（两者均准确识别暗夜/闪电/盔甲/画面字幕/bilibili 水印）：
- MiniCPM-V 描述更"话痨"，但字幕文字抓取到位。
- Gemma 4:26b 描述更精炼、语序更自然，质量**持平或略优**。

**结论**：Gemma 4 可作为本地 VLM 的**直接替代品**（接口零改动）；
但 26B 档延迟约为 MiniCPM-V 的 **4.5 倍**、显存 **3.3 倍**。

## 3. 如何切换到 Gemma 4

改 `video-tasks.json`（或前端表单的 `model` 字段）即可，无需改代码：

```jsonc
{
  "default_model": "gemma4:26b",   // 质量档（离线深度分析）
  // "default_model": "gemma4:4b", // 速度档（低显存/高并发）
  "tasks": [ { "model": "gemma4:26b", "concurrency": 3 } ]
}
```

`scripts/video_tasks.py` 已登记候选（`KNOWN_LOCAL_VLM_MODELS`）：
`minicpm-v:latest` / `gemma4:26b` / `gemma4:12b` / `gemma4:4b` / `llama3.2-vision:11b` / `qwen2.5-vl:7b`。
本机已验证存在：`minicpm-v:latest`、`gemma4:26b`、`llama3.2-vision:11b`。

若未安装，先拉取：`ollama pull gemma4:12b`（标签以 `ollama search gemma4` 实际返回为准）。

## 4. 并发（P0 同步修复）

- 脚本端 `run_task` 原默认 `concurrency=1`（串行），与**前端表单默认 3**、**后端路由 `DEFAULT_CONCURRENCY=3`** 不一致。
- P0 已统一：脚本端新增 `DEFAULT_CONCURRENCY=3` 与 `MAX_CONCURRENCY=8` 安全上限，
  `concurrency = max(1, min(请求值, 8))`。三端现在一致（默认 3，上限 8）。
- 帧级并发早由 `process_frames_concurrently()`（ThreadPoolExecutor）实现，默认 1 时退化成串行；
  现在默认 3 即生效，单视频多帧并行调 Ollama。

## 5. 升级建议（速度/质量分级）

结合 P1-a 的"能力位掩码"思路，建议把 VLM 做成**分级**而非单一默认：

| 档位 | 模型 | 适用 |
|---|---|---|
| 速度档（默认） | `minicpm-v:latest` 或 `gemma4:4b` | 高并发实时分析、低显存设备 |
| 均衡档 | `gemma4:12b` | 常规离线分析 |
| 质量档 | `gemma4:26b` | 离线深度分析、对精度敏感场景 |

下一步（P1-b Feature 化）可把"VLM 档位选择"做成 `VideoFeature` 的参数，由能力位掩码 + 设备显存探测自动择档。

## 6. 验证脚本（可复现）

`/tmp/verify_gemma4.py`（已运行）：用 `cv2` 抽帧 → `video_tasks.chat('ollama', url, model, prompt, images=[b64], max_tokens=200)`
对两模型各 3 帧对比。后续换模型只需改 `MODELS` 列表重跑。
