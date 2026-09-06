# 提案：调试窗口升级 (Agent Debug UI Upgrade)

## 背景 (Context)
后端已完成历史记录增强（版本追踪、持久化），但 `AgentDebug` 页面目前仅支持单次调试或通过 Trace ID 手动回溯。用户无法直观地浏览和对比该 Agent 的历史对话记录。

## 目标 (Goals)
在 `AgentDebug` 页面引入侧边栏，支持直接查看、搜索和加载该智能体的历史对话记录。

## 变更内容 (What Changes)
1.  **UI 布局**: `AgentDebug.vue` 改为左右分栏布局（左侧历史列表，右侧对话窗口）。
2.  **历史列表**: 左侧展示历史会话列表，包含：时间、用户提问摘要、Agent 版本、状态（成功/失败）。
3.  **交互**: 点击左侧列表项，右侧自动加载对应的历史对话详情（复用现有的 Trace 回溯逻辑）。
4.  **过滤**: 支持按 Agent ID 自动过滤当前调试的 Agent 历史。

## 影响 (Impact)
- **Frontend**: `frontend/src/views/AgentDebug.vue`
- **API**: 使用已实现的 `GET /api/v1/chat/history`。
