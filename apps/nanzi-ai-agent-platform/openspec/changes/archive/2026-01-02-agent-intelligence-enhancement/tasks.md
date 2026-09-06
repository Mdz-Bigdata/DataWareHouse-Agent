# 任务清单

- [x] **Task 1: 后端自愈逻辑增强**
    - [x] 区分业务错误与系统错误。
    - [x] 修改 `_handle_data_query_stream` 循环，支持错误回馈。
    - [x] 增加重试次数限制配置。
- [x] **Task 2: 上下文提取指令集**
    - [x] 更新 `system_prompt`，要求输出格式化的 Context 信息。
    - [x] 在 `agent_service` 中解析 LLM 输出并推送 `type: "context"`。
- [x] **Task 3: 前端 Context 侧边栏开发**
    - [x] 创建 `AgentContextStack` 组件。
    - [x] 在 `AgentDebug.vue` 中集成该组件。
    - [x] 实时监听 SSE 中的 context 事件并更新视图。
- [x] **Task 4: 测试与验证**
    - [x] 模拟错误的 SQL 提问（例如故意拼错表名），观察 Agent 是否能自愈。
    - [x] 验证多轮对话中 Context 信息的累积与展示。
