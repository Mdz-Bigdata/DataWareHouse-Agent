# Tasks: 增强智能体日志 (Enhance Agent Logging)

## 后端开发 (Backend)
- [x] **设计日志结构**: 定义 `AgentExecutionStep` Pydantic 模型（包含 step_id, tool, args, result, duration 等）及数据库表结构。 <!-- id: be-log-model -->
- [x] **创建数据表**: 编写 SQL 迁移脚本，创建 `ai_agent_execution_traces` 表。 <!-- id: be-db-migration -->
- [x] **改造 AgentService**: 在 `_handle_data_query_stream` 等核心逻辑中，增加日志收集逻辑，将每一步的 Input/Output 存入内存 buffer。 <!-- id: be-agent-collect -->
- [x] **持久化存储**: 在对话结束时，将 buffer 中的 Trace 数据批量写入数据库，并关联 `trace_id`。 <!-- id: be-db-persist -->
- [x] **API 扩展**: 新增 `GET /api/v1/chat/logs/{trace_id}` 接口，允许前端根据 Trace ID 获取完整的执行链路。 <!-- id: be-log-api -->

## 前端开发 (Frontend)
- [x] **UI 入口**: 在 `ChatBubble` 组件中增加“查看详情”图标按钮（仅对 Assistant 消息且有 trace_id 时可见）。 <!-- id: fe-ui-button -->
- [x] **日志面板**: 开发 `TraceLogViewer` 组件（侧边抽屉），使用 Timeline（时间轴）展示执行步骤。 <!-- id: fe-log-viewer -->
- [x] **JSON 展示**: 集成 JSON 格式化组件，美化展示工具参数和返回值。 <!-- id: fe-json-view -->
- [x] **联调验证**: 验证对话结束后点击按钮能正确加载并显示日志。 <!-- id: fe-verify -->
