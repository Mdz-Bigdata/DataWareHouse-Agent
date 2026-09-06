# Spec: 功能按钮与操作点权限控制

## ADDED Requirements

### 1. 功能点权限定义
系统 **MUST** 支持 `resource_type='element'` 的资源定义，用于标识页面内的特定操作。

### 2. 按钮显隐控制指令
前端 **MUST** 提供一个全局指令（如 `v-has-perm`），用于基于权限集控制 DOM 元素的挂载状态。
#### Scenario: 敏感操作按钮控制
- **GIVEN** 管理员为“运营角色”分配了 `element:agent:debug` 但未分配 `element:agent:delete`
- **WHEN** 该角色下的用户进入智能体管理页面
- **THEN** “调试”按钮正常显示
- **AND** “删除”按钮在 DOM 中被移除（或置灰禁用）

### 3. API 请求鉴权（保底）
对于标记为受控的功能点，后端接口 **SHOULD** 在执行敏感操作前再次校验对应的 `element` 权限。
