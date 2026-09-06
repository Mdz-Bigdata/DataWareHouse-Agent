# 任务清单：RAGFlow 集成 (Tasks: RAGFlow Integration)

## 1. 准备工作 (Preparation)
- [x] 创建 SQL 迁移脚本 `V21-add_ragflow_agent_fields.sql` <!-- id: 1 -->
- [x] 执行数据库迁移并验证字段 <!-- id: 2 -->

## 2. 后端开发 (Backend)
- [x] 实现 `RagFlowClient` 适配器 (支持流式 API) <!-- id: 3 -->
- [x] 实现 `KnowledgeSearchTool` 并注册到工具库 <!-- id: 4 -->
- [x] 修改 `Agent` 相关的 Schema 和 ORM 模型以支持新字段 <!-- id: 5 -->
- [x] 在 `ChatService/AgentExecutor` 中实现 `RAGFLOW` 引擎分发逻辑 <!-- id: 6 -->
- [x] 更新 `RouterService` 的意图识别逻辑 (Prompt 增强) <!-- id: 7 -->

## 3. 前端开发 (Frontend)
- [x] 更新 `Agent` 管理界面，支持引擎类型选择和配置 <!-- id: 8 -->
- [x] 在 `AgentDebug.vue` 中实现 RAG 引用来源 (Citations) 的渲染逻辑 <!-- id: 9 -->
- [x] 优化消息气泡样式，支持展示来源卡片 <!-- id: 10 -->

## 4. 测试与验证 (Testing)
- [x] 编写 `KnowledgeSearchTool` 的单元测试 <!-- id: 11 -->
- [x] 编写双模集成的集成测试 <!-- id: 12 -->
- [x] 验证 RAGFlow 宕机时的降级/报错处理 <!-- id: 13 -->
- [x] 更新 `tests/CHECKLIST.md` <!-- id: 14 -->