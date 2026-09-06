## ADDED Requirements

### Requirement: 数据集状态控制 (Dataset Status Control)
系统 **MUST** 支持对数据集进行启用或禁用操作。

#### Scenario: 数据库字段扩展
- **WHEN** 执行数据库迁移脚本
- **THEN** `meta_datasets` 表新增 `status` 字段，类型为 TINYINT，默认值为 1 (启用)。

#### Scenario: 智能体检索过滤
- **WHEN** 智能体调用 `get_dataset_schema` 搜索元数据
- **THEN** 系统仅返回 `status=1` 的数据集。
- **AND** 已禁用 (`status=0`) 的数据集即使关键词匹配也不得出现在结果中。

#### Scenario: 前端状态切换
- **WHEN** 管理员在数据集列表页面点击卡片上的状态开关（药丸形状）
- **THEN** 系统立即调用 API 更新该数据集的状态。
- **AND** 界面实时反映最新的启用/禁用状态。
