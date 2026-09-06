# relationships-management Specification

## Purpose
TBD - created by archiving change optimize-metadata-management. Update Purpose after archive.
## Requirements
### Requirement: 创建关系 (Create Relationship)
系统 MUST 允许创建两个表之间的有向关系。
- Given 源表 ID (Users), 目标表 ID (Orders), 关联类型 ("one_to_many"), 和条件 ("users.id = orders.user_id")。
- When 调用创建关系 API。
- Then 一个新的 `MetaRelationship` 记录被创建。

#### Scenario: 关联表 (Link Tables)
- Given 源表 ID (Users), 目标表 ID (Orders), 关联类型 ("one_to_many"), 和条件 ("users.id = orders.user_id")。
- When 调用创建关系 API。
- Then 一个新的 `MetaRelationship` 记录被创建。

### Requirement: 关系列表 (List Relationships)
系统 MUST 允许列出与数据集关联的关系。
- Given 一个数据集 ID。
- When 调用列出关系 API。
- Then 系统返回源表或目标表属于该数据集的所有关系。

#### Scenario: 查看关系 (View Relationships)
- Given 一个数据集 ID。
- When 调用列出关系 API。
- Then 系统返回源表或目标表属于该数据集的所有关系。

### Requirement: 删除关系 (Delete Relationship)
系统 MUST 允许删除一个关系。
- Given 一个有效的关系 ID。
- When 调用删除 API。
- Then 该关系被移除。

#### Scenario: 移除关联 (Remove Join)
- Given 一个有效的关系 ID。
- When 调用删除 API。
- Then 该关系被移除。

### Requirement: 自关联支持 (Self-Reference Support)
系统 MUST 支持自关联关系（例如 Employee -> Manager）。

#### Scenario: 层级关联 (Hierarchy Link)
- Given 源表 Employees 和目标表 Employees。
- When 创建关系 "employees.manager_id = employees.id"。
- Then 创建成功。

