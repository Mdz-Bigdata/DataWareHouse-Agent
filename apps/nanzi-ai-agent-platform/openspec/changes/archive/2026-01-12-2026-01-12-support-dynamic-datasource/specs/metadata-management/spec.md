## ADDED Requirements

### Requirement: 数据集配置 (Dataset Configuration)
系统 **MUST** 支持在元数据管理界面配置数据集的 Data Source ID，并将其包含在导出的 YAML 上下文中。

#### Scenario: 配置 MySQL 数据源
- **WHEN** 管理员在前端创建或编辑数据集时
- **THEN** 界面提供 "Data Source ID" 输入框。
- **AND** 管理员输入如 `mysql_crm_01` 后，系统保存该字段到数据库。

#### Scenario: YAML 导出包含数据源
- **WHEN** 系统 (Tool) 调用 `export_dataset_yaml` 导出元数据时
- **THEN** 返回的 YAML 必须包含顶级字段 `data_source`。
- **AND** 如果数据库中该字段为空，则自动填充系统默认的 `external_sql_data_source` 值。

### Requirement: 兼容性 (Compatibility)
系统 **MUST** 确保在引入动态数据源后，未配置新字段的历史数据集仍能正常工作。

#### Scenario: 旧数据集处理
- **WHEN** 访问未配置 `data_source` 的旧数据集
- **THEN** 系统默认将其视为 ClickHouse 数据源，保证现有业务不受影响。