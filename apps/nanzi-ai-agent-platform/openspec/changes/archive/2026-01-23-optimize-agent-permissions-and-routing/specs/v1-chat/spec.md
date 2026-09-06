# v1-chat Spec Delta

## ADDED Requirements

### Requirement: 路由模式 (Routing Modes)
系统应支持用户在“自动路由”和“专家模式”之间切换。

**Scenario: 切换到专家模式**
- **WHEN** 用户在设置中选择“专家模式”并指定了一个“默认专家”（Default Expert Agent）。
- **THEN** 聊天界面输入框上方显示提示条：“当前为专家模式: [专家名称]”。
- **AND** 发送的所有消息（除非使用 `@` 覆盖）均直接路由给该专家，跳过 Router 分析。

**Scenario: 切换到自动模式**
- **WHEN** 用户在设置中选择“自动模式”。
- **THEN** 隐藏专家模式提示条。
- **AND** 发送的消息由 Router 分析并分发。

### Requirement: @提及列表过滤 (Mention List Filtering)
**Scenario: 触发 @ 列表**
- **WHEN** 用户在输入框输入 `@`
- **THEN** 显示的智能体列表应为以下集合的并集：
    1.  用户有权限的系统级智能体 (`is_system=True` + Perm)。
    2.  用户自己创建且启用的非系统级智能体 (`is_system=False` + Owner + Enabled)。
