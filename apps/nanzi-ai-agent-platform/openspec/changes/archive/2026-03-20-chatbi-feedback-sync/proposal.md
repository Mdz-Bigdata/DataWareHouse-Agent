## Why
ChatBI 目前无法利用用户反馈来优化 SQL 生成质量。通过收集用户点赞/踩的反馈，我们可以沉淀优质 SQL 案例并将其同步到 RAGFlow，为后续的 Few-Shot 注入提供数据基础。这能显著提高 AI 在处理复杂业务查询时的准确性和一致性。

## What Changes
- **数据库扩展**：
  - 在 `ai_agent_execution_history` 中增加 `feedback` 字段，用于记录通用的用户反馈状态。
  - 新建 `ai_chatbi_examples` 表，专门存储经过验证的优质 SQL 经验及其关联的元数据（Dataset ID）。
  - 新建 `ai_chatbi_example_usages` 表，记录每次经验案例被实际引用的情况。
- **核心服务层**：
  - 实现 `ExampleService`，负责从 trace 记录中智能提取成功的 SQL、管理经验库状态以及与 RAGFlow 的异步数据同步。
- **API 接口**：
  - 面向用户端：实现通用的 `/api/chat/feedback` 接口，支持对所有智能体回答的反馈收集。
  - 面向管理端：实现列表查询、详情查看、审核批准、驳回、废弃以及手动触发 RAGFlow 同步的接口。
- **权限与资源**：
  - 注册「ChatBI 经验库」管理菜单及对应的细粒度权限控制（列表、审核、同步、删除）。
- **同步机制**：
  - 对接 RAGFlow API，将 `approved` 状态的经验异步上传至知识库，并记录同步状态和错误信息。

## Capabilities

### New Capabilities
- `chatbi-feedback-collection`: 收集并存储用户对回答的反馈，针对 SQL 类请求提取最终成功的查询语句。
- `chatbi-example-management`: 管理后台功能，支持对经验库条目进行多状态审核管理（pending/approved/rejected/deprecated）。
- `ragflow-example-sync`: 异步同步经验库条目至 RAGFlow 知识库，支持重试和状态追踪。

### Modified Capabilities
- `agent-execution-history`: 增强执行历史记录功能，支持持久化存储用户反馈状态。

## Impact
- **数据库 (`db-prod/`)**：新增 `V50-create_chatbi_examples.sql` (表结构) 和 `V51-register_feedback_resources.sql` (菜单与权限)。
- **后端模型 (`app/models/`)**：新增 `ChatBIExample` 和 `ChatBIExampleUsage` 模型。
- **API 接口**：新增反馈收集及管理端 API，修改现有 Portal 接口权限校验。
- **外部系统**：依赖 RAGFlow 知识库 API 进行向量化检索准备。
