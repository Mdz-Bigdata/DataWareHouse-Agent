# Change: Import Metadata from Database

## Why
目前元数据智能导入（Smart Import）仅支持通过粘贴 DDL 文字进行分析。对于拥有大量表结构的现有数据库，手动导出并粘贴 DDL 非常繁琐。
通过直接连接数据库获取表结构及其 DDL，可以极大地提升元数据初始化的效率和用户体验。

## What Changes
1.  **Backend Services**:
    -   新增 `DBImportService`，支持动态连接 MySQL、ClickHouse 和 Oracle（需安装驱动）。
    -   实现连接测试、获取表列表、获取指定表 DDL 的逻辑。
2.  **API Endpoints**:
    -   在 `metadata.py` 中新增 `POST /metadata/db/test-connection`。
    -   新增 `POST /metadata/db/tables`。
    -   新增 `POST /metadata/db/ddl`。
3.  **Frontend Components**:
    -   新增 `DatabaseImportModal.vue` 组件，用于输入连接信息和选择表。
    -   更新 `SmartImportWizard.vue`，在输入框上方增加“从数据库加载”按钮。
    -   在 `metadata.ts` API 层增加对应的后端接口调用代码。
