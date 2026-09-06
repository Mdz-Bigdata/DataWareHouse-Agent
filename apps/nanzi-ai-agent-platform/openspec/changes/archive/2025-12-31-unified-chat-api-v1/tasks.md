# 任务：统一对话接口 V1 实施步骤

- [x] **框架搭建**
  - [x] 实现 `app/api/v1/api.py` 路由聚合器。
  - [x] 在 `app/main.py` 挂载 `/api/v1` 根路由。
- [x] **中枢逻辑开发**
  - [x] 创建 `app/services/ai/agent_service.py` 处理分发。
  - [x] 实现基础意图的分支处理器 (Handlers)。
- [x] **接口实现**
  - [x] 实现 `POST /api/v1/chat/completions`。
  - [x] 支持基本的会话上下文（取出 `messages` 列表中的最后一条作为 Query）。
- [x] **验证交付**
  - [x] 通过 API 工具模拟外部调用。
  - [x] 录制演示视频/截图展示不同意图的自动路由效果。
