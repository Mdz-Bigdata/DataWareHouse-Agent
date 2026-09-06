# permissions Spec Delta

## MODIFIED Requirements

### Requirement: 混合访问控制策略 (Hybrid Access Control)
系统必须实施区分“系统级”与“用户级”智能体的混合鉴权逻辑。

**Scenario: 访问系统级智能体**
- **GIVEN** 目标智能体 `is_system=True`
- **WHEN** 用户尝试访问（对话、查看详情）
- **THEN** 系统检查 `ai_agent_user_permissions` 表中是否存在该用户对该智能体的记录。
- **IF** 存在记录 -> 允许访问。
- **ELSE** -> 拒绝访问。

**Scenario: 访问用户级智能体**
- **GIVEN** 目标智能体 `is_system=False`
- **WHEN** 用户尝试访问
- **THEN** 系统检查智能体的 `created_by` 字段是否等于当前用户 ID。
- **AND** 检查智能体的 `is_enabled` 是否为 `True`。
- **IF** 是创建者且已启用 -> 允许访问。
- **ELSE** -> 拒绝访问。
