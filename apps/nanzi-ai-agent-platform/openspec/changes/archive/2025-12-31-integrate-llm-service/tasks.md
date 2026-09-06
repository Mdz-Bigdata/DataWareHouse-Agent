# 任务：LangChain 服务集成实施

- [x] **环境与依赖准备**
  - [x] 安装 `langchain`, `langchain-openai`, `langchain-community`。
  - [x] 配置 `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME` 环境变量。
- [x] **LangChain 基础封装**
  - [x] 在 `app/core/llm/` 实现统一的工厂类，返回初始化好的 `ChatOpenAI` 对象。
- [x] **意图识别链 (Intent Recognition Chain)**
  - [x] 定义意图识别所需的 Pydantic Schema。
  - [x] 使用 LangChain LCEL 编写 `IntentChain`。
  - [x] 实现针对 `DATA_QUERY`, `KNOWLEDGE_BASE`, `GENERAL` 的分类 Prompt。
- [x] **业务集成与 API**
  - [x] 实现 `POST /api/portal/chat/intent` 接口调用该链。
  - [x] 集成 LangChain 的内置回调 (Callbacks) 用于详细的 Token 和链路日志。
- [x] **验证与优化**
  - [x] 编写基准测试，验证识别准确度。
  - [x] 确保 LangChain 异步调用不阻塞主循环。
