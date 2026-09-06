# Change: Metadata Management

## Why
当前的 Text-to-SQL 能力依赖于原始的 DDL，缺乏关键的业务上下文（如中文字段名、同义词、枚举值含义）。
为了支持 "ChatBI" 和未来基于 RAG 的 Schema 检索，我们需要一个专门的元数据管理系统，用于存储、管理和辅助生成语义化的元数据。

## What Changes
*   **Database**: 在 `db-prod` 中新增 3 张表 (`meta_datasets`, `meta_tables`, `meta_columns`) 以本地存储元数据。
*   **Backend**: 新增 `MetadataService` 和 RESTful API (`/api/portal/metadata/*`) 用于管理端的增删改查。
*   **Assistant**: 新增 `MetadataGeneratorService`，利用 LLM 根据 DDL 自动生成元数据建议值。
*   **Frontend**: 在管理控制台开发用于管理数据集和表语义的 Web 界面。

## Impact (影响)
*   **受影响的 Specs**: `metadata-management` (新增能力)。
*   **受影响的代码**:
    *   `db-prod/V3-create_metadata_tables.sql` (新增)
    *   `app/api/v1/metadata.py` (新增)
    *   `app/services/metadata_service.py` (新增)
    *   `app/models/metadata.py` (新增)
    *   `app/core/config.py` (可能需要配置更新)

## Future Migration (未来迁移)
*   **RAGFlow 集成**: 由于我们所有的元数据都已高度结构化（Stored in SQL）并且支持标准化的 YAML 导出，未来迁移至 RAGFlow 时：
    1.  可以直接调用 `GET /yaml` 接口批量导出所有 YAML。
    2.  或者编写一个脚本，直接读取 MySQL 的 `meta_columns` 表，将其转换为 RAGFlow 知识库所需的 Chunk 格式。
    3.  迁移成本极低，甚至可以双向同步。
