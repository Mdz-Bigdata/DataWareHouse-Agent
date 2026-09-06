## 1. 数据库实现 (Database Implementation)
- [x] 1.1 创建 `db-prod/V3-create_metadata_tables.sql`，包含 `meta_datasets`, `meta_tables`, `meta_columns` 表结构。
- [x] 1.2 执行 SQL 脚本以初始化数据库表。

## 2. 后端实现 (Backend Implementation)
- [x] 2.1 创建 `app/models/metadata.py` (SQLAlchemy 模型)。
- [x] 2.2 创建 `app/services/metadata_service.py` (CRUD 逻辑)。
- [x] 2.3 **[管理侧]** 创建 `app/api/portal/endpoints/metadata.py` (管理后台用):
    - [x] `GET /datasets`, `POST /datasets` (增删改查数据集)
    - [x] `POST /tables/import` (DDL 导入/生成预览)
    - [x] `POST /tables` (保存/更新表结构)
- [x] 2.4 **[服务侧]** 创建 `app/api/v1/endpoints/metadata.py` (AI/外部集成用):
    - [x] `GET /datasets/{id}/schema` (获取 AI 专用的 Schema)
    - [x] `GET /datasets/{id}/yaml` (获取 RAG 专用的 YAML)
- [x] 2.5 注册路由到 `portal_router` 和 `v1_router`。

## 3. 系统配置与开关 (System Configuration)
- [x] 3.1 在 `app/core/config.py` 或数据库配置表新增以下配置项：
    - [x] `METADATA_PROVIDER`: 默认为 `local` (可选 `ragflow`)
    - [x] `RAGFLOW_API_URL`: RAGFlow 服务地址
    - [x] `RAGFLOW_API_KEY`: RAGFlow 认证 Key
- [x] 3.2 改造 `get_dataset_schema` 工具：根据 `METADATA_PROVIDER` 开关，决定是调用本地 Local Service 还是调用 RAGFlow API。

## 4. 元数据助手实现 (Metadata Assistant Implementation)
- [x] 4.1 实现 `MetadataGeneratorService`，支持接收 DDL 字符串。
- [x] 4.2 实现 LLM Prompt Chain，从 DDL 中提取业务术语、同义词和枚举值。
- [x] 4.3 暴露生成接口 `POST /api/v1/metadata/generate`。

## 5. 智能体集成 (Agent Integration)
- [x] 5.1 修改 `app/services/ai/tools/data_api.py`，新增/改造 `get_dataset_schema(dataset_name)` 工具。
- [x] 5.2 实现逻辑：
    - 读取系统配置 `METADATA_PROVIDER`。
    - **Local**: 调用本地 `metadata_service.export_yaml`。
    - **RAGFlow**: (未来) 组装请求调用 `RAGFLOW_API_URL`。
- [x] 5.3 智能体流程更新：先让 Agent 选择合适的数据集，提取上下文，再生成 SQL。

## 6. 验证 (Verification)
- [x] 6.1 使用 cURL/Postman 手动测试 API。
- [x] 6.2 使用样例 DDL 验证 LLM 生成效果。
- [x] 6.3 验证 Agent 在 Local 模式下能否正确读取元数据。
