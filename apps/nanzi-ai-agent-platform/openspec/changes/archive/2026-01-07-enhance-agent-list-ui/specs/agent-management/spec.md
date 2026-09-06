## ADDED Requirements

### Requirement: 智能体状态管理 (Agent Status Management)
系统 MUST 允许管理员对智能体进行“启用”或“禁用”操作。

#### Scenario: 禁用智能体
- **WHEN** 管理员在列表中点击某活跃智能体的“禁用”按钮
- **THEN** 该智能体的状态变更名为“已禁用” (Disabled)，且在后续的智能体选择或调用中（除历史记录查看外）不再对普通用户可见。

### Requirement: 智能体列表筛选 (Agent List Filtering)
系统 MUST 在智能体列表页提供筛选功能，以便用户快速查找目标智能体。

#### Scenario: 按状态筛选
- **WHEN** 用户在筛选栏选择“已禁用”
- **THEN** 列表仅显示 `is_enabled=false` 的智能体。

### Requirement: 封装的版本管理 (Encapsulated Version Management)
系统 MUST 提供独立的视图（如模态框或抽屉）来管理智能体的版本，而不是在主列表页并排显示，以节省空间并聚焦内容。

#### Scenario: 打开版本管理
- **WHEN** 用户在智能体卡片上点击“版本管理”按钮
- **THEN** 系统打开一个覆盖层（模态框/抽屉），列出该智能体的所有历史版本，并允许用户执行创建新版本、发布版本等操作。
