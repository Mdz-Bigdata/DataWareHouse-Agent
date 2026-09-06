# Spec: Agent Debugging UI

## ADDED Requirements

### Requirement: Chat Interface
系统 **MUST** 提供类似对话的交互界面。
- **Inputs**: 支持多行文本输入。
- **Actions**: 发送按钮。
- **Shortcuts**: 支持 `Ctrl + Enter` (Windows/Linux) 或 `Cmd + Enter` (Mac) 快捷键发送消息。

#### Scenario: Send Message via Shortcut
- Given User has typed a message in the input box
- When User presses `Cmd + Enter`
- Then The message is sent
- And The input box is cleared

### Requirement: Process Visualization (Chain of Thought)
系统 **MUST** 展示智能体的思考和执行过程（即 CoT 或 Tool Calls）。
- **Visibility**: 默认显示主要步骤（如“正在查询数据库...”、“正在分析数据...”）。
- **Details**: 支持展开/折叠查看详细的输入/输出（如 SQL 语句、API 返回的 JSON）。

#### Scenario: View Thinking Process
- Given Agent is processing a user request
- When The agent executes an internal step (e.g., Tool Call)
- Then The UI displays a progress indicator or log entry for that step
- And The user can click to expand details

### Requirement: Independent Debugging Page
系统 **MUST** 提供一个独立的页面用于智能体调试。

#### Scenario: Navigate to Agent Debugging
- Given User logs into the dashboard
- When User clicks "智能体调试" in the sidebar
- Then The system navigates to `/dashboard/agent-debug`
- And The "智能体调试" page is displayed

### Requirement: Menu Accessibility
“智能体调试”菜单 **MUST** 在 Dashboard 侧边栏中可见。

#### Scenario: Menu Visibility
- Given User is on the dashboard
- Then The "智能体调试" menu item is visible in the sidebar
