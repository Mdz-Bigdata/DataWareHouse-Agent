# Functional Specifications: OpenClaw Integration

## 1. 前端配置项 (UI Spec)
- **Engine Type Switch**: 在 `AgentForm` 中新增 `OPENCLAW` 类型。
- **Conditional Fields**: 选中 `OPENCLAW` 时，显示以下配置（由 `engine_config` 管理）：
    - `Base URL`: 对应 `engine_config.base_url` (Required, String, 提示：https://api.openclaw.example.com)
    - `API Key`: 对应 `engine_config.api_key` (Required, Password, 建议：后端存储时不修改，前端仅在输入时展示)
    - `BOT ID`: 对应 `engine_config.model` (Required, String, 提示：OpenClaw 机器人 ID)

## 2. 后端执行流程 (Execution Spec)
- **Dispatcher Logic**:
    - 获取当前用户的 `username`。
    - 将 `AIAgent.engine_config` 透传给 `OpenClawExecutor`。
- **API Payload**:
    ```json
    {
      "model": "bot_id_from_config",
      "messages": [ ...history... ],
      "stream": true,
      "user": "current_username",
      "conversation_id": "trace_id_from_platform"
    }
    ```
- **SSE Parsing**:
    - 兼容 OpenAI 格式：解析 `data: {"choices": [{"delta": {"content": "..."}}]}`。
    - 错误处理：如果 OpenClaw 返回包含 `error` 字段的 JSON，需转换为平台日志输出。

## 3. 数据一致性 (Data Consistency)
- **Schema Validation**: `ChatConfig` 需支持 `engine_config` 的动态解析。
- **Database Migration**: 无需修改表结构，利用现有的 JSONB `engine_config` 字段。
