## 1. 后端 RAGFlow 管理接口

- [x] 1.1 梳理 RAGFlow datasets、documents、chunks、retrieval 接口返回结构，补齐 `RagFlowClient` 的兼容解析与错误处理
- [x] 1.2 新增本地知识库元数据模型和数据库迁移
- [x] 1.3 新增知识库元数据服务，支持按 RAGFlow Dataset ID 创建、更新、删除/标记删除和合并展示
- [x] 1.4 在 `app/api/portal/endpoints/ragflow.py` 增加创建知识库接口，并在 RAGFlow 创建成功后写入本地元数据
- [x] 1.5 在 `app/api/portal/endpoints/ragflow.py` 增加删除知识库接口，并执行真实删除与本地元数据状态更新
- [x] 1.6 在 `app/api/portal/endpoints/ragflow.py` 增加知识库文档列表接口
- [x] 1.7 在 `app/api/portal/endpoints/ragflow.py` 增加不限制文件类型的文档上传接口
- [x] 1.8 在 `app/api/portal/endpoints/ragflow.py` 增加文档真实删除接口
- [x] 1.9 在 `app/api/portal/endpoints/ragflow.py` 增加手动触发文档解析接口
- [x] 1.10 在 `app/api/portal/endpoints/ragflow.py` 增加检索测试接口

## 2. 权限与路由

- [x] 2.1 在前端权限常量中新增知识库开发平台菜单权限与操作权限
- [x] 2.2 在后端知识库管理接口中增加对应操作权限校验
- [x] 2.3 在前端路由中新增 `/dashboard/knowledge-bases` 和 `/dashboard/knowledge-retrieval-test`
- [x] 2.4 在控制台侧边栏中于「CHATBI 开发平台」后新增「知识库开发平台」分组
- [x] 2.5 确认有对应操作权限的非管理员用户可以创建、删除、上传、解析和检索测试

## 3. 知识库管理页面

- [x] 3.1 新增 `KnowledgeBaseManagement.vue` 页面骨架和 API 调用层
- [x] 3.2 实现合并 RAGFlow 数据和本地元数据的知识库列表展示、刷新、错误态和空状态
- [x] 3.3 实现创建知识库弹窗和本地扩展元数据提交流程
- [x] 3.4 实现真实删除知识库确认弹窗和删除流程
- [x] 3.5 实现知识库详情区域或详情弹窗，用于展示文档列表
- [x] 3.6 实现不限制文件类型的文档上传、真实删除和手动触发解析操作
- [x] 3.7 按权限隐藏或禁用创建、删除、上传、解析等操作按钮
- [x] 3.8 实现本地知识库扩展元数据编辑入口

## 4. 检索测试页面

- [x] 4.1 新增 `KnowledgeRetrievalTest.vue` 页面骨架和 API 调用层
- [x] 4.2 复用知识库选择能力，支持选择一个或多个知识库
- [x] 4.3 实现 query、top_k、similarity_threshold、vector_similarity_weight 输入控件
- [x] 4.4 实现检索提交、加载态、错误态和参数校验
- [x] 4.5 展示命中文档名、相似度、Chunk ID 和内容片段
- [x] 4.6 支持复制 dataset_ids 或检索结果关键信息用于 Agent 配置排查

## 5. 审计日志

- [x] 5.1 创建知识库成功/失败时记录审计日志
- [x] 5.2 删除知识库成功/失败时记录审计日志
- [x] 5.3 上传文档、删除文档、触发解析成功/失败时记录审计日志
- [x] 5.4 执行检索测试成功/失败时记录审计日志，记录 query 摘要、dataset_ids、参数和命中数量
- [x] 5.5 确认审计日志不记录 RAGFlow API Key 或其他敏感字段

## 6. 测试与验证

- [x] 6.1 为 RAGFlow Portal API 增加后端单元测试或集成测试
- [x] 6.2 为本地知识库元数据合并展示和异常状态增加测试覆盖
- [x] 6.3 为权限过滤、无权限操作和有权限非管理员操作增加测试覆盖
- [x] 6.4 为知识库操作审计日志增加测试覆盖
- [x] 6.5 执行前端类型检查并修复本变更新增的类型问题
- [ ] 6.6 手动验证知识库创建、文档上传、手动解析、真实删除、审计日志和检索测试流程
- [x] 6.7 更新 `tests/CHECKLIST.md`，记录新增知识库开发平台验证项
