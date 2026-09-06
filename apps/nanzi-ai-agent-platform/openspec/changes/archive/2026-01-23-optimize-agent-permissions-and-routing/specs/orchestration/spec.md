# orchestration Spec Delta

## MODIFIED Requirements

### Requirement: 自动路由候选集限制
**Scenario: 自动路由候选筛选**
- **WHEN** 系统执行自动路由逻辑 (`RouterService`)
- **THEN** 构建给 LLM 的候选智能体列表必须仅包含 `is_system=True` **且** `is_enabled=True` 的智能体。
- **AND** 非系统级智能体（无论是否启用）均不参与自动路由决策。
