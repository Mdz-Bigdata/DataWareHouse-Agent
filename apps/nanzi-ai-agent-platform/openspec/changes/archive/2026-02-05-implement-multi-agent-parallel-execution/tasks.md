# 任务列表：实现多智能体并行执行

## 准备工作
- [x] 验证当前的路由逻辑和日志流式传输是否正常。 <!-- id: 0 -->
- [x] 创建用于测试多意图场景的测试用例（例如：同时涉及数据查询和知识库检索）。 <!-- id: 1 -->

## 核心开发 (后端)

### 1. 路由服务升级
- [x] 修改 `app/schemas/agent.py` 中的 `RouteResult`，增加 `secondary_agents` (List[str]) 字段。 <!-- id: 2 -->
- [x] 基于 `architech/prompts/router/V5_router_devops_optimized.md` 创建 `V6_router_multi_agent.md`，增加多智能体输出指令。 <!-- id: 3 -->
- [x] 更新 `app/services/ai/router_service.py` 中的 `DEFAULT_SYSTEM_PROMPT` (同步 V6 内容) 和解析逻辑，处理 `secondary_agents`。 <!-- id: 4 -->
- [x] 更新 `RouterService.route_query` 方法，接收并处理 `enable_multi_agent` 参数。 <!-- id: 5 -->

### 2. 编排服务重构
- [x] 在 `app/services/ai/agent_service.py` 中抽象出 `_run_single_agent` 逻辑（已在 `chat_completion_stream` 中重构）。 <!-- id: 6 -->
- [x] 修改 `chat_completion_stream` 接口签名，增加 `enable_multi_agent` 参数。 <!-- id: 7 -->
- [x] 实现分支逻辑：检测到多智能体且开关开启时，使用 `asyncio.gather` 并行调用 `_run_single_agent`。 <!-- id: 8 -->
- [x] 实现日志聚合逻辑：确保多个 Agent 的日志流在 SSE 中能正确区分（例如通过 `title` 前缀）。 <!-- id: 9 -->

### 3. 结果聚合 (Synthesis)
- [x] 在 `AgentService` 中实现 `_synthesize_multi_agent_results` 方法。 <!-- id: 10 -->
- [x] 定义聚合 Prompt，将多个 Agent 的输出作为上下文输入给 LLM。 <!-- id: 11 -->
- [x] 将聚合后的流式响应返回给前端。 <!-- id: 12 -->

## 界面开发 (前端)

### 4. 调试与主界面适配
- [x] 修改 `frontend/src/views/AgentDebug.vue`，增加 `enable_multi_agent` 复选框，并透传给 API。 <!-- id: 13 -->
- [x] 修改 `frontend/src/views/EmbedChat.vue`，在设置/Debug 面板中增加多智能体开关。 <!-- id: 14 -->
- [x] (可选) 优化前端日志组件，支持解析并高亮日志中的 `[AgentName]` 前缀。 <!-- id: 15 -->

## 验证与自动化测试
- [x] **自动化测试**: 编写 `tests/ai/test_multi_agent_router.py`，验证 Router 在各种意图下的解析准确性。 <!-- id: 16 -->
- [x] **自动化测试**: 编写 `tests/ai/test_multi_agent_orchestrator.py`，模拟并行执行流程并验证日志聚合和 Synthesis 结果。 <!-- id: 17 -->
- [x] **回归测试**: 运行 `tests/api/v1/test_chat_completions.py`，确保单智能体流程未受影响。 <!-- id: 18 -->
- [ ] 手动测试：使用复合 Query 触发并行执行，检查前端日志是否交错显示，最终回答是否融合了多方信息。 <!-- id: 19 -->
- [x] 边界测试：测试其中一个 Agent 报错或超时的情况，确保不影响整体流程。 <!-- id: 20 -->
