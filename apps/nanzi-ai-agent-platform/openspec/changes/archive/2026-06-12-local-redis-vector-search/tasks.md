## 1. 基础依赖与向量索引初始化

- [x] 1.1 在本地 Redis 服务模块中，实现 RediSearch 元数据专属向量索引的自动检测与初始化函数（`ensure_metadata_index()`），指定 PREFIX 为 `metadata:dataset:`，排除其他业务 Key。

## 2. 元数据同步服务改造 (MySQL 到 Redis Vector)

- [x] 2.1 修改元数据服务层（`MetadataService`），新增元数据提取与向量化同步逻辑。在数据集创建和编辑时，遍历数据集下的表和指标生成各自独立的 HASH 同步到 Redis；在数据集删除时级联删除其对应的全部表/指标 Redis Keys。
- [x] 2.2 实现表结构智能导入时的异步元数据特征向量更新管道，保证 DDL 导入后向量索引及时刷新。

## 3. 网关检索接口升级 (RAGFlow 多态对齐)

- [x] 3.1 改造 `/api/v1/schema` 路由。在 local 本地模式下，首先根据当前用户获取有权访问的数据集 ID 列表，无权限则直接短路拦截。
- [x] 3.2 使用 EmbeddingClient 计算查询词向量，并使用有权 ID 列表构建 RediSearch Tag 过滤表达式，执行 FT.SEARCH HNSW KNN 检索。
- [x] 3.3 格式化本地检索的返回输出。将召回的 HASH 属性（`doc_name`, `content`）与转换后的 `similarity` 打分按 RAGFlow 标准拼接为 `schema_context` 及 `hits` 返回，对齐 RAGFlow 接口。

## 4. 验证与测试

- [x] 4.1 编写单元测试验证本地向量索引的创建与隔离防混淆。
- [x] 4.2 编写端到端检索测试，模拟不同用户角色检索元数据，确认前置权限过滤和返回数据格式完美对齐。
