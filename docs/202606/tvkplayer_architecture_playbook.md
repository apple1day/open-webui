# TVKPlayer 架构借鉴手册
## —— 把腾讯视频播放器的三大机制翻译成 my-webui 的优化方向

> **目的**：把 `TVKPlayer` 知识库中三个经过大规模生产验证的架构机制，翻译为 `my-webui` 视频大模型分析系统的下一步优化方向，并作为 `MEDIA_ASSET_MANAGEMENT_SOLUTION.md` 的落地补丁。
>
> **关联文档**：`MEDIA_ASSET_MANAGEMENT_SOLUTION.md`（主方案）、`with3.md`、`with4.md`
>
> **版本**：v1.0 · 日期：2026-07-04 · 状态：评审稿

---

## 一、三大机制（权威定义提炼）

### 机制 A —— Multi-DRM 三套枚举（"支持什么"与"用哪个"解耦）

TVKPlayer 不是用一个枚举表达 DRM，而是用**三套独立枚举**：

| 枚举 | 文件 | 回答的问题 | 形态 |
|------|------|-----------|------|
| `TVKDrmType` | `tvk_drm_type.h` | **当前用哪个** | 普通枚举：None=-1 / Widevine=0 / Unitend=1 / ChinaDrm20=2 / Fairplay=3 / Self=4 |
| `TVKDrmSupportType` | `tvk_cgi_defines.h` | **设备支持哪些** | 位掩码：Common=0x1 … MultiDrm=0x200 / TXVDrm=0x400 |
| `TVKMultiDrmSupportType` | `tvk_cgi_defines.h` | **Multi-DRM 能力** | 位掩码：Fairplay=0x10 / Widevine=0x20 / ChinaDrm20=0x80 |

配套：`GetDrmCapability()` 前置查询 + `TVKDrmCap`(支持/不支持) + L1(硬件 TEE)/L3(软件) 安全分级 + `kPlayControlEnableDrmLevelControl=0x1000`（后台按安全级别限清晰度）。

**范式**：`能力位掩码` + `当前枚举` + `能力查询前置` + `安全降级`。两个维度解耦——能用什么、现在用哪个，互不污染。

### 机制 B —— Feature 工厂（可插拔、独立开关/升级/容错）

- `ITVKFeature` 抽象基类：纯虚函数规范生命周期 `OnLoad` / `Process` / `OnUnload`。
- `TVKFeatureGroup`：集中管理点播 / 直播 / 重试 / 解码器策略列表。
- `CreateVodFeatureList()` / `CreateLiveFeatureList()`：工厂函数，按平台与场景**动态组装** Feature 列表。
- `TVKFeatureUtil` / `TVKFeatureParamGroup`：起播位置计算、CGI 解析、能力值合并、参数实例管理。
- 具体实现继承 `ITVKFeature`：`TVKVodWidevineDrmFeature` / `TVKVodFairplayDrmFeature` / `TVKVodChinaDrm20Feature` / `TVKVodTXVDrmFeature` / `TVKVodSoftWatermarkFeature` / `TVKVodDynamicWatermarkFeature`。

**范式**：DRM、水印、反截屏、字幕都是**独立 Feature 插件**，不写死在主流程里——独立开关、独立升级、独立容错。

### 机制 C —— qqliveasset 资源即对象（Asset as Object）

- `ITVKAsset` 抽象基类：`Clone()` / `GetAssetType()` / `SetConfigParam()` / `GetConfigParam()` / `IsValid()` / `Release()`。
- `TVKAssetType` 枚举：9 种资源形态（URL / 直播PID / 在线Vinfo / 离线Vinfo / VID点播 / 快播 / 文件描述符 / Android fd / 模拟直播）。
- `TVKAssetFactory`：静态工厂方法统一创建各类资源（`CreateQQLiveVodVidAsset` 等），返回智能指针。
- `TVKQQLiveAssetPlayerContext`：**依赖注入**中心，注入播放内核、状态管理、策略选择器、Feature 组、后处理器。
- `TVKQQLiveAssetPlayerProxy`：**代理模式**封装统一接口 + 预处理/后处理（权限、校验、监控）。
- `Release*` 释放函数：生命周期防泄漏。

**范式**：资源统一抽象 → 工厂创建 → 上下文注入 → 代理横切 → 显式释放。

---

## 二、机制 → my-webui 映射

