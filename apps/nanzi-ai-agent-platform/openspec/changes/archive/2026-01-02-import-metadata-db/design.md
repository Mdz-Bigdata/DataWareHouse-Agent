# Design: Database DDL Import

## Architecture
该功能采用分层架构，解耦数据库驱动与 API 接口。

### Backend
- **Controller**: `metadata.py` 暴露 REST 接口。
- **Service**: `DBImportService` 负责调度不同的 DDL 提取器。
- **Adapters**:
    - `MySQLFetcher`: 使用 `aiomysql` 执行 `SHOW CREATE TABLE`。
    - `ClickHouseFetcher`: 使用 `asynch` 执行 `SHOW CREATE TABLE`。
    - `OracleFetcher`: (Optional) 预留接口。

### Frontend
- **Trigger**: 在 `SmartImportWizard` 的 DDL 输入框上方增加入口。
- **Modal**: `DatabaseImportModal` 包含：
    - 数据库类型选择 (Icon Grid)。
    - 连接表单 (Host, Port, User, Password, DB/Namespace)。
    - 测试连接状态反馈。
    - 表树/列表选择器。
- **Integration**: 选中的 DDL 字符串通过 `emit` 返回给 `SmartImportWizard` 并追加到输入框。

## Security
- 数据库连接信息仅在请求期间于内存中使用，系统**不持久化**外部数据库的密码。
- 建议用户使用具有 `READ ONLY` 权限的账号。
