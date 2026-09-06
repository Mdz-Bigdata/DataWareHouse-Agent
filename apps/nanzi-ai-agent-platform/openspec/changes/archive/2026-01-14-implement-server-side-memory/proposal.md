# 服务端对话记忆 (Server-Side Conversation Memory) 实施方案

## 目标
利用 Redis 实现服务端的对话历史存储与管理，并更新前端（EmbedChat 和 Dashboard）以利用此特性。通过引入 `conversation_id`，使后端能够自动维护上下文，减少客户端的数据传输量，并解决长对话中的状态丢失问题。

## 核心变更
### 后端
1.  **新增服务**: `MemoryService` (位于 `app/services/ai/memory_service.py`)，负责 Redis 中对话历史的读写操作。
2.  **API 更新**: 更新 `ChatCompletionRequest` 模型，增加可选字段 `conversation_id`。
3.  **逻辑更新**: 修改 `AgentService`，当接收到 `conversation_id` 时，自动合并服务端历史与当前消息。

### 前端
1.  **API Client**: 更新前端 API 请求方法，支持传递 `conversation_id`。
2.  **Agent Debug**: 调试页面增加 `Conversation ID` 的状态管理（支持自动生成/重置），并在请求时携带。
3.  **EmbedChat**: 更新嵌入式聊天组件，支持会话持久化。

## 收益
-   **体验提升**: 刷新页面或切换 Tab 后，对话历史不丢失。
-   **性能提升**: 客户端只需发送最新的一条消息，显著减小网络负载。
-   **一致性**: 在服务端统一管理滑动窗口（Sliding Window），确保 Context Window 策略的一致性。

## 风险
-   **复杂性**: 前后端增加了状态同步的逻辑。
-   **并发性**: 极少数并发写入情况下可能存在竞争。
