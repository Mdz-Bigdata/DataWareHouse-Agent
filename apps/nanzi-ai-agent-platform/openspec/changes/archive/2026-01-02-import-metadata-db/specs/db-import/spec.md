## ADDED Requirements

### Requirement: External Database Connection
系统 MUST 支持连接到外部数据库以提取 DDL 定义。
- **Supported DBs**: MySQL, ClickHouse, Oracle.
- **Operations**: 测试连接 (Test Connection), 获取表列表 (List Tables).

#### Scenario: Successful MySQL Connection Test
- **WHEN** 用户输入正确的 MySQL 主机、端口、用户名和密码，并点击“测试连接”
- **THEN** 系统返回连接成功消息。

### Requirement: DDL Extraction and Payload Loading
系统 MUST 能够从选定的外部表中提取原始 DDL 并将其填充到导入向导的文本框中。

#### Scenario: Load Table DDL to Textarea
- **WHEN** 用户从表列表中选择 "orders" 和 "order_items"，并确认导入
- **THEN** 系统后端执行 `SHOW CREATE TABLE`，将生成的 DDL 拼接后填入 `SmartImportWizard` 的输入框中，供 AI 进一步分析。
