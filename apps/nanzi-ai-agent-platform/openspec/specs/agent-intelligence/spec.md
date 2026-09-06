# agent-intelligence Specification

## Purpose
TBD - created by archiving change agent-intelligence-enhancement. Update Purpose after archive.
## Requirements
### Requirement: Agent Self-Correction
系统 MUST 实现工具调用错误的自愈机制，允许 Agent 在报错后修正并重试。

#### Scenario: SQL Syntax Error Recovery
- **WHEN** Agent 提交的 SQL 语句语法错误
- **THEN** 系统将错误信息返回给 Agent，允许其修正 SQL 并再次执行，直到成功或达到最大重试次数。

### Requirement: Real-time Context Extraction
系统 MUST 在对话过程中实时提取关键业务实体并推送至前端。

#### Scenario: Update Context Panel
- **WHEN** Agent 识别到用户询问涉及特定的机房（如“华东一号”）
- **THEN** 系统通过 SSE 推送 `type: "context"` 事件，前端 MUST 实时更新上下文面板以显示“机房: 华东一号”。

### Requirement: 智能体工具调用 (Agent Tool Execution)
智能体及底层工具 **MUST** 具备根据元数据中的 `data_source` 字段自动选择 SQL 方言并路由查询的能力。

#### Scenario: 动态数据源路由
- **WHEN** LLM 决定调用 `execute_sql_query`
- **THEN** LLM 必须从上下文（YAML）中提取 `data_source` 值，并作为参数传递给工具。
- **AND** 工具接收该参数，并将其传递给底层的 SQL 执行 API。

#### Scenario: 动态方言校验
- **WHEN** 工具收到 `data_source="mysql_oa"` 的请求
- **THEN** 工具内部使用 MySQL 语法规则对 SQL 进行安全校验（而非默认的 ClickHouse 规则）。
- **AND** 只有校验通过后才执行查询。

#### Scenario: 提示词升级
- **WHEN** 部署新版本
- **THEN** ChatBI 的 System Prompt (V5) 必须包含关于数据源识别和参数传递的明确指令。

