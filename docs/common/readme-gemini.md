这份是关于 **Open WebUI**（一个非常流行、功能强大的开源 AI 界面平台）的官方 README 文档翻译，紧接着我为你整理了一份系统化的学习与上手指南。

---

# Open WebUI 官方文档翻译 📜

**Open WebUI 是一个可扩展、功能丰富且用户友好的自托管 AI 平台，设计为完全离线运行。** 它支持各种大语言模型（LLM）运行器（如 **Ollama**）以及 **兼容 OpenAI 的 API**，并内置了用于 RAG（检索增强生成）的推理引擎，使其成为一个功能强大的 AI 部署解决方案。

对开源 AI 充满热情？[加入我们的团队 →](https://careers.openwebui.com/)

> [!TIP]
> **正在寻找 [企业方案](https://docs.openwebui.com/enterprise)？** – **[立即联系我们的销售团队！](https://docs.openwebui.com/enterprise)**
> 获得**增强功能**，包括**自定义主题与品牌**、**服务等级协议 (SLA) 支持**、**长期支持 (LTS) 版本**等！

欲了解更多信息，请务必查看我们的 [Open WebUI 官方文档](https://docs.openwebui.com/)。

## 核心特性 ⭐

* 🚀 **轻松安装**：支持使用 Docker 或 Kubernetes（kubectl、kustomize 或 helm）无缝安装，并提供 `:ollama` 和 `:cuda` 标签镜像，享受省心的部署体验。
* 🤝 **Ollama/OpenAI API 集成**：轻松集成兼容 OpenAI 的 API，与 Ollama 模型并肩进行多元化对话。自定义 OpenAI API URL 可连接 **LMStudio、GroqCloud、Mistral、OpenRouter 等**。
* 🛡️ **细粒度权限和用户组**：允许管理员创建详细的用户角色和权限，确保安全的运行环境。
* 📱 **响应式设计**：在台式机、笔记本电脑和移动设备上享受无缝的交互体验。
* 📱 **移动端渐进式网络应用 (PWA)**：通过 PWA 在移动设备上享受类似原生应用的体验，提供 localhost 上的离线访问和无缝用户界面。
* ✒️🔢 **完整支持 Markdown 和 LaTeX**：具备全面的 Markdown 和 LaTeX 渲染能力，极大提升 LLM 的交互体验。
* 🎤📹 **免提语音/视频通话**：集成免提语音和视频通话功能。支持多种语音转文字（Local Whisper、OpenAI、Deepgram、Azure）和文字转语音引擎（Azure、ElevenLabs、OpenAI、Transformers、WebAPI）。
* 🛠️ **模型构建器 (Model Builder)**：通过 Web UI 轻松创建 Ollama 模型。创建和添加自定义角色/智能体、自定义聊天元素，并无缝导入 [Open WebUI Community](https://openwebui.com/) 社区资源。
* 🐍 **原生 Python 函数调用工具**：在工具工作区中内置代码编辑器支持。支持“自带函数 (BYOF)”，只需添加纯 Python 函数，即可实现与 LLM 的无缝函数调用。
* 💾 **持久化 Artifact 存储**：内置用于 Artifact 的键值存储 API，支持日志、跟踪器、排行榜和协作工具等功能，涵盖跨会话的个人和共享数据范围。
* 📚 **本地 RAG 集成**：支持突破性的检索增强生成 (RAG)。支持 9 种向量数据库和多种内容提取引擎（Tika、Docling、Document Intelligence、Mistral OCR、PaddleOCR-vl、外部加载器）。直接在聊天中加载文档或加入文档库，在输入查询前使用 `#` 命令即可轻松调用。
* 🔍 **用于 RAG 的网页搜索**：支持使用 15+ 种搜索引擎提供商（包括 `SearXNG`、`Google PSE`、`Brave Search`、`Tavily`、`Perplexity`、`Bing`、`DuckDuckGo` 等）进行网页搜索，将结果直接注入聊天。
* 🌐 **网页浏览功能**：使用 `#` 关键字加 URL 即可将网站无缝整合到聊天中，直接整合网络内容。
* 🎨 **图像生成与编辑集成**：支持 OpenAI 的 DALL-E、Gemini、ComfyUI（本地）和 AUTOMATIC1111（Stable Diffusion 本地），支持图像生成和基于提示词的编辑流。
* ⚙️ **多模型同时对话**：轻松同时与多个模型互动，利用各自的独特优势获得最佳回答。
* 🔐 **基于角色的访问控制 (RBAC)**：严格限制权限，只有获授权的人员才能访问 Ollama，独家模型创建/拉取权限仅留给管理员。
* 🗄️ **灵活的数据库与存储选项**：支持 SQLite（带可选加密）、PostgreSQL，或配置云存储后端（S3、Google Cloud Storage、Azure Blob Storage）进行大规模部署。
* 🔍 **先进向量数据库支持**：可选 9 种向量数据库（ChromaDB、PGVector、Qdrant、Milvus、Elasticsearch 等）以获得最佳 RAG 性能。
* 🔐 **企业级认证**：完整支持 LDAP/Active Directory 集成、SCIM 2.0 自动配置以及基于受信任 Header 或 OAuth 的单点登录 (SSO)。
* ☁️ **云原生集成**：原生支持 Google Drive 和 OneDrive/SharePoint 文件选择器，无缝导入企业云存储文档。
* 📊 **生产级可观测性**：内置 OpenTelemetry 支持（追踪、指标、日志），轻松使用现有的可观测性技术栈进行监控。
* ⚖️ **水平扩展性**：支持基于 Redis 的会话管理和 WebSocket，用于负载均衡器后面的多工人、多节点部署。
* 🌐🌍 **多语言支持**：通过国际化（i18n）支持，在您喜欢的语言中使用 Open WebUI。欢迎加入贡献语言翻译！
* 🧩 **Pipelines (插件支持)**：使用 [Pipelines 插件框架](https://github.com/open-webui/pipelines) 将自定义逻辑和 Python 库无缝集成到 Open WebUI 中。示例包括**函数调用**、用户**速率限制**、使用 Langfuse 进行**用量监控**、**实时翻译**、**恶意消息过滤**等。

---

## 如何安装 🚀

### 方式 A：通过 Python pip 安装 🐍

确保您使用的是 **Python 3.11** 运行环境。

1. **安装 Open WebUI**：
```bash
pip install open-webui

```



```
2. **运行 Open WebUI**：
   ```bash
   open-webui serve

```

启动后，可在浏览器访问：[http://localhost:8080](http://localhost:8080)

### 方式 B：通过 Docker 快速启动 🐳

> [!WARNING]
> 使用 Docker 安装时，请确保包含 `-v open-webui:/app/backend/data` 挂载参数，以防数据丢失。

#### 1. 如果 Ollama 在你本地电脑上：

```bash
docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main

```

#### 2. 如果 Ollama 在其他独立服务器上：

修改 `OLLAMA_BASE_URL` 为你目标服务器的 URL：

```bash
docker run -d -p 3000:8080 -e OLLAMA_BASE_URL=https://example.com -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main

```

#### 3. 运行并开启 Nvidia GPU 硬件加速：

```bash
docker run -d -p 3000:8080 --gpus all --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:cuda

```

### 仅使用 OpenAI API 的安装命令

```bash
docker run -d -p 3000:8080 -e OPENAI_API_KEY=your_secret_key -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main

```

### 安装捆绑了 Ollama 的一体化镜像

此方法使用单个容器镜像将 Open WebUI 与 Ollama 打包在一起：

* **CPU 模式**：
```bash
docker run -d -p 3000:8080 -v ollama:/root/.ollama -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:ollama

```



```

安装完成后，可在浏览器访问：[http://localhost:3000](http://localhost:3000)。

---

# 如何高效学习和使用 Open WebUI？ 🎯

Open WebUI 已经超越了普通的“Web 界面”，它是一个全功能的私有 AI 工作站。想要彻底玩转它，建议遵循以下**进阶路线图**：

## 第一阶段：跑通基础环境（第 1 天）
1. **基础选型**：推荐首选 **Docker 安装法**。如果本地配置了显卡且想完全离线玩，安装 **Ollama**，然后下载一个轻量模型（如 `llama3` 或 `qwen2.5`）。
2. **连接 API**：进入 Open WebUI 的管理员设置（Admin Settings），尝试绑定你的外部 API（如 DeepSeek、OpenAI 或智谱 API）。
3. **熟悉基础界面**：掌握多模型对比聊天（在聊天框顶部同时选择两个模型，它们会同时回答，非常适合评测模型优劣）。

## 第二阶段：解锁高级交互（第 2-3 天）
1. **玩转 `#` 号命令（RAG 基础）**：
   * 在知识库/文档管理（Workspace -> Knowledge）里上传几篇 PDF 或本地文档。
   * 在聊天框里输入 `#`，你会看到弹出了你的文档。选中它，大模型就能基于这篇文档和你对话了。
2. **玩转网页互动**：
   * 在对话框直接输入 `#[https://news.ycombinator.com](https://news.ycombinator.com)` 这样的网址，Open WebUI 会自动抓取网页内容给大模型分析。
3. **使用系统提示词和 Modelfile**：
   * 学习在后台自己组装一个“角色”。例如：设定一个“翻译专家”或“代码评审员”，省去每次聊天都要输入长篇前置提示词的麻烦。

## 第三阶段：打造真正的私人 AI 助理（第 4-7 天）
1. **配置联网搜索（Web Search）**：
   * 强烈建议去了解并部署一个免费的 `SearXNG`（或者直接去申请一个免签的网页搜索 API 密钥，如 Tavily、Google PSE）。
   * 配置好后，你的本地 Ollama 模型就拥有了实时联网搜索的能力！
2. **体验 Python 原生工具（Function Calling）**：
   * 在 Workspace 里的 **Tools** 页面，你可以直接写一小段 Python 代码（比如：获取当前天气、计算复杂的数学公式）。
   * 把这个工具授权给模型后，大模型在需要的时候会自动运行这段代码并返回正确答案。

## 第四阶段：探索高阶生态（进阶）
1. **逛一逛官方社区**：访问 [Open WebUI Community](https://openwebui.com/)。这里有全球网友分享的自定义角色（Models）、高级工具（Tools）和复杂插件（Functions），看到喜欢的可以直接一键导入到你的系统里。
2. **研究 Pipelines 框架**：
   * 如果你有编程基础，想在企业里落地，可以去研究 Open WebUI 的 `Pipelines`。它能帮你做合规过滤（防止员工输入敏感词）、调用企业内部微服务、做多模型路由管理。

建议你先用 Docker 把项目跑起来，进到后台随便点点，遇到具体配置问题（如“联网搜索怎么接”、“怎么挂载本地向量库”）随时来问我！

```