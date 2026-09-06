1.  **创建 MemoryService (Backend)** <!-- id: 0 -->
    -   文件: `app/services/ai/memory_service.py`
    -   实现 `MemoryService` 类 (Redis 操作)。
    -   验证: 单元测试 `tests/ai/core/test_memory_service.py`。

2.  **更新 API & Service (Backend)** <!-- id: 1 -->
    -   文件: `app/api/v1/endpoints/chat.py`, `app/services/ai/agent_service.py`
    -   Schema 增加 `conversation_id`。
    -   `AgentService` 集成记忆读写逻辑。

3.  **更新前端 API Client (Frontend)** <!-- id: 2 -->
    -   文件: `frontend/src/api/chat.ts` (或其他 API 定义文件)
    -   更新 `sendChatMsg` 或类似函数，支持传入 `conversation_id`。

4.  **适配 AgentDebug 页面 (Frontend)** <!-- id: 3 -->
    -   文件: `frontend/src/views/AgentDebug.vue`
    -   增加 `conversationId` 状态 (ref)。
    -   初始化时生成 UUID。
    -   增加“清除上下文/新会话”按钮逻辑 (重置 UUID)。
    -   调用 API 时传递 ID。

5.  **适配 EmbedChat (Frontend)** <!-- id: 4 -->
    -   文件: `docs/embed_demo.html` (及相关 JS 逻辑)
    -   在 JS 初始化逻辑中增加 `localStorage` 管理 Session ID。
    -   AJAX 请求中附带 `conversation_id`。

6.  **端到端测试 (Verification)** <!-- id: 5 -->
    -   启动前后端。
    -   在 Debug 页面发消息，刷新页面，确认后端日志显示使用了 Redis 历史，且新消息能接上上下文。
