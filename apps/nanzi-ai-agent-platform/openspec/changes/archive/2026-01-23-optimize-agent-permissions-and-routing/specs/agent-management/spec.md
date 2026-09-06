# agent-management Spec Delta

## MODIFIED Requirements

### Requirement: 权限分配列表过滤
**Scenario: 管理员获取可分配智能体列表**
- **WHEN** 管理员在权限管理界面请求“智能体列表”以分配给用户
- **THEN** 返回的列表必须仅包含 `is_system=True` (系统级) **且** `is_enabled=True` (已启用) 的智能体。
- **AND** 禁用的智能体或非系统级智能体不应出现在分配候选项中。
