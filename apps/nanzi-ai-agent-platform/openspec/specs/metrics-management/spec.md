# metrics-management Specification

## Purpose
TBD - created by archiving change optimize-metadata-management. Update Purpose after archive.
## Requirements
### Requirement: 创建指标 (Create Metric)
系统 MUST 允许创建一个与数据集关联的新指标。
- Given 一个有效的数据集 ID 和指标详情（名称="Revenue", 逻辑="sum(amount)"）。
- When 调用创建指标 API。
- Then 一个新的 `MetaMetric` 记录被创建并返回 ID。

#### Scenario: 标准创建 (Standard Creation)
- Given 一个有效的数据集 ID 和指标详情（名称="Revenue", 逻辑="sum(amount)"）。
- When 调用创建指标 API。
- Then 一个新的 `MetaMetric` 记录被创建并返回 ID。

### Requirement: 指标列表 (List Metrics)
系统 MUST 允许列出特定数据集的所有指标。
- Given 一个数据集 ID。
- When 调用列出指标 API。
- Then 返回属于该数据集的 `MetaMetric` 对象列表。

#### Scenario: 查看列表 (View List)
- Given 一个数据集 ID。
- When 调用列出指标 API。
- Then 返回属于该数据集的 `MetaMetric` 对象列表。

### Requirement: 更新指标 (Update Metric)
系统 MUST 允许更新现有指标的详情。
- Given 一个有效的指标 ID 和新的逻辑字符串。
- When 调用更新 API。
- Then 指标记录被更新并返回新版本。

#### Scenario: 逻辑更新 (Logic Update)
- Given 一个有效的指标 ID 和新的逻辑字符串。
- When 调用更新 API。
- Then 指标记录被更新并返回新版本。

### Requirement: 删除指标 (Delete Metric)
系统 MUST 允许删除一个指标。
- Given 一个有效的指标 ID。
- When 调用删除 API。
- Then 该指标从数据库中移除。

#### Scenario: 清理 (Clean Up)
- Given 一个有效的指标 ID。
- When 调用删除 API。
- Then 该指标从数据库中移除。

### Requirement: 指标校验 (Metric Validation)
系统 MUST 校验指标名称在数据集内是唯一的。

#### Scenario: 重名检查 (Duplicate Check)
- Given 现有名为 "Revenue" 的指标。
- When 尝试在同一数据集中创建另一个名为 "Revenue" 的指标。
- Then 系统返回 400 Bad Request 错误。

