# Implementation Plan: OpenClaw Engine Integration

## Phase 1: 后端实现 (Backend Implementation)
- [x] **Task 1: 更新 `app/services/ai/openclaw_client.py`**
    - 确保 `chat_stream` 方法支持传入 `user` (当前用户名)。
    - 将 `base_url`, `api_key` 和 `model` (BOT ID) 改为从 `config` 参数中动态读取。
- [x] **Task 2: 更新 `app/services/ai/executors/openclaw_executor.py`**
    - 捕获当前用户信息并传递给 Client。
    - 优化 UI 日志（“小龙虾正在思考”等）。
- [x] **Task 3: 完善 `app/services/ai/dispatcher.py`**
    - 确保在分发给 `OpenClawExecutor` 时，正确传递 `engine_config` 和上下文。

## Phase 2: 前端实现 (Frontend Implementation)
- [x] **Task 4: 修改 `frontend/src/` 中的智能体配置表单 (AgentForm)**
    - 新增 `Execution Engine` 中的 `OPENCLAW` 选项。
    - 增加动态表单字段：`base_url`, `api_key`, `model` (BOT ID)。
    - 绑定到 `engine_config` 数据。
- [x] **Task 5: UI 测试与验证**
    - 在前端创建 OpenClaw 智能体，确认配置能够正确保存到后端。
    - 进行对话测试，验证流式输出和用户信息透传。

## Phase 3: 验证 (Verification)
- [x] **Task 6: 编写单元/集成测试**
    - 编写 Mock OpenClaw 接口的测试用例。
    - 验证 `AgentDispatcher` 的正确路由。
