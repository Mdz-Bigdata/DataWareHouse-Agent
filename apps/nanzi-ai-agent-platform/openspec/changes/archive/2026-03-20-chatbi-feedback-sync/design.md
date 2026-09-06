# ChatBI 反馈驱动优化系统 - 技术设计 (Phase 1)

## Context

ChatBI 在多轮 ReAct 模式下生成 SQL 存在不确定性。为了提高 SQL 质量，我们需要：
1. **收集反馈**：记录用户对所有智能体回答的点赞/点踩。
2. **提取优质 SQL**：针对 ChatBI 类型的 Trace，自动提取其最后成功的 SQL。
3. **经验库管理**：在管理后台审核并同步这些 SQL 案例。
4. **与 RAGFlow 同步**：通过 RAGFlow 提供的向量检索能力，为后续 Few-Shot 注入打下基础。

## Goals / Non-Goals

**Goals:**
- **反馈闭环**：实现统一的 `/api/chat/feedback` 接口。
- **经验沉淀**：自动从 ChatBI Trace 中提取 `status='success'` 且 `step_number` 最大的 SQL。
- **后台管控**：在「智能体管理 -> ChatBI 经验库」中提供审核与 RAG 同步功能。
- **动态配置**：通过系统配置 `chatbi_sample_knowledge_base` 关联 RAGFlow 数据集。
- **权限隔离**：所有管理端操作受 RBAC 权限点控制。

**Non-Goals:**
- **第一阶段不实现**：Few-Shot 注入逻辑（即：如何从 RAGFlow 检索并拼接 Prompt）。
- **第一阶段不实现**：经验库的定期自动回归测试。

## Decisions

### 1. 数据库与表结构 (Consolidated DDL)
**Rationale**: 为了简化维护，所有相关变更合并为 `V50-init_chatbi_feedback_system.sql`。
- **`ai_chatbi_examples`**: 存储核心 SQL 经验，状态机包含 `pending/approved/rejected/deprecated`。
- **`ai_agent_execution_history`**: 增加 `feedback` 字段以支持全局看板。
- **权限资源**: 注册菜单「ChatBI 经验库」及 `list/audit/sync/delete` 权限。

### 2. 经验同步机制 (ExampleService)
- **提取逻辑**：在反馈点赞时，异步触发 `ExampleService.create_from_feedback`。
- **格式化**：将经验存储为 Markdown 格式（用户问题、SQL、AI 回答），以便 RAGFlow 更好地切分与向量化。
- **错误重试**：如果同步 RAGFlow 失败，在后台记录 `rag_sync_error`，支持管理员手动重试。

### 3. 配置管理 (System Config)
- **位置**：新增 `chatbi_sample_knowledge_base` 到「智能体设置 (AI Agent)」分组。
- **UI**：前端 `SystemConfig.vue` 将识别该 key，并调用现有 RAGFlow 数据集列表接口进行渲染。

### 4. 权限与隔离 (RBAC)
- 菜单挂载在「智能体管理」下。
- 只有拥有 `chatbi:example:audit` 权限的用户才能改变经验状态。

## Risks / Trade-offs

- **[Risk] Trace 数据不完整** → **[Mitigation]** 提取 SQL 时严格检查 `tool_name='execute_sql_query'` 和 `status='success'`。如果找不到有效 SQL，则不创建经验记录。
- **[Risk] RAGFlow 接口超时** → **[Mitigation]** 同步过程采用异步任务处理，不阻塞前端反馈响应。
- **[Risk] 敏感信息泄露** → **[Mitigation]** 经验库条目严格关联 `dataset_id`。

## Migration Plan

1. 执行 `db-prod/V50-init_chatbi_feedback_system.sql`。
2. 重启后端服务。
3. 在管理后台「系统设置 -> 智能体设置」中，选择用于存放经验的 RAGFlow 数据集。
