# 提案：增强对话历史功能（版本号、过滤与分页）

## 背景
目前的“对话历史”（执行历史）缺乏追踪 Agent 版本的能力。同时，缺乏一个用于列表展示、过滤和分页的 API，导致前端难以构建完善的历史记录视图。

## 问题
1. **缺失版本追踪**：当 Agent 配置（Prompt/模型/工具）更新后，历史记录无法得知是哪个版本生成的回答。
2. **缺乏查询接口**：目前没有统一的接口来获取过去的会话列表。
3. **不支持搜索过滤**：用户无法根据关键词、日期或特定 Agent 搜索历史。

## 解决方案
1. **数据库更新**：在 `ai_agent_execution_history` 表中添加 `agent_version` (string) 字段。
2. **接口开发**：新增 `GET /api/v1/chat/history` 接口，支持：
    - 分页 (`page`, `page_size`)
    - 过滤 (`agent_id`, `keyword`, `start_date`, `end_date`)
    - 响应结果包含 `agent_version`。
3. **内部逻辑**：更新 `AgentService` 和 `AuditManager`，在执行任务时捕获并持久化 Agent 版本号。

## 风险
- **数据迁移**：旧的历史记录 `agent_version` 将显示为 NULL。
- **性能**：关键词搜索目前使用 `LIKE` 匹配，在数据量极大时可能需要考虑全文索引，但在中等规模下目前方案可行。