# 提案：嵌入式 Web 组件增强版 (Proposal: Embedded Web Widget with Enhanced Communication)

## 目标 (Goal)
为第三方业务系统提供一种低成本、安全的集成方案，允许通过 IFrame 嵌入智能体聊天窗口。支持 `postMessage` 安全通信协议，实现 Token 传递、窗口动态缩放控制、业务上下文注入、快捷指令操作以及企业级健壮性保障。

## 动机 (Motivation)
- **降低集成成本**：第三方无需重复开发聊天界面，直接嵌入即可使用。
- **安全性**：通过 postMessage 传递 Token，避免 URL 参数泄露敏感信息。
- **交互体验**：支持从 IFrame 内部控制宿主页面的窗口大小（如展开/折叠），提供类似原生 Widget 的体验。
- **业务感知**：允许宿主系统注入当前业务上下文（如用户名、部门、当前查看的资源ID），使智能体能主动感知用户环境。
- **功能完备**：支持快捷指令和自定义主题，满足不同业务线的个性化需求。

## 核心特性 (Key Features)

1. **轻量级嵌入页**：
   - 提供 `/embed/chat` 专用路由。
   - 移除所有管理后台的 UI 装饰（侧边栏、Header），仅保留聊天主界面。
   - 响应式设计，完美适配移动端与桌面端小窗口。

2. **双向通信协议 (postMessage)**：
   - **Host -> Widget**: 发送初始化配置 (Token, Theme)、注入上下文 (Context)、发送指令 (Clear, Reload)。
   - **Widget -> Host**: 请求调整窗口大小 (Resize/Expand/Collapse)、通知状态变更 (Ready, Error)。

3. **上下文注入 (Context Injection)**：
   - 支持传递结构化数据（User Info, Page Context），自动附加到对话的 System Prompt 或 Metadata 中。

4. **功能完整性 (Slash Commands)**：
   - 保留 Agent Debug 中的快捷指令功能（如 `/clear`, `/help`），确保用户在嵌入页也能快速操作。

5. **高级主题定制 (Advanced Theming)**：
   - 支持预置主题（`light`, `dark` 等）。
   - 支持通过 `postMessage` 传递自定义样式变量（如 `--primary-color`, `--font-family`），与宿主系统无缝融合。

6. **企业级健壮性 (Robustness)**：
   - **自动重连**：支持网络波动或长时间闲置后的静默重连，保证会话连续性。
   - **会话重置**：支持宿主发送 `RESET_SESSION` 指令，用于用户登出或切换上下文时彻底清除敏感数据。
   - **多实例隔离**：基于 `iframe` 天然隔离，但在协议层支持 `instance_id` 以防未来多开场景下的消息混淆。

7. **集成规范**：
   - 提供 CSS `z-index` 及布局最佳实践文档，防止样式冲突。

## 成功指标 (Success Metrics)
- 第三方系统可通过 `<iframe src="...">` 成功加载无 Chrome 的聊天界面。
- 能够通过 JS SDK 或原生 postMessage 成功唤起聊天并传递 Token。
- 聊天窗口在移动端和 PC 端缩放表现正常，无样式溢出，主题色可定制。
- 智能体能读取并利用注入的 `context` 信息回答问题。
- 网络断开时能自动重连，且支持多实例互不干扰。
