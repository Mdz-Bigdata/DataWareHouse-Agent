# Implementation Plan - Fix Slash Command Visibility and Add Button

## Why
用户报告在 `EmbedChat` 中无法看到快捷指令的“新增”按钮。经调查发现，`currentUser` 和 `accountInfo` 未正确从后端获取并赋值，导致依赖这些变量的 UI 元素（如新增按钮和删除按钮）不显示。此外，快捷指令容器的 `v-if` 条件可能导致在某些边缘情况下容器隐藏。

## How
1.  **更新 `validateToken`**：在验证 API Key 成功后，解析响应中的用户信息，并赋值给 `currentUser` 和 `accountInfo`。
2.  **移除不必要的 `v-if`**：从快捷指令容器中移除 `v-if="slashCommands.length > 0"`，确保容器始终显示（在大屏幕上）。
3.  **统一用户信息**：确保 `currentUser` 和 `accountInfo` 状态同步。

## Rationale
- `validateToken` 调用的 `/api/portal/auth/user_apikey` 接口已经返回了用户信息，直接利用该数据可以避免额外的 API 调用。
- 移除容器的 `v-if` 保证了即使在列表加载前或为空时，UI 布局也保持稳定，且“新增”按钮始终可见。
