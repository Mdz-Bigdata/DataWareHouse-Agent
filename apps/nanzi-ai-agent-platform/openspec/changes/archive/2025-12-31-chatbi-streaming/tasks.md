# 任务：ChatBI 与流式输出实施步骤

- [x] **ChatBI 工具开发**
  - [x] 实现 `app/services/ai/tools/data_api.py` 封装。
  - [x] 编写核心 ChatBI Prompt 提示词。
- [x] **支持流式的 AgentService**
  - [x] 重构 `AgentService` 以支持异步生成器 (`astream`)。
  - [x] 确保 `GENERAL` 意图在流式模式下正常吐字。
- [x] **接口与骨架**
  - [x] 升级 `create_chat_completion` 支持 `StreamingResponse`。
  - [x] 实现 SSE 消息封装工具函数。
- [x] **验证交付**
  - [x] 录制控制台/Postman 流式接收视频。
  - [x] 模拟自然语言查数并展示其调用工具的过程。
