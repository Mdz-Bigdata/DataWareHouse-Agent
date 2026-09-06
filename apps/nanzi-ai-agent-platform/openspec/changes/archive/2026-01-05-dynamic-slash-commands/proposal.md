# Proposal: Dynamic Slash Commands (动态快捷指令)

## Why
目前 `AgentDebug` 页面中的快捷指令（Quick Commands）是硬编码在前端代码中的。
用户希望能够动态添加、删除和修改这些指令，以便根据测试需求灵活调整常用的测试用例。

## What Changes
1.  **Database**: 新增 `slash_commands` 表，存储指令的显示文本 (`label`) 和实际执行内容 (`command`)。
2.  **Backend**: 新增 CRUD 接口 `slash_commands`。
3.  **Frontend**: 
    - `AgentDebug.vue` 移除硬编码，改为从后端加载。
    - `AgentDebug.vue` 新增“管理指令”的弹窗，允许管理员（或用户）增删改指令。

## Impact
- **Database**: 新增 `slash_commands` 表。
- **Backend**: 新增 API 路由。
- **Frontend**: `AgentDebug.vue` 逻辑更新。

## Affected Specs
- `specs/slash-commands`
