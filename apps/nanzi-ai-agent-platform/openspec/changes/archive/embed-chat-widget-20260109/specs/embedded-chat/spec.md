# 嵌入式聊天组件 (Embedded Chat Interface)

## ADDED Requirements

### Requirement: 沉浸式嵌入聊天页 (Interactive Embedded Chat View)
系统 MUST 提供一个专用的路由 `/embed/chat`，用于渲染适合 iframe 嵌入的极简全屏聊天界面。该界面 MUST NOT 包含侧边栏、顶部导航或调试面板等管理端元素。

#### Scenario: 渲染嵌入页
- **Given** 我是访问 `/embed/chat` URL 的用户
- **Then** 我应该看到一个去除侧边栏、无 Header 的纯净聊天界面
- **And** 布局应占满 100% 的视口宽高。

### Requirement: 灵活的鉴权方式 (Flexible Authentication)
系统 MUST 支持通过 URL 参数和 postMessage 初始化两种方式进行鉴权，以适应不同的嵌入场景。

#### Scenario: URL 参数传 Token
- **Given** 我访问 `/embed/chat?token=valid_token&agent_id=my-agent`
- **Then** 聊天窗口应自动使用提供的 Token 完成鉴权
- **And** 连接到指定的 `agent_id`。

#### Scenario: PostMessage 初始化
- **Given** 嵌入页已加载完毕
- **When** 父窗口发送 `postMessage`，内容为 `{ type: 'INIT_CONFIG', token: '...', agent_id: '...' }`
- **Then** 聊天组件应使用这些参数进行鉴权并建立会话。

### Requirement: 上下文感知 (Context Awareness)
系统 MUST 允许宿主页面向聊天会话注入业务上下文（如用户部门、当前资源 ID），并将其传递给后端用于 Prompt 工程。

#### Scenario: 注入业务上下文
- **Given** 聊天会话已激活
- **When** 父窗口发送 `{ type: 'UPDATE_CONTEXT', payload: { user_dept: 'IT' } }`
- **Then** 用户后续发送的消息 payload 中应包含此上下文信息。

### Requirement: 组件交互通信 (Widget Interaction)
系统 MUST 提供机制，使嵌入组件能通过 postMessage 向宿主窗口发送 UI 状态变更请求（如调整大小）。

#### Scenario: 请求调整窗口大小
- **Given** 聊天组件处于最小化或初始状态
- **When** 内部逻辑判断需要展开（例如用户点击了开关按钮）
- **Then** 组件应向父窗口发送 `{ type: 'REQUEST_RESIZE', ... }` 消息。

### Requirement: 快捷指令支持 (Slash Commands Availability)
嵌入式聊天界面 MUST 支持快捷指令（如 `/clear`, `/help`），且体验需与主调试界面保持一致。指令菜单 MUST 支持通过输入框输入 `/` 触发，并支持键盘或触摸选择。

#### Scenario: 使用快捷指令
- **Given** 我正在嵌入式聊天输入框中输入
- **When** 我输入 `/` 字符
- **Then** 输入框上方应根据上下文弹出可用指令菜单
- **And** 我可以选择一个指令立即执行。

### Requirement: 高级主题定制 (Advanced Theming Support)
系统 MUST 允许宿主自定义聊天组件的外观。这包括选择预置主题（如 light, dark）以及注入自定义 CSS 变量（如主色调、字体）进行精细控制。

#### Scenario: 注入自定义主题
- **Given** 父窗口正在初始化组件
- **When** 父窗口发送 `INIT_CONFIG` 消息，包含 `{ theme: 'custom', styleVars: { '--primary-color': '#ff0000' } }`
- **Then** 聊天组件的按钮和强调色应变为自定义的红色。

### Requirement: 网络健壮性 (Network Resilience)
当网络连接丢失时，聊天组件 MUST 自动尝试重连，并 SHOULD 向用户（及通过 postMessage 向宿主）通知连接状态。

#### Scenario: 自动重连
- **Given** 聊天正在进行中，但网络连接断开
- **Then** 组件应进入“重连中”状态，并定期尝试重新建立连接
- **And** 组件应向宿主发送 `CONNECTION_STATUS` 消息，状态为 `reconnecting`。

### Requirement: 会话控制 (Session Control)
系统 MUST 允许宿主通过编程方式重置聊天会话，清除所有历史记录和上下文，以支持用户切换或新流程。

#### Scenario: 重置会话
- **Given** 一个包含历史记录的活跃会话
- **When** 宿主发送 `RESET_SESSION` 消息
- **Then** 组件应清空对话历史，并将上下文重置为初始状态。

### Requirement: 实例隔离 (Instance Isolation)
为了支持在同一个宿主页面嵌入多个组件实例，系统 MUST 支持在所有 `postMessage` 通信中携带可选的 `instance_id` 参数，确保消息路由正确。

#### Scenario: 多实例路由
- **Given** 页面嵌入了两个聊天组件，分别指定 `instance_id=A` 和 `instance_id=B`
- **When** 宿主发送一条带有 `instance_id: 'A'` 的消息
- **Then** 只有组件 A 应处理该消息，组件 B 应忽略。
