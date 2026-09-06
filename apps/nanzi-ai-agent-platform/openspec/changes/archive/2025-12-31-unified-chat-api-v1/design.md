# 设计：统一对话路由与分发架构

## 核心架构：智能体调度层 (Agent Dispatcher)

我们将引入 `AgentService` 作为 V1 接口的逻辑核心。它不直接处理业务，而是扮演“中枢调度员”的角色。

### 核心组件
1. **`AgentService` (Orchestration Layer)**:
   - 接收用户原始输入。
   - 调用 `IntentService` 获取意图标签。
   - 根据标签路由到对应的“意图处理器” (Intent Handler)。
2. **`BaseIntentHandler` (Standard Identity)**:
   - 定义统一的处理接口 `handle(query, context)`。
   - **`GeneralHandler`**: 处理闲聊。
   - **`DataQueryHandler`**: 目前先返回“正在为您准备数据查询能力...”的 Mock 信息。
   - **`KnowledgeHandler`**: 目前先返回“正在检索知识库...”的 Mock 信息。

### API 契约 (V1)
- **Endpoint**: `POST /api/v1/chat/completions`
- **Request Body**:
  ```json
  {
    "messages": [{"role": "user", "content": "..."}],
    "stream": false
  }
  ```
- **Response**: 标准化的 AI 响应结构。

### 路由逻辑流
1. 用户调用 V1 API。
2. `AgentService` 识别意图。
3. 如果意图是 `GENERAL`，直接流式或非流式返回结果。
4. 如果意图是 `DATA_QUERY`，返回一个带有特定标识的回复，提示正在执行数据分析。
