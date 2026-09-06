# Capability: Multi-Agent Orchestration

## ADDED Requirements

### Requirement: Intent-based Routing
系统 **MUST** 能够自动识别用户意图，并在多个可用智能体之间进行动态分发。

#### Scenario: User asks about data
- **Given**: 系统中存在一个名为 `ChatBI` 的智能体，其描述为“处理结构化数据查询和报表”。
- **When**: 用户输入“帮我查询上周的订单量趋势”。
- **Then**: 路由中心应识别出意图为 `DATA_QUERY`。
- **And**: 系统应自动调度 `ChatBI` 智能体进行回复。

### Requirement: Agent Metadata Synchronization
智能体管理系统 **SHALL** 支持配置路由所需的元数据。

#### Scenario: Admin configures routing description
- **Given**: 管理员正在编辑一个名为 `StockAgent` 的智能体。
- **When**: 管理员在“功能描述”中输入“提供最新的股票报价和市场分析”。
- **Then**: 该描述应持久化到数据库。
- **And**: 语义路由索引应自动更新，确保新描述生效。
