## MODIFIED Requirements

### Requirement: Intelligent Routing Strategy
The system SHALL route user queries to the most appropriate agent based on intent and context.

#### Scenario: Context Aware Routing
- **WHEN** user sends a query
- **THEN** system analyzes input using LLM
- **AND** system considers recent conversation history (N turns)
- **AND** system checks agent `description` and `capabilities`
- **AND** system returns target agent ID with confidence score

#### Scenario: Fallback Mechanism
- **WHEN** routing confidence is below threshold (e.g., 0.5)
- **THEN** system falls back to `general-chat` or default logic

#### Scenario: Routing with Tool-Enriched Context
- **WHEN** an agent is being selected for a query
- **THEN** the router SHALL also consider the available tools (including newly integrated MCP tools) assigned to that agent to better judge its suitability.

### Requirement: Implementation Details
The system MUST implement the following data flow and API definitions to support routing logic.

#### Data Flow
1. **输入**: 用户 Query + 对话历史 (Conversation History)。
2. **处理**:
   - 构建 Prompt，注入 Agent 列表和历史摘要。
   - 调用 LLM 获取 JSON 格式的路由结果（包含 `agent_name` 和 `confidence`）。
3. **输出**: 目标 Agent 的标识符。

#### API Definitions
**RouterService.route_query**
```python
async def route_query(self, user_input: str, history: Optional[List[dict]] = None) -> Optional[RouteResult]
```
- **user_input**: 用户当前的文本输入。
- **history**: 对话历史列表，格式为 `[{'role': 'user', 'content': '...'}, ...]`。用于增强语义理解。

#### Configuration
- **System Prompt**: 存储在 `system_configs` 表中 (`router_system_prompt`)，定义了路由器的角色和规则。
- **Agent Metadata**: 动态从 `agents` 表加载，作为 Prompt 的上下文。
- **Tool Metadata Injection**: 在构造 Router Prompt 时，系统必须支持注入 Agent 所关联的工具（包含 MCP 工具）的简要说明。
