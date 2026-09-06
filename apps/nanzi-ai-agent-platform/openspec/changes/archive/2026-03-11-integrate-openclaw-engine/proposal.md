## Why

扩展系统的引擎架构，接入 OpenClaw (小龙虾) 外部引擎。这允许用户配置能够调用外部 OpenClaw 自动化工作流或模型的智能体，从而增强系统在复杂任务编排和执行方面的能力。

## What Changes

- **前端 UI (智能体配置页)**:
    - 在“执行引擎 (Execution Engine)”单选框中新增 **OpenClaw (自动化引擎)** 选项。
    - 当选中 OpenClaw 时，显示以下配置字段：
        - **OpenClaw 地址 (Base URL)**: 外部 OpenClaw 服务的 API 基础地址。
        - **API 密钥 (API Key)**: 用于鉴权的 Token。
        - **机器人/BOT ID (Model)**: 对应 OpenClaw 接口中的 `model` 参数。
- **后端服务**:
    - **Schema 更新**: 确保 `ChatConfig` 或 `engine_config` 能够持久化存储 OpenClaw 的特定参数（URL, Key, Bot ID）。
    - **Dispatcher 路由**: 完善 `AgentDispatcher`，支持根据引擎类型分发至 `OpenClawExecutor`。
    - **Executor 实现**: 完善 `OpenClawExecutor`，使其能够从智能体配置中动态读取 API 参数，而非硬编码。
- **客户端集成**:
    - 完善 `OpenClawClient`，支持基于配置的鉴权和流式/异步请求。

## Capabilities

### New Capabilities
- `openclaw-engine-integration`: 支持 OpenClaw 引擎的注册、UI 配置以及在对话流中的执行。

### Modified Capabilities
- `agent-management`: 需要修改智能体管理的 API 和 UI，以支持新的引擎配置字段。

## Impact

- **Frontend**: `frontend/src/` 下的智能体配置组件需要新增表单逻辑。
- **Backend API**: `app/schemas/agent.py` 和相关服务层。
- **Executors**: `app/services/ai/executors/openclaw_executor.py`。
- **Config**: 可能涉及 `ConfigService` 用于设置系统默认的 OpenClaw 参数。
