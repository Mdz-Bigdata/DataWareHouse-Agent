# Implementation Plan: Metadata to RAGFlow Integration

## 1. 为什么这么做 (Why)
- **语义搜索增强**: 传统的元数据查询基于关键字匹配，难以处理“用户交易相关的表”这类模糊语义。通过 RAGFlow 向量化，能显著提升 AI 找表的准确率。
- **执行效率**: AI 预先通过 RAG 缩小表范围，可以避免将全量 YAML 塞入 Context，节省 Token 并提升 SQL 生成质量。
- **状态透明**: 通过 UI 上的同步状态徽章，管理员可以清晰掌握元数据在知识库中的新鲜度。

## 2. 准备怎么搞 (How)

### 2.1 数据库与模型 (Database)
- 增加 `rag_dataset_id`, `rag_synced_at`, `rag_sync_status` 字段。
- 修改 `MetaDataset` 模型以支持这些字段，并同步到 `DatasetResponse` Schema。

### 2.2 RAGFlow 客户端增强 (Client)
- 在 `RagFlowClient` 中新增以下内部方法：
    - `create_dataset(name, ...) -> id`: 创建 KB。
    - `upload_document(dataset_id, file_path) -> doc_id`: 上传 Markdown。
    - `delete_document(dataset_id, doc_id)`: 删除旧文件。
    - `parse_document(dataset_id, doc_id)`: 触发解析。

### 2.3 同步核心逻辑 (Sync Logic)
- **一表一 Chunk 保证**: 
    - 采用 **“One” 解析模式** (`chunk_method="one"`)：RAGFlow 会将整个文件作为一个 Chunk 处理，这能完美实现您的“一表一 Chunk”硬约束。
    - 即使使用 `naive` 模式，我们也会将 `chunk_token_num` 硬编码设置为最大值 (例如 `8192`)。
- **先删后加策略**: 通过文件名匹配，同步前删除 RAGFlow 侧的同名文件。

### 2.4 查询路由 (Routing)
- 修改 `get_dataset_schema` 工具：
    - 检查 `metadata_provider` 配置。
    - 若为 `ragflow`，则调用 `RagFlowClient.retrieve`，返回检索到的 Markdown 片段。

### 2.5 UI 列表净化 (UI Filtering)
- **Problem**: 自动生成的 `meta-xxx` 知识库会污染前端 Agent 配置页面的知识库选择下拉框。
- **Solution**: 修改 `app/api/portal/endpoints/ragflow.py` 中的 `/datasets` 代理接口，增加过滤逻辑，**默认隐藏**所有以 `meta-` 开头的系统知识库。

## 3. 为什么这么修改 (Rationale)
- **使用 "One" 解析模式**: 这是 RAGFlow 提供的专门用于“整文档作为一个 Chunk”的模式，比手动调大 token 数更稳健，能确保 Schema 信息的绝对完整。
- **异步处理**: 同步过程涉及多个 API 调用和文件 IO，后端将通过异步任务处理，UI 通过轮询或状态字段展示进度，避免阻塞前端。
- **独立工具逻辑**: 保持元数据查询工具的独立性，不与通用 `knowledge_tool` 耦合，以便于后续针对 SQL 生成场景做专门的提示词优化。

## 4. 关键文件修改列表
- `app/models/metadata.py`: 模型变更。
- `app/services/ai/ragflow_client.py`: API 能力扩展。
- `app/services/metadata_rag_service.py`: **新增**，处理同步逻辑。
- `app/api/portal/endpoints/metadata.py`: 暴露同步接口。
### 4.4 Logging & Monitoring (日志与监控)
- **路由日志**: 每次调用元数据网关时，必须打印 `[Metadata Gateway] Current Provider: [LOCAL/RAGFLOW]`。
- **查询详情日志**: 
    - `local`: 记录 `SELECT` 的数据集 ID。
    - `ragflow`: 记录检索 Query、目标 `dataset_ids` 以及检索结果的 Top-1 相似度。
- **同步全链路日志**: 记录同步过程中的每一个 API 调用状态，例如 `[RAG Sync] Uploading orders.md to Dataset [ID]... Success.`
