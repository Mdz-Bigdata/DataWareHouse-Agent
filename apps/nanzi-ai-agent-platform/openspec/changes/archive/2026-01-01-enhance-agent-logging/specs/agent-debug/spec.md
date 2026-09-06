# agent-debug Specification Delta

## MODIFIED Requirements

### Requirement: Process Visualization (Chain of Thought)
系统 **MUST** 展示智能体的思考和执行过程（即 CoT 或 Tool Calls），并提供完整的日志查阅能力。
- **Visibility**: 默认显示主要步骤（如“正在查询数据库...”、“正在分析数据...”）。
- **Details**: 支持展开/折叠查看详细的输入/输出（如 SQL 语句、API 返回的 JSON）。
- **Traceability**: 在对话界面提供“查看日志”入口，点击可查看该次对话的完整执行链路，包括：
    - 调用的工具名称 (Tool Name)
    - 传递的参数 (Arguments)
    - 工具返回的原始响应 (Raw Response)
    - 执行耗时与状态

#### Scenario: View Detailed Logs
- Given Agent has completed a response (or is streaming)
- When User clicks the "View Logs" button on the message bubble
- Then A log viewer panel/modal appears
- And The panel shows a chronological list of all execution steps
- And User can inspect the raw JSON of parameters and results
