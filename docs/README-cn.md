# 开放WebUI 👋

![GitHub stars](https://img.shields.io/github/stars/open-webui/open-webui?style=social)
![GitHub forks](https://img.shields.io/github/forks/open-webui/open-webui?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/open-webui/open-webui?style=social)
![GitHub repo size](https://img.shields.io/github/repo-size/open-webui/open-webui)
![GitHub language count](https://img.shields.io/github/languages/count/open-webui/open-webui)
![GitHub top language](https://img.shields.io/github/languages/top/open-webui/open-webui)
![GitHub last commit](https://img.shields.io/github/last-commit/open-webui/open-webui?color=red)
[![Discord](https://img.shields.io/badge/Discord-Open_WebUI-blue?logo=discord&logoColor=white)](https://discord.gg/5rJgQTnV4s)
[![](https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/tjbck)
![Open WebUI Banner](./banner.png)

**开放WebUI 是一个功能丰富且用户友好的全离线AI平台，适用于企业级部署。它支持各种LLM运行器（例如Ollama和兼容OpenAI的API），并且内置推理引擎用于RAG，使其成为强大的AI部署解决方案。**

如果你对开源AI充满热情，请考虑加入我们[团队](https://careers.openwebui.com/)！
![Open WebUI Demo](./demo.png)

> [!TIP]  
**寻找企业级计划？** **[请与我们的销售团队联系](https://docs.openwebui.com/enterprise)。**

获取增强功能，包括自定义主题和品牌、服务级别协议（SLA）支持、长期支持（LTS）版本等！更多信息，请参阅 [开放WebUI文档](https://docs.openwebui.com/)。

## 开放WebUI 的关键特性 ⭐
- 🚀 **轻松安装**：使用Docker或Kubernetes（kubectl，kustomize 或 helm）无缝安装。支持 `:ollama` 和 `:cuda` 标记的镜像。
- 🤝 **Ollama/OpenAI API 集成**：轻松集成兼容OpenAI 的API，支持多样化的对话。自定义 OpenAI API URL ，以链接到 LMStudio, GroqCloud, Mistral, OpenRouter 等服务。
- 🛡️ **细致的权限控制和用户组**：通过允许管理员创建详细的用户角色和权限，确保一个安全的用户环境。这种粒度不仅增强了安全性，还提供了定制化的用户体验，促进用户的归属感和责任感。
- 📱 **响应式设计**：在桌面电脑、笔记本和平板设备上提供流畅体验。
- 📱 **渐进式Web应用 (PWA) 手机版**：通过我们的 PWA 享受类似原生应用程序的移动体验，在本地主机上支持离线访问，并无缝集成用户界面。
- ✒️🔢 **全面 Markdown 和 LaTeX 支持**：利用全面的 Markdown 和 LaTeX 功能，使LLM的交互更丰富。
- 🎤📹 **语音/视频通话免提功能**：使用多种 Speech-to-Text 提供商（本地 Whisper、OpenAI、Deepgram、Azure）和 Text-to-Speech 引擎（Azure、ElevenLabs、OpenAI、Transformers、WebAPI），体验流畅的语音和视频通话，动态且互动式的聊天环境。
- 🛠️ **模型构建器**：通过 Web UI 轻松创建 Ollama 模型。创建并添加自定义角色/代理，定制聊天元素，并通过 [开放 WebUI 社区](https://openwebui.com/) 简易地导入模型。
- 🐍 **本地Python函数调用工具**：在工具工作空间中增强 LLM 的功能。只需加入纯 Python 函数即可实现无缝集成。
- 💾 **持久化工件存储**：内置键值存储 API 用于工件，支持日志、追踪器、排行榜和协作工具的个人和共享数据范围跨会话使用。
- 📚 **本地RAG 集成**：通过选择9种向量数据库和多种内容提取引擎（Tika, Docling, Document Intelligence, Mistral OCR, PaddleOCR-vl，外部加载器）进行检索增强生成 (RAG) 支持。将文档直接载入对话或添加到文件库，在查询之前使用 `#` 命令轻松访问它们。
- 🔍 **Web 搜索用于 RAG**：通过15+提供商（SearXNG, Google PSE, Brave Search, Kagi, Mojeek, Tavily, Perplexity, serpstack, serper, Serply, DuckDuckGo, SearchApi, SerpApi, Bing, Jina, Exa, Sougou, Azure AI Search, Ollama Cloud）执行 Web 搜索，直接将结果注入对话体验。
- 🌐 **网页浏览功能**：使用 `#` 命令后跟 URL 将网站无缝整合到您的聊天体验中。该特性允许您直接在对话中引入网络内容，增强互动的丰富性和深度。
- 🎨 **图像生成与编辑集成**：通过多个引擎（包括 OpenAI 的 DALL-E、Gemini、ComfyUI 和 AUTOMATIC1111）创建和编辑图像，并支持基于提示的工作流。
- ⚙️ **多模型对话**：轻松同时与多种模型交互，充分发挥其独特优势以获得最佳响应。利用多样化的模型群组优化体验。
- 🔐 **角色基础访问控制 (RBAC)**：确保安全的访问权限；只有授权人员才能访问您的Ollama，并且专属的模型创建/拉取权限仅限于管理员。
- 🗄️ **灵活的数据库和存储选项**：选择 SQLite（带可选加密），PostgreSQL，或配置云存储后端（S3、Google Cloud Storage、Azure Blob Storage）用于可扩展部署。
- 🔍 **高级向量数据库支持**：从包括 ChromaDB, PGVector, Qdrant, Milvus, Elasticsearch, OpenSearch, Pinecone, S3Vector 和 Oracle 23ai 的9个选项中选择，以获得最佳的 RAG 性能。
- 🔐 **企业级认证**：完全支持LDAP/Active Directory集成、SCIM 2.0 自动化配置以及通过受信任头信息或OAuth提供程序实现SSO。使用 SCIM 2.0 协议进行企业级用户和组配给，与Okta, Azure AD 和 Google Workspace 等身份提供者无缝整合，以自动化用户的生命周期管理。
- ☁️ **云原生集成**：本地支持Google Drive和OneDrive/SharePoint文件选取，从企业云端存储中轻松导入文档。
- 📊 **生产可观测性**：内置的OpenTelemetry支持跟踪、指标和日志记录，能够提供全面监控，并与现有可观察性堆栈无缝对接。
- ⚖️ **水平扩展性**：通过Redis会话管理和WebSocket支持实现多工作进程及节点部署后的负载均衡器。
- 🌐🌍 **多种语言支持**：利用国际化 (i18n) 支持，您可以以您首选的语言体验Open WebUI。加入我们共同扩展我们所支持的语言！
- 🧩 **流水线和插件支持**：通过 [Pipelines 插件框架](https://github.com/open-webui/pipelines)，轻松将自定义逻辑和 Python 库集成到 Open WebUI 中。启动您的 Pipeline 实例，将 OpenAI URL 设置为 Pipelines URL，并探索无限可能。 [示例](https://github.com/open-webui/pipelines/tree/main/examples) 包括 **函数调用**, 用户 **速率限制** 来控制访问, 使用Langfuse等工具实现的 **使用监控**， **Live Translation with LibreTranslate** 用于多语言支持、 **有害信息过滤器**