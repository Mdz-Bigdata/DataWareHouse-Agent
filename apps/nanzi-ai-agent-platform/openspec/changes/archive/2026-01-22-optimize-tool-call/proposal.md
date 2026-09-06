# Proposal: Optimize Tool Call & Editor Experience

## Why
用户需要更高效的智能体开发体验（Markdown 编辑器、宽弹框）以及更稳定的后端执行逻辑（JQL 修复、花括号转义修复）。

## What Changes
1.  **Frontend**:
    -   新增 `MarkdownEditor.vue` 组件，支持编辑/预览切换。
    -   升级 `AgentManagement.vue` 版本编辑弹框，增加宽度至 `7xl`。
    -   优化 `TraceLogViewer.vue` 和 `MessageRenderer.vue`。
2.  **Backend**:
    -   修复 `DataQueryExecutor` 中的花括号转义 Bug。
    -   修复 `DataQueryExecutor` 默认参数陷阱。
3.  **Prompt**:
    -   更新 `system_prompt_V3.md`，增强身份感知、修复 JQL 状态错误、强制可视化输出。
