# 视频介质管理方案
## ——对标 Netflix / 腾讯视频 / AWS / 主流流媒体的"采集-编目-处理-分发-消费"全链路工程规范

> **范围**：本方案定义视频"介质"（media asset）从生产入库到消费播放、再到归档退役的**全生命周期管理**规范，含**采集/编目/转码/加密/分发/水印/审核/归档**八大子系统，并落到当前 `my-webui` 视频大模型分析系统的演进路径。
>
> **版本**：v1.0  ·  日期：2026-06-30  ·  状态：评审稿  ·  关联文档：`docs/202606/with3.md`、`with4.md`、`PROGRESS_HANDOFF.md`
>
> **对标对象**：
> - **Netflix**（Open Connect / Pipeline / DRM）
> - **腾讯视频 TVKPlayer**（Multi-DRM / 媒资后台）
> - **腾讯云数据万象 CI**（转码打包一体化）
> - **AWS Media Asset Management**（MAM 标准定义）
> - **C2PA / Content Authenticity Initiative**（介质真实性国际标准）

---

## 目录

- [一、背景与目标](#一背景与目标)
- [二、行业对标：四家主流方案的"介质层"长什么样](#二行业对标四家主流方案的介质层长什么样)
- [三、介质管理总体架构（三域 + 一切）](#三介质管理总体架构三域--一切)
- [四、八大子系统规范](#四八大子系统规范)
  - 4.1 采集入库子系统
  - 4.2 媒资编目（MAM）子系统
  - 4.3 转码与打包流水线
  - 4.4 加密与 DRM 子系统
  - 4.5 分发与播放子系统
  - 4.6 水印与溯源子系统
  - 4.7 内容审核与内容安全子系统
  - 4.8 存储与归档子系统
- [五、与当前 my-webui 的差距矩阵](#五与当前-my-webui-的差距矩阵)
- [六、演进路线图（4 个阶段、12 个月）](#六演进路线图4-个阶段12-个月)
- [七、关键风险与对策](#七关键风险与对策)
- [八、附录](#八附录)

---

## 一、背景与目标

### 1.1 当前现状（基于 `my-webui` 实证）

`my-webui` 是一套**基于 Open WebUI 二次开发的本地离线视频大模型分析系统**，核心链路为：

```
探测元数据 → OpenCV抽帧(质量过滤+pHash去重) → VLM逐帧描述(Ollama/MLX并发)
→ faster-whisper音频转写 → PaddleOCR硬字幕 → qwen2.5汇总
→ 输出 .analysis.md / .notebook.md
```

存在两套并行实现：

| 实现 | 文件 | 能力 | 谁在用 |
|------|------|------|--------|
| A. 后端 API | `routers/video_analysis.py` (1413 行) | 基础抽帧+音频，Web/REST 在线，内存批处理 `_BATCH_JOBS` | 前端 `/video` 面板 |
| B. 离线脚本 | `scripts/video_tasks.py` (2348 行) | 全部高级功能 | `video-tasks.json` 驱动 |

**关键短板（与"介质管理"对照）**：

1. **全链路零加密、零水印、零版权保护层**——直接读裸 `mp4/mov`，产物明文落盘。
2. **无元数据/编目体系**——只有 `ffprobe` 探测出的基础元信息（时长/分辨率/编码），没有媒资 ID、版权声明、合规标签、生命周期状态。
3. **无任务持久化**——`_BATCH_JOBS` 内存字典、`skip_existing` 文件级跳过，进程重启即丢状态。
4. **无冷热分层与归档**——处理完的中间产物（抽帧 `.jpg`、缓存 `.json`、分析报告 `.md`）都堆在同一磁盘，无生命周期管理。
5. **AI 能力游离于介质流水线之外**——VLM/OCR/Whisper 直接读明文本地文件，没有嵌入"媒资后台"的标准处理节点。

### 1.2 方案目标

把"视频大模型分析系统"从一个**单点 AI 工具**演进为一套**具备工业级介质管理能力的工程平台**，做到：

| 维度 | 现状 | 目标 |
|------|------|------|
| **可发现** | 文件名 + 路径 | 媒资 ID + 完整元数据 + 编目 + 全文检索 |
| **可处理** | 串行脚本 + 内存字典 | 持久化任务队列 + 工作流编排 + 资源感知并发 |
| **可保护** | 零加密 | KMS 信封 + CENC 风格加密 + 取证水印 |
| **可消费** | 明文 Markdown | 多端适配 + 鉴权 + JWT 令牌 |
| **可归档** | 无 | 冷热分层 + 生命周期策略 + 合规审计 |
| **可审计** | 无 | 操作日志 + 内容凭证 C2PA + 血缘追踪 |

### 1.3 不在本方案范围

- **DRM 客户端实现**（播放器侧解密、TEE 集成）——属于客户端/终端域，超出后端介质管理范围。
- **AIGC 生成链路**（文生视频、图生视频）——属于生产域的子链路，独立方案。
- **CDN 网络层**（Open Connect、QCDN 自研分发）——属于基础设施层，与本方案通过接口对接而非自建。

---

## 二、行业对标：四家主流方案的"介质层"长什么样

### 2.1 Netflix —— 工业级媒资管线的标杆

> 来源：[InfoQ《深入解析 Netflix 后端架构与云服务的系统设计》](https://www.infoq.cn/article/7ozSkjX1sp49J8YBuMeN)、[Open Connect 官方白皮书](https://openconnect.netflix.com/Open-Connect-Overview.pdf)

#### 2.1.1 媒资管理

| 环节 | 做法 |
|------|------|
| **源内容接入** | 从制作公司获取高质量原始视频，单片平均 500 MB；日均后台接入约 1000 个视频 |
| **格式转换** | 一份内容要支持 2000+ 设备，每部电影要准备多种格式（mp4、3gp 等） |
| **分辨率适配** | 每种视频生成 4K/1080p/720p/480p/240p 多档副本 |
| **分发** | 多副本预分发至全球各 Open Connect Appliance (OCA) |
| **Pipeline** | 切片 → AWS 并行 worker 转码 → 多格式输出 → 边缘预分发 |

#### 2.1.2 存储分层

| 存储 | 选型 | 用途 |
|------|------|------|
| 关系型 | **MySQL**（主-主 + 读副本） | 账单/用户/交易（ACID 强一致） |
| NoSQL | **Cassandra**（50+ 集群、500+ 节点、日备 30 TB、单节点 25 万写/秒） | 观看历史、用户活动、跨区复制 |
| 对象存储 | **AWS S3** | Chukwa 事件流目标 |
| 边缘存储 | **OCA**（Open Connect Appliance） | 全球分布式视频缓存 |
| 数据仓库 | **Hadoop** | 离线大数据分析 |

#### 2.1.3 数据管道与 API

```
用户活动 → Kafka → 推荐引擎 + 大数据
日志    → Chukwa → Hadoop
客户端  → AWS ELB（2 层）→ ZUUL API 网关 → 微服务集群
```

关键组件：**Kafka**（事件总线）、**Chukwa**（日志收集）、**Hystrix**（服务隔离）、**ELB + ZUUL**（流量调度与 DDoS 防护）。

#### 2.1.4 Netflix 给我们的启示

1. **Pipeline 必须并行**——单部视频多种格式 × 多档码率，靠并行 worker 而不是串行脚本。
2. **存储按一致性分库**——强一致走 MySQL，最终一致走 Cassandra，原始素材进 S3，热点进 OCA。
3. **媒资 ID 是核心抽象**——所有 Kafka 事件、Chukwa 日志、微服务调用都围绕 `content_id` 串起来。
4. **API 网关 + 微服务是协作底盘**——媒资管理是几十个微服务协同的产物，不是单体系统。

### 2.2 腾讯视频 TVKPlayer —— 国产化 Multi-DRM 的典型实现

> 来源：腾讯视频 TVKPlayer 项目知识库（`source/api/asset/vod/`、`source/feature/vod/`、`source/capability/`）

#### 2.2.1 加密类型枚举

```
kNone=0  kCommon=1(普通DRM)  kTaiHe=2(数字太和)  kChacha20=3
kFairPlay=4(iOS)  kWidevine=5(Android)  kChinaDrm=6  kChinaDrm2_0=7
kPlayReady=8(Win)  kMultiDrm=9  kTXVDrm=10(腾讯视频自研DRM)
```

**关键发现**：加密类型与 DRM 方案解耦——`kMultiDrm` 可以是 Widevine+PlayReady+FairPlay+ChinaDRM 的任意组合，`kTXVDrm` 是腾讯视频自研 DRM。

#### 2.2.2 Feature 接口模式

播放器按场景加载 Feature：

- `TVKVodWidevineDrmFeature`（Android）
- `TVKVodFairplayDrmFeature`（iOS）
- `TVKVodChinaDrm20Feature`（鸿蒙 / 国合规）
- `TVKVodTXVDrmFeature`（腾讯自研）
- `TVKVodSoftWatermarkFeature`（软水印）
- `TVKVodDynamicWatermarkFeature`（动态水印）

**每个 Feature 独立开关、独立升级、独立容错**——这种"Feature 工厂 + 责任链"模式是腾讯视频播放器的核心架构。

#### 2.2.3 设备能力查询（Capability）

```cpp
TVKPlayerCapabilityRecommender::GetDrmCapability()
→ 返回 L1(硬件TEE) / L3(软件) 等级
→ 4K 内容必须 L1，不达标自动降级
```

#### 2.2.4 媒资后台（qqliveasset）

`qqliveasset/` 目录实现腾讯视频资产播放器的完整 SDK：

- **播放器主控制器**（player_mgr/）
- **媒体轨道管理**（track/）
- **播放策略选择**（strategy/）
- **功耗监控**（power/）
- **字幕渲染**（subtitle/）
- **功能扩展框架**（feature/）
- **资产构建与请求管理**（asset/、requester/）

`tvk_qqlive_vod_online_vinfo_data_asset.cpp` 封装"在线视频信息数据资产"——通过 `SetVidForVodOnlineVinfoDataAsset`、`SetCidForVodOnlineVinfoDataAsset` 等方法把视频 ID、内容 ID、请求参数、请求头绑定到统一资产对象，**整个生命周期通过 `Release*` 函数释放防泄漏**。

#### 2.2.5 腾讯视频给我们的启示

1. **加密与封装解耦**——一份 CENC 加密 + Multi-DRM 组合，应对 200+ 终端类型。
2. **Feature 工厂模式**——DRM、水印、反截屏、字幕都是可插拔的 Feature，不是写死的逻辑。
3. **能力查询是前置**——先查 `GetDrmCapability()` 再决定加载哪个 Feature，是"安全降级"的标准做法。
4. **资产即对象**——`VodAsset` 统一封装"在线/离线/伪直播"等多形态资源，对外是统一接口。

### 2.3 腾讯云数据万象 CI —— 后端介质处理的标准范式

> 来源：腾讯云对象存储 COS 媒体处理文档（数据万象）

#### 2.3.1 标准流程

```
上传 COS → 触发转码(转码过程中加密) → 从密钥管理模块取密钥
→ 加密后的 .m3u8/.ts 写回 COS → CDN 分发
→ 播放器带签名 token(JWT) 请求解密密钥 → 解密播放
```

#### 2.3.2 能力矩阵

| 能力 | 说明 |
|------|------|
| 音视频转码 | 基础音视频处理 |
| 极速高清 | 画质修复 + V265 编码 |
| 广电专业格式 | XAVC、ProRes |
| 精彩集锦 | 多维度识别 + 聚合剪辑 |
| 视频增强 | SDR→HDR、细节增强 |
| 超分辨率 | 老片源高清重建 |
| **视频加密** | **HLS 标准加密** |
| 视频标签 | 多模态融合识别 |
| 人声分离 | 人声 / 背景声分离 |
| HLS 自适应打包 | 一源多码率 |
| 视频截帧 | 时间点 / 间隔 / 数量可配 |
| 音视频拼接 | 开/结尾拼接 |
| 音视频分段 | 切片成多段 |
| 智能封面 | 内容理解 + 帧质量评估 |
| 视频元信息 | 编码/像素/码率/帧率/宽高 |

#### 2.3.3 AI 已经内联进流水线

数据万象转码侧**已经带**"视频标签、智能封面、精彩集锦、质量评分、人声分离"等 AI 能力——这正是 `my-webui` 系统的对标物和归宿。

#### 2.3.4 数据万象给我们的启示

1. **加密与转码一体化**——HLS 标准加密 / DASH 加密在转码模板里勾选开启，自适应码率（一源生成多码率）同时打包加密。
2. **密钥与内容分离**——内容存 COS，密钥存独立密钥管理模块；播放时用 JWT（`HMACSHA256(header.payload, key)`）签发临时令牌换密钥，实现"按次/按人/按时效"授权。
3. **任务/工作流双模式**——存量数据走"任务"（一次性批处理），增量数据走"工作流"（绑定路径、上传即触发）。
4. **AI 是流水线的内建工序**——智能封面、精彩集锦、视频标签不是独立系统，而是转码模板里的勾选项。

### 2.4 AWS 媒体资产管理（MAM）—— 行业标准定义

> 来源：[AWS《什么是媒体资产管理？》](https://aws.amazon.com/cn/what-is/media-asset-management/)

#### 2.4.1 MAM 核心定义

**媒体资产管理（Media Asset Management, MAM）** 是指组织图像、音频和视频等媒体文件以实现**高效发现、检索和使用**的过程。

- 媒体文件因数据固有特性，**难以分类和排序**
- **编目良好的资产**有利于重用、搜索和经济高效的分发
- MAM 系统**集中存储富媒体内容**，提供用于编辑、处理、标记和分发的工具
- 它在资产存储层之上构建一个**软件层**，将基础图像资产**映射到有意义的名称和标签**

#### 2.4.2 MAM 关键能力

| 能力 | 说明 |
|------|------|
| **组织** | 文件可驻留在任何存储位置（本地或云端），MAM 统一访问入口；支持基于 API 和 UI 的交互；可设置用户/群组权限 |
| **轻松搜索** | 类似 CRM 搜索客户，可按上传日期、标题、主题、相关人员、部门等元数据标签检索 |
| **对数字文件进行分析** | 根据元数据分割富媒体资产，进行跨数据库分析 |
| **附加元数据** | 可添加自定义元数据（标题、拍摄日期、演员、导演、剧集、帧速率、音频通道等） |
| **自动文件提取** | 自动扫描已知存储位置中预定格式的文件；可自动检测未列出的元数据 |
| **集成能力** | 与 Amazon S3、Amazon Rekognition 等深度集成 |
| **播放、查看与编辑** | 提供视频/音频/图像的播放查看，部分支持应用内深度编辑 |

#### 2.4.3 MAM vs DAM vs VAM

| 类型 | 全称 | 范围 | 特点 |
|------|------|------|------|
| **VAM** | Video Asset Management | 视频 | 专门管理视频，与视频制作深度集成，支持高级编辑 |
| **MAM** | Media Asset Management | 多媒体 | 管理视频/多媒体及其他文件类型；比 DAM 更先进，支持深度集成与工作流管理 |
| **DAM** | Digital Asset Management | 数字资产 | 传统方案，管理组织品牌资产广度；可能限制文件大小；术语常与 MAM 互换使用 |

#### 2.4.4 MAM 给我们的启示

1. **MAM 是一个软件层**——它不替代存储，而是在 S3/HDFS 之上做"名称-标签-工作流"的统一抽象。
2. **自动文件提取 + 自动元数据**——MAM 必须能扫到指定目录的 `.mp4`/`.wav` 等格式并自动提取元数据。
3. **工作流是 MAM 最大的优势**——自动备份、年龄分级、水印预分发都可以用工作流编排实现。
4. **VAM/DAM/MAM 是不同抽象层级**——`my-webui` 系统当前处于"裸文件系统"层级，连 DAM 都不是。

### 2.5 C2PA / Content Credentials —— 介质真实性国际标准

> 来源：[C2PA 官方网站](https://c2pa.org/)、[Content Authenticity Initiative](https://contentauthenticity.org/how-it-works)

C2PA（Coalition for Content Provenance and Authenticity）是 Adobe、Microsoft、BBC、Intel 等联合发起的**开放技术标准**，给数字内容附加**加密签名的"内容凭证"**，记录来源与编辑链。

- **核心机制**：用 X.509 证书对内容做加密签名，签名包含 **断言（assertions）**、**操作历史（actions）**、**成分清单（ingredient）**。
- **应用场景**：新闻溯源、AIGC 标识、深度伪造检测、司法取证。
- **2026 趋势**：欧盟 AI Act、美国 NO FAKES Act 都把"内容凭证"作为合规要求。

**对 `my-webui` 的启示**：分析报告 `.analysis.md` 出炉时，**附 C2PA 凭证**，写明"本报告由 VLM 在 `xxx` 时间对 `xxx` 视频生成"——这是**内容真实性**的关键一环。

---

## 三、介质管理总体架构（三域 + 一切）

### 3.1 三域切分

```
┌────────────────────────────────────────────────────────────────┐
│                       生产域 (Production)                        │
│  采集上传 → 媒资入库 → 编目 → 母版存储（KMS 加密）                │
└──────────────────────────────┬─────────────────────────────────┘
                               │ 标准媒资事件 (Kafka/Pulsar)
                               ▼
┌────────────────────────────────────────────────────────────────┐
│                       处理域 (Processing)                        │
│  任务队列 → 转码打包 → AI 分析 → 加密打包 → C2PA 签名           │
└──────────────────────────────┬─────────────────────────────────┘
                               │ 加密产物 + JWT 令牌
                               ▼
┌────────────────────────────────────────────────────────────────┐
│                       消费域 (Consumption)                       │
│  分发(CDN/OCA) → 鉴权 → 播放器(DRM 解密) → 鉴权日志             │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│              正交：内容安全 (Content Safety)                     │
│  鉴黄/暴恐/敏感识别、Deepfake 检测、AIGC 溯源(C2PA)              │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 与传统流媒体的差异（`my-webui` 视角）

| 维度 | 传统流媒体（Netflix/腾讯视频） | `my-webui` 视频大模型系统 |
|------|-------------------------------|--------------------------|
| **生产域** | 摄制棚 → 母版 → DRM 加密 | 任意来源本地视频 → OpenCV 抽帧 |
| **处理域** | 转码 → 加密打包 → CDN 预分发 | VLM 抽帧 → OCR/Whisper → 汇总 |
| **消费域** | 播放器 + DRM + 水印 | 报告 `.md` + 截图 + Notebook |
| **正交：内容安全** | 审核 → AI 鉴黄暴恐 → 黑白名单 | VLM 报告 → 用户审阅 |

**核心差异**：`my-webui` 不面向 C 端观众做"防盗版"，而是面向 B 端研究/合规做"AI 分析溯源"。所以**本方案的侧重点是"处理域 + 内容安全"**，而**消费域**对应"研究报告交付"。

### 3.3 总体架构图

```
                    ┌──────────────────────────────────────┐
                    │     媒资后台 (Media Backend)         │
                    │  ┌────────────────────────────────┐  │
                    │  │ 媒资 ID 注册中心 (Asset ID)     │  │
                    │  │ └─ metadata store (PostgreSQL) │  │
                    │  │ └─ 内容索引 (OpenSearch)        │  │
                    │  │ └─ 媒资事件总线 (Kafka)         │  │
                    │  └────────────────────────────────┘  │
                    └──────────────┬───────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│   采集节点     │         │   处理节点     │         │   消费节点     │
│  (Ingestor)   │         │  (Processor)  │         │ (Consumer)    │
│               │         │               │         │               │
│ · 上传/扫描   │         │ · 任务队列    │         │ · 报告下载    │
│ · ffprobe     │         │ · 抽帧/VLM    │         │ · API 查询    │
│ · 初步编目    │         │ · 转码/加密   │         │ · 全文检索    │
│ · KMS 加密    │         │ · OCR/ASR     │         │ · C2PA 验证   │
│ · 入库事件    │         │ · 汇总/C2PA   │         │ · 鉴权日志    │
└───────┬───────┘         └───────┬───────┘         └───────┬───────┘
        │                         │                         │
        ▼                         ▼                         ▼
   母版存储层                  产物存储层                  分发层
   · 对象存储 (S3/COS)         · 冷/热分层                 · CDN
   · KMS 信封                  · 归档/清理                 · 边缘缓存
   · 多副本                    · 索引/检索                 · 鉴权服务
```

---

## 四、八大子系统规范

### 4.1 采集入库子系统（Ingestor）

#### 4.1.1 输入接口

| 接口 | 协议 | 用途 |
|------|------|------|
| `POST /api/v1/asset/upload` | HTTPS Multipart | 单文件上传 |
| `POST /api/v1/asset/scan` | HTTPS + JSON | 批量扫描本地目录 |
| `POST /api/v1/asset/register-external` | HTTPS + JSON | 注册外部已存在的媒资（如 S3 URI） |

#### 4.1.2 入库流程

```
1. 接收文件 → 计算 SHA-256 → 判定是否已存在（幂等）
2. ffprobe 提取元数据：duration / width / height / codec / bitrate / framerate / audio channels
3. 提取关键帧 → 计算感知哈希 (pHash) → 写入元数据 store
4. 触发版权合规检查（黑名单、地理版权、授权状态）
5. KMS 信封加密母版 → 存对象存储
6. 生成媒资 ID（雪花算法）→ 写入 PostgreSQL
7. 发布 `AssetCreated` 事件到 Kafka
```

#### 4.1.3 元数据 Schema

```json
{
  "asset_id": "144115188077855744",
  "source": {
    "type": "local_file",
    "path": "/data/videos/sample.mp4",
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "size_bytes": 524288000
  },
  "metadata": {
    "duration_sec": 3600,
    "width": 1920,
    "height": 1080,
    "codec": "h264",
    "bitrate_kbps": 5000,
    "framerate": 25.0,
    "audio_channels": 2,
    "audio_codec": "aac"
  },
  "phash": "9a8b7c6d5e4f3a2b",
  "rights": {
    "license": "internal_research",
    "expiry": "2027-12-31T23:59:59Z",
    "geo_restriction": ["CN"],
    "watermark_required": true
  },
  "compliance": {
    "checksum_verified": true,
    "blacklist_checked": true,
    "ai_review_status": "pending"
  },
  "lifecycle": {
    "state": "ingested",
    "created_at": "2026-06-30T10:00:00Z",
    "last_accessed": null,
    "tier": "hot"
  }
}
```

#### 4.1.4 落地到 `my-webui`

- **现状**：`scripts/video_tasks.py` 直接读 `video-tasks.json` 配置文件，无统一元数据。
- **演进**：新增 `ingestor.py`，把 `video-tasks.json` 升级为"媒资登记表"，每个视频生成 `asset_id`，写 `~/.video_mam/assets.json` 索引文件。

### 4.2 媒资编目（MAM）子系统

> **目标**：让 `my-webui` 系统从"文件级"升级到"媒资级"。

#### 4.2.1 媒资模型（Asset Model）

| 字段 | 类型 | 说明 |
|------|------|------|
| `asset_id` | BIGINT | 雪花算法主键 |
| `title` | VARCHAR(255) | 媒资标题（自动从文件名提取或人工标注） |
| `category` | ENUM | 类型：movie / tv_episode / short_video / meeting / education / sports / unknown |
| `tags` | JSONB | 标签树（如 `["动作", "科幻", "2024"]`） |
| `description` | TEXT | 简介（可由 VLM 自动生成） |
| `creator` | VARCHAR(128) | 创作者/上传者 |
| `language` | VARCHAR(16) | 原始语言（ISO 639-1） |
| `created_at` | TIMESTAMP | 入库时间 |
| `updated_at` | TIMESTAMP | 最后修改时间 |

#### 4.2.2 元数据自动提取

- **基础元数据**：`ffprobe` / MediaInfo
- **视觉元数据**：VLM 抽帧汇总（场景、人物、动作）
- **音频元数据**：faster-whisper 转写 + 说话人分离
- **文本元数据**：PaddleOCR 提取硬字幕
- **业务元数据**：AI 标签、关键事件时间线

#### 4.2.3 检索能力

| 维度 | 实现 |
|------|------|
| **关键词检索** | PostgreSQL GIN 索引（标题、标签、描述） |
| **相似媒资检索** | pHash 汉明距离 |
| **语义检索** | 帧描述 + 字幕 embedding → 向量库（FAISS / Milvus） |
| **时序定位** | "查询 `12:30` 是否出现过某人物" → 向量库 + 时间戳过滤 |

#### 4.2.4 落地到 `my-webui`

- **现状**：无媒资模型，文件即一切。
- **演进**：用 SQLite 起一个 `media_assets` 表（短期）/ PostgreSQL（中长期），所有任务以 `asset_id` 为主键。

### 4.3 转码与打包流水线

> **对标**：Netflix 转码 Pipeline、腾讯云数据万象。

#### 4.3.1 流水线设计

```
[母版 .mp4]
   │
   ▼
[切片] → 分段 (segment, 60s 一段) ──── 应对长视频
   │
   ▼
[并行转码]  ─┬─→ 360p / 480p / 720p / 1080p / 4K
             ├─→ H.264 / H.265 / AV1
             └─→ ABR (Adaptive Bitrate) ladder
   │
   ▼
[加密打包]
   ├─→ CENC-CTR (cenc)  ─┐
   ├─→ CENC-CBCS (cbcs) ─┼─→ Widevine / PlayReady / FairPlay
   └─→ AES-128 (cbcs)   ─┘
   │
   ▼
[HLS/DASH 清单] → .m3u8 / .mpd
   │
   ▼
[C2PA 签名]  ← 在 manifest 中嵌入内容凭证
```

#### 4.3.2 关键参数

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| 切片时长 | 6 秒 | HLS 标准 |
| GOP 大小 | 2 秒 | 便于随机 seek |
| 编码 | H.264 main / H.265 main | 兼容性 + 效率 |
| 加密模式 | `cbcs` | CBCS 模式支持 sample-AES 模式 |
| 关键帧 | 全关键帧 | 加密后仍可 seek |

#### 4.3.3 与 `my-webui` 的关系

`my-webui` 系统的"抽帧"本质就是**对转码产物的解码采样**——可以**直接消费别人转好的 HLS 加密流**，而不是自己解码原始 mp4：

```
[加密 HLS 流] → Widevine 授权 → 明文帧 → VLM 分析
                                              ↓
                                    [本地缓存加密报告]
```

这是**未来演进的高级形态**——本系统不需要自己转码，但可以消费流媒体行业的转码产物。

#### 4.3.4 落地到 `my-webui`（短期）

- 在 `run_task` 之前增加一个 `prepare` 步骤：用 `ffmpeg -c copy` 把任意格式转成统一 `.mp4`（仅当格式不匹配时）。
- 抽帧后**删除原始帧文件**（已有），但**加密保留关键帧截图**用于报告。

### 4.4 加密与 DRM 子系统

> **对标**：Netflix CENC、腾讯视频 kMultiDrm+TXVDrm、ChinaDRM 2.0。

#### 4.4.1 三层加密体系

```
┌──────────────────────────────────────────────┐
│ L1 存储加密（KMS 信封）                       │
│   母版/中间产物 → AES-256-GCM / SM4-GCM       │
│   DEK 由 CMK 加密，DEK 明文只在内存            │
│   适用：母版、抽帧截图、分析报告 .md            │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ L2 传输加密（TLS 1.3 + QUIC）                 │
│   HTTPS / MTLS / 证书钉扎                     │
│   适用：API 调用、Kafka 消息、对象存储上传      │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ L3 内容加密（CENC + Multi-DRM）               │
│   cbcs 加密 + CPIX 密钥交换                   │
│   适用：流媒体分发（如果未来要分发）            │
└──────────────────────────────────────────────┘
```

#### 4.4.2 密钥管理

| 组件 | 实现 | 用途 |
|------|------|------|
| **KMS** | HashiCorp Vault / 腾讯云 KMS / AWS KMS | 主密钥 (CMK) 管理 |
| **DEK** | 每次任务随机生成 | 数据加密密钥 |
| **JWT 令牌** | HS256/RS256 | 临时授权（按次/按人/按时效） |
| **密钥轮换** | 90 天自动 | 降低长期泄露风险 |

#### 4.4.3 落地到 `my-webui`（立即可做）

1. **清除硬编码 API Key**（`analyze-remote.sh` 第 25 行已发现）。
2. **加密 `.analysis.md`**：用 AES-256-GCM 加密后再写盘，密钥从环境变量或 Vault 读取。
3. **加密抽帧截图**：用 `age` / `gocryptfs` 在写盘前加密。
4. **JWT 鉴权**：API 加 `Bearer Token`，从 Ollama 配置复用密钥。

```python
# 伪代码
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

def encrypt_file(path: str, key: bytes) -> None:
    data = open(path, 'rb').read()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, data, None)
    open(path + '.enc', 'wb').write(nonce + ct)
    os.remove(path)  # 删除明文
```

### 4.5 分发与播放子系统

> **对标**：Netflix Open Connect、腾讯云 CDN。

#### 4.5.1 分发模型

| 场景 | 分发方式 | 鉴权 |
|------|----------|------|
| **内部分析** | 本地文件系统 / NFS | 文件系统权限 |
| **跨节点共享** | MinIO / S3 兼容对象存储 | Pre-signed URL |
| **报告交付** | 加密 ZIP + JWT 链接 | Bearer Token |
| **流媒体预览** | HLS 加密流 + CDN | Widevine / FairPlay |

#### 4.5.2 Pre-signed URL 模式

```python
# 伪代码
import boto3
from botocore.config import Config

s3 = boto3.client('s3', config=Config(signature_version='s3v4'))
url = s3.generate_presigned_url(
    'get_object',
    Params={'Bucket': 'analysis-reports', 'Key': f'report-{asset_id}.md.enc'},
    ExpiresIn=3600,  # 1 小时有效
    HttpMethod='GET'
)
```

#### 4.5.3 落地到 `my-webui`

- **现状**：产物写本地盘，无分发能力。
- **演进**：提供"导出分析结果"功能，生成 Pre-signed URL（短期）/ 加密 ZIP（长期）。

### 4.6 水印与溯源子系统

> **对标**：Netflix 取证水印、腾讯视频软水印/动态水印。

#### 4.6.1 两类水印

| 类型 | 实现 | 抗性 |
|------|------|------|
| **可见水印** | 帧右上角叠加用户 ID + 时间戳 | 弱（裁剪即去） |
| **不可见水印** | DCT 域 / DWT 域 / DWT-DCT 域 | 强（裁剪、压缩、屏摄仍可检测） |

#### 4.6.2 取证水印方案（视频大模型场景）

- **会话级前向水印（A/B watermarking）**：每个分析任务生成两个版本的报告（A/B），通过比对泄露的版本可溯源到具体任务/用户。
- **响应时延嵌入**：在 `.analysis.md` 的字体/空格中嵌入微调信息（隐写术）。

#### 4.6.3 C2PA 凭证

```
分析报告 .analysis.md
   └─ 附加 C2PA manifest (.c2pa)
       ├─ assert: 由 my-webui VLM 生成
       ├─ created_at: 2026-06-30T10:00:00Z
       ├─ creator: my-webui v1.0
       ├─ ingredient: 引用源视频 SHA-256
       └─ signature: Ed25519 / RSA-PSS
```

#### 4.6.4 落地到 `my-webui`

- **现状**：无水印、无凭证。
- **演进**：
  1. 在 `run_task` 收尾时给 `.analysis.md` 加 C2PA manifest（用 `c2pa-python` 库）。
  2. 抽帧截图叠加 `my-webui/<asset_id>/<timestamp>` 半透明水印。
  3. 长期目标：支持会话级水印（A/B variants）。

### 4.7 内容审核与内容安全子系统

> **核心**：这是视频大模型的**天然主场**。

#### 4.7.1 审核维度

| 维度 | 实现 | 模型 |
|------|------|------|
| **鉴黄** | VLM 帧级分类 | NSFW detection model |
| **暴恐** | VLM 帧级分类 + OCR 文字检测 | violence detection |
| **政治敏感** | 视觉 + 语音 + 文字多模态 | 自研 / 第三方 |
| **隐私** | 人脸检测 + 车牌检测 | RetinaFace / PaddleOCR |
| **Deepfake** | 视频级真伪检测 | MesoNet / XceptionNet |
| **AIGC 溯源** | C2PA 凭证解析 | `c2pa-python` |

#### 4.7.2 内容安全与"处理域"的耦合

```python
# 伪代码
def analyze_with_safety(asset_id):
    # 1. 抽帧
    frames = extract_frames(asset_id)
    
    # 2. 内容审核（前置）
    safety_report = safety_check(frames)
    if safety_report.has_violation:
        return {
            'status': 'blocked',
            'reason': safety_report.violations,
            'c2pa_assertion': 'policy_violation'
        }
    
    # 3. AI 分析（仅对合规内容）
    analysis = vlm_describe(frames)
    
    # 4. 内容审核（后置）
    post_safety = safety_check(analysis)
    
    return {
        'status': 'completed',
        'analysis': analysis,
        'safety_report': post_safety,
        'c2pa_assertion': 'clean'
    }
```

#### 4.7.3 落地到 `my-webui`

- **现状**：无内容审核。
- **演进**：在 `run_task` 中增加 `safety` 步骤，输出合规标签 `safe / sensitive / blocked`。

### 4.8 存储与归档子系统

> **对标**：Netflix 4 层存储、AWS S3 Glacier。

#### 4.8.1 冷热分层

| 分层 | 存储介质 | 访问延迟 | 保留期 | 内容 |
|------|----------|----------|--------|------|
| **Hot** | NVMe SSD | < 10 ms | 30 天 | 活跃分析任务、近期报告 |
| **Warm** | HDD / S3 Standard | < 1 s | 90 天 | 母版、抽帧截图 |
| **Cold** | S3 IA / 归档存储 | < 1 min | 1 年 | 加密分析报告、汇总 |
| **Frozen** | S3 Glacier Deep Archive | < 12 h | 永久 | 合规归档、审计日志 |

#### 4.8.2 生命周期策略

```yaml
# 伪配置
lifecycle_policy:
  - name: analysis_report_lifecycle
    match: { type: analysis_md }
    transitions:
      - { from: hot, to: warm, after_days: 30 }
      - { from: warm, to: cold, after_days: 90 }
      - { from: cold, to: frozen, after_days: 365 }
    actions:
      - on_transition: encrypt_with_kms
      - on_expire: notify_and_retain_legal_hold
```

#### 4.8.3 落地到 `my-webui`

- **现状**：全在本地 `/tmp/...` 临时目录，无分层。
- **演进**：
  1. 增加 `tier` 字段到媒资元数据。
  2. 引入 MinIO（本地 S3 兼容）做分层。
  3. 30 天未访问的产物自动从 hot 迁移到 warm。

---

## 五、与当前 `my-webui` 的差距矩阵

| 子系统 | 主流标准 | 当前 `my-webui` | 差距 | 优先级 |
|--------|----------|-----------------|------|--------|
| **4.1 采集入库** | 媒资 ID + 元数据 store + 事件总线 | `video-tasks.json` 配置文件 | 🔴 大 | P1 |
| **4.2 媒资编目** | MAM 元数据 + 多维检索 | 无 | 🔴 大 | P1 |
| **4.3 转码打包** | CENC 加密 + ABR + 工作流 | OpenCV 抽帧、无加密 | 🟡 中 | P2 |
| **4.4 加密 DRM** | KMS 信封 + CENC + Multi-DRM | 零加密 | 🔴 大 | P0 |
| **4.5 分发播放** | CDN + Pre-signed URL | 写本地 | 🟡 中 | P2 |
| **4.6 水印溯源** | 不可见水印 + C2PA | 无 | 🟡 中 | P2 |
| **4.7 内容安全** | VLM 审核 + Deepfake 检测 | 无 | 🟡 中 | P1 |
| **4.8 存储归档** | 冷热分层 + 生命周期策略 | 全在临时目录 | 🟡 中 | P1 |

> **P0** = 立即修复（已发现的硬编码 API Key、裸奔产物）
> **P1** = 阶段 1（先增效）应实现
> **P2** = 阶段 2-3（再扩能、后检索）应实现

---

## 六、演进路线图（4 个阶段、12 个月）

### 阶段 0 — 止血（已完成，2026-06）

- ✅ 后端 `status` 字段补齐、`_FRAME_FILES` 注解修复
- ✅ `run_task` 重复抽帧 / 变量顺序修复
- ⏳ **新加**：清理 `analyze-remote.sh` 硬编码 API Key；产物加密落盘

### 阶段 1 — 介质基础 + 吞吐（2026-Q3，前 3 个月）

**目标**：从"裸文件处理"升级到"媒资级处理"

1. **媒资 ID 化**：所有任务以 `asset_id` 为主键，引入 `media_assets` 表（SQLite 起步）。
2. **元数据自动提取**：ffprobe → JSON + pHash，存 SQLite。
3. **KMS 信封加密**：母版 / 产物 / 报告统一 AES-256-GCM，密钥从环境变量读取。
4. **持久化任务队列**：`video-tasks.json` 升级为 `tasks.db`（SQLite），状态机 `pending/running/done/failed`，支持 `--resume` / `--retry-failed`。
5. **资源感知分级并发**：解码(CPU/IO) / VLM(GPU) / 汇总(GPU) / 写盘(IO) 四级独立并发。
6. **冷热分层**：抽帧 / 报告用 SSD（hot），母版用 HDD（warm），过期报告迁移到 S3 兼容存储（cold）。

### 阶段 2 — 扩能 + 架构整合（2026-Q4，中 3 个月）

1. **统一两套实现**：把 `scripts/video_tasks.py` 的增强能力抽成 `video_core/` 公共模块，`routers/video_analysis.py` 直接复用。
2. **帧级缓存下沉**：从"整段视频结果缓存"改为"按帧哈希缓存描述"，同模板视频大幅省推理。
3. **结构化 JSON 输出**：报告同步产出时间线/标签/实体/关键帧路径/置信度，便于入库。
4. **内容审核嵌入流水线**：前置鉴黄/暴恐/敏感，后置 Deepfake 检测。
5. **C2PA 凭证**：每个分析报告附加 C2PA manifest，写明"由 VLM 在何时对何视频生成"。

### 阶段 3 — 可消费性 + 检索（2027-Q1，后 3 个月）

1. **轻量时序增强**：汇总 prompt 注入帧时间戳序列 + 镜头边界，让 `qwen2.5` 生成"事件时间线"而非帧堆叠。
2. **向量检索 RAG-over-video**：帧描述 + 字幕做 embedding 存向量库（FAISS / Milvus），实现"一句话在海量视频中定位片段"。
3. **Pre-signed URL 分发**：分析报告支持临时链接下载，1 小时有效。
4. **软水印 + 取证水印**：抽帧截图叠加用户 ID 水印；会话级 A/B 水印可溯源到具体任务。
5. **媒资后台 Web 面板**：`/mam` 路由，全文检索媒资库、查看处理历史、C2PA 验证。

### 阶段 4 — 工业级介质管理（2027-Q2+，未来）

1. **Multi-DRM 兼容消费域**：如果未来要把分析结果以"加密视频"形式分发，可支持 CENC + Widevine/PlayReady/FairPlay。
2. **机密计算（TEE）推理**：在 TEE 环境里对**加密视频**做 VLM 推理（Intel SGX / NVIDIA H100 CC），兼顾"要分析"与"要保密"。
3. **跨域血缘追踪**：从"原始视频"到"分析报告"的全链路血缘，支持合规审计。
4. **同态加密 AI 探索**：在 HE 环境里做隐私计算，处理极度敏感的视频（如医疗影像、未公开财报）。

---

## 七、关键风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| **AI 分析结果含敏感信息泄露** | 高（合规问题） | 产物加密落盘 + KMS 密钥管理 + 访问审计日志 |
| **KMS 不可用导致系统瘫痪** | 高 | 密钥本地缓存（短 TTL） + 故障降级到环境变量 |
| **C2PA 凭证被篡改** | 中 | 用 Ed25519 / RSA-PSS 强签名，签名时间戳接入区块链存证 |
| **任务队列丢失导致重跑** | 中 | SQLite WAL 模式 + 定期 checkpoint + 多副本 |
| **VLM 误判导致审核漏放** | 高 | 多模型投票 + 人工抽检 + 黑白名单 |
| **海量视频存储成本爆炸** | 中 | 冷热分层 + 30 天后压缩归档 + 90 天后清理中间产物 |
| **分析报告被非法传播** | 中 | 软水印 + 不可见水印 + 报告 ID 与分发渠道绑定 |
| **API Key / Token 泄露** | 高 | 强制环境变量 + Vault 集成 + 定期轮换 + 实时吊销 |

---

## 八、附录

### 附录 A：术语表

| 术语 | 全称 | 说明 |
|------|------|------|
| **MAM** | Media Asset Management | 媒资管理 |
| **DAM** | Digital Asset Management | 数字资产管理 |
| **VAM** | Video Asset Management | 视频资产管理 |
| **DRM** | Digital Rights Management | 数字版权管理 |
| **CENC** | Common Encryption | ISO/IEC 23001-7 通用加密 |
| **CPIX** | Content Protection Information Exchange | 密钥交换协议 |
| **ABR** | Adaptive Bitrate | 自适应码率 |
| **ABR ladder** | 自适应码率阶梯 | 360p/480p/720p/1080p/4K |
| **CBOR** | Concise Binary Object Representation | C2PA manifest 编码格式 |
| **C2PA** | Coalition for Content Provenance and Authenticity | 内容来源真实性联盟 |
| **KMS** | Key Management Service | 密钥管理服务 |
| **DEK** | Data Encryption Key | 数据加密密钥 |
| **CMK** | Customer Master Key | 主密钥 |
| **TEE** | Trusted Execution Environment | 可信执行环境 |
| **OCA** | Open Connect Appliance | Netflix 边缘缓存设备 |
| **OCA 编号** | kMultiDrm=9 | 腾讯视频 Multi-DRM 枚举 |
| **TXVDrm** | Tencent Video DRM | 腾讯视频自研 DRM |
| **pHash** | Perceptual Hash | 感知哈希 |

### 附录 B：参考资源

| 资源 | URL | 用途 |
|------|-----|------|
| AWS 《什么是媒体资产管理》 | https://aws.amazon.com/cn/what-is/media-asset-management/ | MAM 标准定义 |
| Netflix Open Connect 白皮书 | https://openconnect.netflix.com/Open-Connect-Overview.pdf | Netflix CDN |
| C2PA 标准 | https://spec.c2pa.org/ | 内容凭证规范 |
| C2PA 组织 | https://c2pa.org/ | 联盟主页 |
| Content Authenticity Initiative | https://contentauthenticity.org/how-it-works | Adobe 主导的真实性项目 |
| 腾讯云对象存储媒体处理 | （腾讯云文档） | 数据万象 CI |
| Netflix 后端架构解析 | https://www.infoq.cn/article/7ozSkjX1sp49J8YBuMeN | Netflix 系统设计 |
| OWASP 媒体安全 Top 10 | https://owasp.org/ | 媒体安全最佳实践 |
| ISO/IEC 23001-7 (CENC) | ISO 标准 | 通用加密规范 |
| ISO/IEC 23009-1 (DASH) | ISO 标准 | DASH 自适应流 |

### 附录 C：当前 `my-webui` 待清理的安全债

来自前序调研（`with4.md`）：

1. ⚠️ `analyze-remote.sh` 第 25 行曾硬编码 API Key（需清理 + 清 git 历史）
2. ⚠️ `.analysis.md` / 抽帧截图全部明文落盘
3. ⚠️ `~/.video_analysis_cache` 明文缓存
4. ⚠️ 任务状态仅在内存，重启即丢

**对应本方案的修复点**：

- 4.4 加密与 DRM 子系统 → 修复 1、2、3
- 阶段 1 任务队列 → 修复 4

### 附录 D：方案文档结构

本方案对应到当前 `my-webui` 文档体系：

```
docs/
└── 202606/
    ├── PROGRESS_HANDOFF.md         # 研发进度交接
    ├── with1.md ~ with4.md         # 历次会话总结
    ├── MEDIA_ASSET_MANAGEMENT_SOLUTION.md   # 本方案
    ├── video_core_architecture.md  # (待写) video_core 公共模块设计
    ├── encryption_guide.md         # (待写) 加密与 KMS 集成指南
    ├── c2pa_integration.md         # (待写) C2PA 凭证集成
    ├── mam_data_model.md           # (待写) 媒资数据模型
    └── tasks_lifecycle.md          # (待写) 任务队列与生命周期
```

---

**方案结束**

> 本方案定义了视频介质从采集到消费的全链路管理规范，对标 Netflix / 腾讯视频 / 腾讯云 / AWS / C2PA 等主流方案，并落到当前 `my-webui` 系统的演进路径。后续将分阶段实施，每个阶段都有可验证的产出和回退机制。