| TVKPlayer 机制 | my-webui 现状 | my-webui 应演进为 |
|---------------|--------------|------------------|
| A. 能力位掩码 + 当前枚举 + 能力查询 + 降级 | 能力散落在代码（scene_enhanced/dedup/…），无统一声明；无设备能力查询，缺能力就崩 | `VideoTaskCap` 位掩码 + `detect_device_capability()` 前置查询 + 能力缺失即降级 |
| B. Feature 工厂 | 脚本 B 有 7 个高级能力，后端 A 只有基础；两套实现重复维护 | `VideoFeature` 基类 + `CreateProcessingFeatureList(cap)` 工厂组装，A/B 共用一个引擎 |
| C. 资源即对象 + 上下文 DI + 代理 + Release | "filename + path" 直接传，无抽象、无生命周期管理 | `IVideoAsset` + `VideoAssetFactory` + `VideoAnalysisContext`（DI）+ `Release` 防泄漏 |

---

## 三、下一步优化方向（按优先级）

### P1-a · 任务能力位掩码 + 设备能力查询 —— 移植机制 A
- 定义 `VideoTaskCap` 位掩码常量：`kCapExtractFrame=0x1 / kCapQualityFilter=0x2 / kCapDedup=0x4 / kCapOcr=0x8 / kCapAsr=0x10 / kCapSceneDetect=0x20 / kCapNotebook=0x40 / kCapSegment=0x80`。
- `run_task` 调度层加 `detect_device_capability()`：GPU 可用？Ollama 在线？OCR 模型在？缺失即降级并记 warning，**不崩**。
- 价值：消除"没 GPU 就 OOM/崩溃"的脆弱性，是机制 A"能力查询前置→安全降级"的直接移植。

### P1-b · Feature 化重构 —— 移植机制 B（根治"两套实现"架构债）
- 把脚本 B 的 7 个高级能力抽成 `video_core/features/` 下 7 个 `VideoFeature(OnLoad/Process/OnUnload)`。
- `CreateProcessingFeatureList(cap)` 按位掩码动态组装。
- 后端 `routers/video_analysis.py` 直接 import 此引擎；每个 Feature 独立 try/except 容错。
- 这是 `with3.md` 中"video_core 公共模块"的精准形态——不是简单抽函数，而是**插件化分析引擎**。

### P1-c · 资源对象化 —— 移植机制 C（为媒资 ID 化铺路）
- 定义 `IVideoAsset`（对标 `ITVKAsset`）：持有 `source_path / sha256 / asset_id / metadata`，提供 `open()` / `release()`。
- `VideoAssetFactory.create(local_path | url | vinfo)` 统一入口。
- `VideoAnalysisContext`（对标 `TVKQQLiveAssetPlayerContext`）注入解码器 / VLM / OCR / 任务状态 / 后处理。
- 价值：为方案 4.1/4.2 的媒资 ID 化提供统一抽象，并为阶段 2"消费别人转好的 HLS 加密流"预留 URL/在线 Vinfo 资源形态。

---

## 四、最小可动手切片（建议先落 P1-a）

P1-a 不动 `run_task` 核心逻辑、风险最低、立刻提升健壮性，是按 TVKPlayer 模式改造的"第一刀"：

1. 在 `scripts/video_tasks.py` 新增 `VideoTaskCap` 位掩码常量集合。
2. 新增 `detect_device_capability()`：探测 GPU / Ollama 连通性 / OCR 模型存在性，返回位掩码。
3. 在 `main()` 调度层：解析任务声明的 `cap`，与设备 `cap` 取交集，缺失能力记录 warning 并降级（如无 OCR 则跳过 OCR 步骤），而非抛异常。

落地后即可在"无 GPU / 模型未拉起"的退化环境下稳定跑通，并天然具备往后扩展 Feature 的能力声明基础。

---

## 五、与主方案的关系

- 本手册是 `MEDIA_ASSET_MANAGEMENT_SOLUTION.md` 的**落地补丁**。
- 主方案 §2.2.1 已据本手册修正为真实的三套枚举（`TVKDrmType` / `TVKDrmSupportType` / `TVKMultiDrmSupportType`）。
- 主方案 §阶段 1 的"资源感知分级并发""统一两套实现""媒资 ID 化"分别对应本手册的 P1-a / P1-b / P1-c。

**下一步**：建议先实施第四节的 P1-a 最小切片，再按 P1-b → P1-c 顺序推进 Feature 化与资源对象化。
