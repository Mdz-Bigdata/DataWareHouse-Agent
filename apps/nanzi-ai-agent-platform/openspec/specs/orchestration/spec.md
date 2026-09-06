# 智能路由与编排 (Orchestration & Routing)

## Purpose
`RouterService` 是多智能体系统的核心中枢，负责根据用户意图和对话上下文，将请求分发给最合适的智能体（Agent）。

## Requirements
### Requirement: Intelligent Routing Strategy
The system SHALL route user queries to the most appropriate agent based on intent and context.

#### Scenario: Context Aware Routing
- **WHEN** user sends a query
- **THEN** system analyzes input using LLM (after optional heuristic shortcuts)
- **AND** system considers recent conversation history (N turns)
- **AND** system checks agent `description` and `capabilities`
- **AND** system returns target agent ID with confidence score

#### Scenario: Heuristic Shortcuts (No LLM)
- **WHEN** user sends a pure greeting (`looks_like_greeting`)
- **THEN** system routes to general assistant without invoking router LLM
- **WHEN** user requests external/web search (`looks_like_web_search_query`)
- **THEN** system routes to general assistant without invoking router LLM
- **WHEN** previous turn was handled by a data-query agent AND `resolve_data_agent_session_affinity()` returns `BREAK`
- **THEN** system breaks ChatBI session affinity and routes to general assistant without invoking router LLM
- **WHEN** previous turn was handled by a data-query agent AND affinity is `UNCERTAIN`
- **THEN** system does NOT heuristic-fallback to general assistant; it proceeds to semantic router LLM
- **WHEN** previous turn was handled by a data-query agent AND affinity is `KEEP`
- **THEN** system does NOT break ChatBI affinity via this heuristic shortcut

#### Scenario: Direct Agent Selection
- **WHEN** request includes `agent_id` or `agent_name` (including Embed expert mode)
- **THEN** system skips router LLM and loads the specified agent
- **AND** sets `route_hints.direct_agent_selection = true` (disables main-assistant data hallucination guard)

#### Scenario: Fallback Mechanism
- **WHEN** routing confidence is below threshold (e.g., 0.5) or LLM fails after retries
- **THEN** system falls back to `general-chat` or default logic

### Requirement: Implementation Details
The system MUST implement the following data flow and API definitions to support routing logic.

#### Data Flow
1. **输入**: 用户 Query + 对话历史 (Conversation History) + 可选 `last_agent_name`。
2. **处理**:
   - 若未显式指定智能体：依次尝试问候短路、联网短路、ChatBI 亲和性 `BREAK` 打断（`UNCERTAIN` 不短路）。
   - 构建 Prompt，注入 Agent 列表、历史摘要及「禁止机械沿用 ChatBI」提示（当适用）。
   - 调用 LLM 获取 JSON 格式的路由结果（包含 `agent_name`、`confidence`、`turn_labels` 等）。
3. **输出**: 目标 Agent 的标识符及通用会话 hint。

#### API Definitions
**RouterService.route_query**
```python
async def route_query(
    self,
    user_input: str,
    history: Optional[List[dict]] = None,
    *,
    last_agent_name: Optional[str] = None,
    ...
) -> Optional[RouteResult]
```
- **user_input**: 用户当前的文本输入。
- **history**: 对话历史列表，格式为 `[{'role': 'user', 'content': '...'}, ...]`。用于增强语义理解。
- **last_agent_name**: 上一轮处理智能体名称，用于会话粘性与 ChatBI 亲和性判定。

#### Configuration
- **System Prompt**: 内置于代码 `RouterService.DEFAULT_SYSTEM_PROMPT`，定义了路由器的角色和规则。不再从数据库 `system_configs` 读取，也不在"提示词管理"中暴露，避免运营误改导致路由失准。
- **Agent Metadata**: 动态从 `agents` 表加载，作为 Prompt 的上下文。
- **Session affinity**: `DataSessionAffinity` + `resolve_data_agent_session_affinity()` in `intent_service.py`（`KEEP` / `BREAK` / `UNCERTAIN`）；`should_inherit_data_agent_session()` 为「== KEEP」的兼容包装。

## History
- **2026-01-05**: 增加上下文感知能力 (`history` 参数)，优化多轮对话路由准确率。
- **2026-06**: 问候/联网启发式短路；ChatBI 会话粘性修正（`should_inherit_data_agent_session`）；专家直选 `direct_agent_selection`；路由历史上下文注入「禁止机械沿用」提示。
- **2026-07**: ChatBI 会话亲和性三态（仅 `BREAK` 启发式离开；`UNCERTAIN` 进语义路由）。
