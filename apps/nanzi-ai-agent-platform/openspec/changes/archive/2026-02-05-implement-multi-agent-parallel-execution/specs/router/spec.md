# 路由服务 (Router Service)

## MODIFIED Requirements

### Requirement: 支持多智能体路由返回
Router LLM **MUST** 能够识别复合意图，并返回一个主要智能体 (Primary Agent) 和可选的次要智能体列表 (Secondary Agents)。

#### Scenario: 复合意图路由
- **Given** 用户提问 "查询上海机房的服务器数量，并找出相关的维护制度文档"。
- **And** 系统中有 "chat-bi" (负责数据) 和 "knowledge-base" (负责文档) 两个智能体。
- **And** 请求参数 `enable_multi_agent` 为 `True`。
- **When** RouterService 处理该请求。
- **Then** 返回的 RouteResult 应包含 `agent_id="chat-bi"` 和 `secondary_agents=["knowledge-base"]`。
- **And** `confidence` 分数应保持较高 (> 0.8)。

#### Scenario: 单一意图保持兼容
- **Given** 用户提问 "昨天销售额是多少"。
- **When** RouterService 处理该请求。
- **Then** 返回的 RouteResult 应包含 `agent_id="chat-bi"` 和空的 `secondary_agents`。

#### Scenario: 开关控制
- **Given** 用户提问涉及多意图。
- **But** 请求参数 `enable_multi_agent` 为 `False`。
- **When** RouterService 处理该请求。
- **Then** 返回的 RouteResult 应仅包含 `primary_agent`，忽略 `secondary_agents`。

### Requirement: 路由 Prompt 升级 (V6)
系统提示词 **MUST** 基于 V5 版本进行升级，明确指示 LLM 在必要时输出多智能体策略。

#### Scenario: Prompt 包含多智能体指令
- **Given** RouterService 初始化。
- **When** 生成 System Prompt。
- **Then** Prompt 内容应保留 V5 的指代消解和冲突判定逻辑。
- **And** 新增关于 "复合意图识别" 的 Step。
- **And** 输出 JSON 格式增加 `secondary_agents` 字段。