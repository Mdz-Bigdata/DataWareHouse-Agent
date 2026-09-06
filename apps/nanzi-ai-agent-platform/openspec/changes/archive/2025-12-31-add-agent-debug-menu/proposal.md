# Proposal: Add Agent Debugging Menu

## Goal
在 Dashboard 中添加“智能体调试”菜单，提供一个专门的界面用于测试和调试智能体（Agent）功能。

## Context
当前系统缺乏一个直观的界面来调试正在开发的智能体功能。用户希望通过一个独立的菜单项进入调试页面，以便进行测试。
目前已有 `API 调试` (Playground)，新的“智能体调试”将专注于 Agent 交互和流程验证。

## Solution
1. **Frontend**:
   - 在 `Dashboard.vue` 的侧边栏导航中添加“智能体调试”菜单项。
   - 创建新的视图组件 `src/views/AgentDebug.vue`。
   - 在 `src/router/index.ts` 中配置路由 `/dashboard/agent-debug`。

## Risks
- 无明显风险，纯前端页面新增。
