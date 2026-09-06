## Why

随着元数据规模的增加，现有的本地检索（local 模式）使用基于 SQL 的模糊匹配（LIKE）在检索准确率上存在局限。主要表现在：它无法处理同义词、近义词，且只能通过简单的名称包含关系来召回，缺乏语义感知的召回能力。而系统的 RAG 模式在外部网络异常或未配置 RAGFlow 时又不可用。因此，我们需要将本地检索（local 模式）升级为使用本地运行的 Redis 向量搜索（RediSearch），在保证完全本地化运行且零网络外部依赖的前提下，实现高精准的语义召回，以提升 ChatBI 场景下的 Text-to-SQL 生成效果。

## What Changes

- **元数据本地检索机制重构**：将本地模式的模糊 LIKE 匹配机制彻底替换为使用本地 Redis Stack (RediSearch) 执行的 KNN 向量搜索。
- **本地元数据索引生命周期管理**：在数据集新建、编辑、删除和智能导入时，自动生成特征描述文本，计算 Embedding 并实时更新至 Redis 向量数据库。
- **权限安全门禁前置**：在向量搜索前执行前置权限过滤（Pre-filtering），利用用户有权访问的 Dataset ID 限制 Redis KNN 的检索范围，杜绝水平越权与召回率退化。
- **RAGFlow 多态对齐**：本地 Redis 检索存储和返回的数据格式在物理字段（如 `doc_name`, `content`）与相似度换算上完全对齐 RAGFlow 接口，使系统调用时实现平滑多态适配。

## Capabilities

### New Capabilities
- `local-redis-vector-search`: 本地元数据检索升级为 Redis 向量检索功能，支持前置权限标签过滤与 RAGFlow 数据及返回格式的多态对齐。

### Modified Capabilities

## Impact

- **后端路由与接口**：
  - `/api/v1/schema` 路由处理逻辑修改，引入 `EmbeddingClient` 生成提问向量，并通过 Redis 客户端执行 HNSW KNN 检索。
- **服务层 (Services)**：
  - `app/services/metadata_service.py` 关联修改，支持在数据变更时提取特征文本并同步更新本地 Redis 向量库。
- **依赖系统**：
  - 依赖本地的 Redis 实例（须支持 RediSearch 模块，即 Redis Stack）以及系统的 Embedding 微服务。
