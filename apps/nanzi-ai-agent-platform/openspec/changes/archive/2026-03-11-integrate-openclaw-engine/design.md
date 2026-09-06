# Design: Integrate OpenClaw Engine

## 1. 概述
本设计旨在将 OpenClaw (小龙虾) 自动化引擎集成到 AI Agent 平台中。OpenClaw 将作为一种新的执行引擎类型 (`engine_type: "OPENCLAW"`)，通过类 OpenAI 的 SSE 接口提供流式对话能力。

## 2. 数据模型变更

### 2.1 Engine Config 结构
OpenClaw 的配置将存储在 `AIAgent.engine_config` 字段中，具体格式如下：
```json
{
  "base_url": "https://api.openclaw.example.com",
  "api_key": "sk-xxxxxx",
  "model": "bot-123",
  "extra_params": {}
}
```

### 2.2 API 请求体
向 OpenClaw 发送的 POST 请求将包含以下字段：
- `model`: 对应配置中的 `model` (BOT ID)。
- `messages`: 标准对话历史。
- `stream`: 始终为 `true`。
- `user`: **当前登录用户的用户名 (Critical)**。
- `conversation_id`: 对应系统的 `trace_id`。

## 3. 后端架构设计

### 3.1 AgentDispatcher
- **逻辑**: 识别 `agent_config.engine_type == 'OPENCLAW'`。
- **操作**: 实例化 `OpenClawExecutor`。需要确保 Dispatcher 能够获取当前用户信息并传递给 Executor。

### 3.2 OpenClawExecutor & Client
- **OpenClawClient**:
    - 实现 `chat_stream` 方法。
    - 负责 SSE 数据的解析。
    - 负责 `snake_case` (服务端) 到 `camelCase` (前端) 的潜在字段转换（如果涉及元数据）。
- **OpenClawExecutor**:
    - 封装 UI 日志输出（如“小龙虾正在思考...”）。
    - 调用 Client 获取流，并将结果 yield 给前端。

## 4. 前端 UI 设计

### 4.1 智能体配置页面
- **位置**: `frontend/src/components/AgentForm.tsx` (或相应组件)。
- **交互**:
    - 当 `Execution Engine` 选中 `OpenClaw` 时，切换显示专用的配置面板。
    - 提供 `Base URL` (Input), `API Key` (Password/Input), `BOT ID` (Input) 三个核心字段。
    - 实时校验必填项。

## 5. 安全与审计
- **鉴权**: 所有的 API Key 仅在后端读取并透传，不暴露给前端浏览器（除非在配置编辑状态下）。
- **用户识别**: 通过 `user` 参数确保 OpenClaw 侧可以记录操作者身份。

## 6. 异常处理
- **网络超时**: 设置合理的 HTTP 超时时间（如 60s）。
- **接口错误**: 如果 OpenClaw 返回非 200 状态码，Executor 需捕获并输出友好的错误日志给前端 UI。
