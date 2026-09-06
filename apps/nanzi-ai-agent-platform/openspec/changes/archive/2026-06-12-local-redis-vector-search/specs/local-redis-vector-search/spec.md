## ADDED Requirements

### Requirement: 本地元数据向量化与 Redis 同步
系统 MUST 在数据集（MetaDataset）生命周期发生变更（如新建、修改元数据属性、导入表结构、删除）时，将最新的元数据特征生成 Embedding 向量，并写入指定的 Redis HASH 缓存。

#### Scenario: 成功同步数据集元数据向量
- **WHEN** 管理员在元数据管理后台新建或修改一个名为 "机房资产列表" 的数据集并成功保存
- **THEN** 系统遍历该数据集下的每一个物理表及指标，分别利用 `MetadataRagService` 生成各自的单表/指标 YAML 文本，计算向量，并同步写入对应的 Redis Key `metadata:dataset:<id>:table:<table_name>` 和 `metadata:dataset:<id>:metrics`，每个 HASH 结构均对齐包含 `doc_name`, `content`, `dataset_id` 和 `embedding` 向量字段

### Requirement: 元数据索引防混淆隔离
系统在初始化 Redis 向量索引（RediSearch Index）时，MUST 严格通过 PREFIX 参数将扫描范围限定为元数据特定的命名空间，确保向量搜索不会混入非元数据类型的 Redis Key。

#### Scenario: 初始化元数据专属向量索引
- **WHEN** 系统启动或检测到元数据检索请求且索引不存在时
- **THEN** 系统向 Redis 执行 `FT.CREATE` 命令，并将 `PREFIX` 参数指定为 `1 metadata:dataset:`，且向量距离度量使用 `COSINE`，以避免混淆诸如 `memory:summary:` 等其他业务的哈希 Key

### Requirement: 前置安全权限过滤检索
系统在执行本地模式下的元数据检索（如 `/api/v1/schema`）时，MUST 首先根据当前普通用户的角色权限拉取其有权访问的数据集 ID 列表，并作为 RediSearch 查询的前置 Tag 过滤条件，杜绝水平越权与召回率退化。超级管理员（Admin）则使用全量通配符检索。

#### Scenario: 普通用户带权限检索元数据
- **WHEN** 普通用户（非 Admin）发送请求检索元数据，该用户仅对 ID 为 1 和 3 的数据集拥有已启用的访问权限
- **THEN** 系统将 ID 列表拼装成 Tag 过滤条件 `(@dataset_id:{1|3})`，向 Redis 向量数据库发起 HNSW KNN 检索，仅在 ID 为 1 和 3 的子集中搜索，并以 `similarity` 打分高低排序召回前 Top K 个数据集的元数据

#### Scenario: 超级管理员无过滤全量检索元数据
- **WHEN** 超级管理员（Admin）发送请求检索元数据
- **THEN** 系统跳过有权 ID 列表的前置过滤拼接，向 Redis 向量数据库发起使用 `*` 全量通配符的 HNSW KNN 检索 `*=>[KNN 5 @embedding $vec AS score]`

### Requirement: 检索返回格式与 RAGFlow 多态对齐
本地 Redis 检索召回的元数据片段在返回拼接和格式化逻辑上，MUST 与 RAGFlow 的 Chunks 格式、分隔符及相似度（Similarity）算法完全对齐，以保证系统整体的无感调用与多态适配。

#### Scenario: 检索召回返回格式对齐
- **WHEN** 本地 Redis 检索成功命中数据集
- **THEN** 系统解析出 HASH 的 `doc_name` 与 `content`，将余弦距离 `score` 转换为对齐 RAGFlow 的相似度 `1.0 - score`，并以 `--- Source: <doc_name> (Sim: <similarity>) ---\n<content>` 格式拼接返回 `schema_context`，同时填充 `hits` 数组
