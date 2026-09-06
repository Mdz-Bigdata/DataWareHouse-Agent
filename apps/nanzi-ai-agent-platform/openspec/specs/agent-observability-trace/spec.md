# agent-observability-trace Specification

## Purpose
TBD - created by archiving change federated-query-trace-canvas. Update Purpose after archive.
## Requirements
### Requirement: 链路追踪 Trace 数据库模型扩展
系统 MUST 支持对 `ai_agent_execution_traces` 物理表结构进行升级，增加 `span_id` (VARCHAR(64))、`parent_span_id` (VARCHAR(64)) 以及 `meta_info` (JSON) 字段，用以映射父子嵌套 Span 链路。

#### Scenario: 物理表模型正常初始化与数据库迁移
- **WHEN** 运行数据库迁移脚本 `apply-sql.sh` 或 Python 工具
- **THEN** 物理表 `ai_agent_execution_traces` 中新增 span_id、parent_span_id 和 meta_info 字段且成功索引

### Requirement: 跨协程安全的 TraceSpan 上下文收集
系统 MUST 实现一个基于 `ContextVar` 的 `TraceSpan` 收集上下文管理器，在核心 Runner（如 DataAgentRunner）以及工具调用（如 get_dataset_schema）的生命周期内自动拦截记录耗时并追踪 parent 关系。

#### Scenario: Agent 执行过程中的嵌套 Trace 记录
- **WHEN** ChatBI Agent 运行自愈 SQL ReAct 循环，进入 execution_sql_query 工具调用
- **THEN** System 自动识别 `execute_sql_query` 的父 span 为 `SQL_Self_Healing_Loop`，并在退出时自动写入对应的 execution_time_ms

### Requirement: 树形 Trace API 端点
系统 MUST 提供只读接口 `GET /api/portal/traces/{trace_id}/spans`，按用户会话的 `trace_id` 自动从数据库拉取扁平 span 数据，并递归组装为具有 `children` 层级嵌套关系的树状 JSON 结构返回。

#### Scenario: 按 trace_id 检索树形链路数据
- **WHEN** 客户端以合法身份请求接口 `/api/portal/traces/trace-12345/spans`
- **THEN** 接口返回 HTTP 200，并以 JSON 格式呈现树状层级关系，明确显示各个子 Span 的具体耗时（ms）

### Requirement: 前端链路 Trace 按钮展现与可见性
系统 MUST 允许所有人查看链路 Trace。MUST 在 AI 消息底部工具栏的“重新生成”按钮之后放置一个“Trace”按钮，并在移动端设备（窄屏）上通过响应式 CSS 样式自动隐藏。

#### Scenario: 桌面端消息底部 Trace 按钮展示
- **WHEN** 桌面端用户接收到 AI 消息且消息已完成流式输出
- **THEN** 消息下方的重新生成按钮后面紧跟一个“Trace”或“链路”按钮

#### Scenario: 移动端隐藏 Trace 按钮
- **WHEN** 移动端（窄屏）用户查看 AI 消息
- **THEN** 重新生成按钮后不会渲染 Trace 按钮，规避窄屏排版挤占问题

### Requirement: 链路甘特图弹窗查看
系统 MUST 在用户点击消息下方的 Trace 按钮时，弹窗或拉出抽屉呈现该次对话的树形时延甘特图。

#### Scenario: 点击按钮唤醒甘特图
- **WHEN** 用户点击 AI 消息下方的 Trace 按钮
- **THEN** 页面弹出 TraceLogViewer 或同等浮层，直观展示树状 Span 甘特时延和调用元数据

