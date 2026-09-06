## ADDED Requirements

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
