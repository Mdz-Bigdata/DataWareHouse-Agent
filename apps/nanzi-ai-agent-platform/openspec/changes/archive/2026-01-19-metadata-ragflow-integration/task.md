# Task List: 元数据发布至 RAGFlow

## Phase 1: 基础设施与数据准备
- [ ] **T1.1 数据库变更**: 创建 `V29-add_rag_dataset_fields.sql` 并添加字段。
- [ ] **T1.2 模型更新**: 更新 `app/models/metadata.py` 增加字段映射。
- [ ] **T1.3 Schema 更新**: 更新 `app/schemas/metadata.py` 使 API 能返回 rag 状态。

## Phase 2: RAGFlow 客户端扩展
- [ ] **T2.1 增加 Dataset 管理接口**: `list_datasets`, `create_dataset`。
- [ ] **T2.2 增加文件管理接口**: `upload_document`, `delete_document`, `list_documents`。
- [ ] **T2.3 增加解析控制接口**: `parse_document`。

## Phase 3: 同步逻辑实现
- [ ] **T3.1 Markdown 模板开发**: 实现根据 Table 数据生成 SQL 优化的 Markdown。
- [ ] **T3.2 同步 Service 开发**: 实现 `MetadataRagService`，包含 KB 自动创建、旧文件删除、新文件上传、触发解析的全流程。
- [ ] **T3.3 异常处理与状态更新**: 确保同步失败时记录错误状态，同步成功更新时间戳。
- [ ] **T3.4 同步详情日志**: 补充各步骤的 `logger.info` 埋点，记录 API 交互详情。
- [ ] **T3.5 级联删除**: 修改 `delete_dataset`，在删除本地记录时同步删除 RAGFlow 上的对应 KB。

## Phase 4: API 与 UI 对接
- [ ] **T4.1 API 暴露**: 在 `metadata.py` 中增加 `/datasets/{id}/rag/sync` 接口。
- [ ] **T4.2 列表过滤**: 修改 `endpoints/ragflow.py`，默认过滤掉 `meta-` 开头的知识库，防止污染通用选择界面。
- [ ] **T4.3 前端状态展示**: 修改 `MetadataDatasets.vue`，为卡片添加状态 Badge。

## Phase 5: 查询验证
- [ ] **T5.1 增强 search_metadata**: 适配 `metadata_provider=ragflow` 时的检索路径。
- [ ] **T5.2 路由与详情日志**: 在 Gateway 处增加 `metadata_provider` 路由结果日志，及 RAG 检索耗时日志。
- [ ] **T5.3 自动化测试**: 验证同步流程及 RAG 模式下的 Schema 返回质量。
