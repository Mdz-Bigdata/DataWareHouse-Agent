# Tasks: Agent Orchestration Implementation

## Phase 1: Meta-data & Infrastructure
- [x] 数据库变更：给 `ai_agents` 表增加 `description` 字段。 <!-- id: 1 -->
- [x] 模型同步：更新 `AIAgent` 模型和 Pydantic 模式。 <!-- id: 2 -->
- [x] UI 适配：在管理后台增加智能体描述的编辑功能。 <!-- id: 3 -->

## Phase 2: Router Development
- [x] 实现 `RouterService` 类，支持基于 LLM 的智能体匹配 (Zero-shot Strategy)。 <!-- id: 4 -->
- [x] 在 `AgentManagerService` 中增加路由缓存逻辑 (Skipped for LLM Strategy)。 <!-- id: 5 -->
- [x] 实现 `LLMRouter` 作为编排方案。 <!-- id: 6 -->

## Phase 3: Service Refactoring
- [x] 创建 `RouterService` 统一管理多 Agent 调度 (API Integrated). <!-- id: 7 -->
- [x] 修改 `AgentService`，使其支持在执行过程中作为子任务被调用 (Router calls service via ID flow). <!-- id: 8 -->
- [x] 处理跨 Agent 调用的上下文衔接（Session Flow - Basic ID passing）. <!-- id: 9 -->

## Phase 4: Verification
- [x] 编写测试用例验证路由准确率。 <!-- id: 10 -->
- [x] 在 Agent Chat 界面展示当前活跃的子 Agent (Frontend - AgentDebug.vue). <!-- id: 11 -->
