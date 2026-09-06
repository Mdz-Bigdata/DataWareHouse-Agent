# 规范：服务端对话记忆 (Conversation Memory)

## ADDED Requirements

### Requirement: 服务端历史存储 (Server-Side History Storage)
当请求中提供 `conversation_id` 时，系统必须 (MUST) 将对话历史存储在 Redis 中。

#### Scenario: 客户端发送带 conversation_id 的新消息
-   **Given** Redis 服务可用且已启用
-   **And** 客户端发送聊天请求，包含 `conversation_id="test-conv-1"`
-   **Then** 系统应将消息追加到 Redis 历史中
-   **And** 使用完整历史作为 Context 进行推理

### Requirement: 前端会话管理 (Frontend Session Management)
前端应用必须 (MUST) 生成并维护会话 ID，以利用服务端记忆功能。

#### Scenario: AgentDebug 页面初始化
-   **Given** 用户打开 Agent Debug 页面
-   **Then** 页面应自动生成一个新的 UUID 作为 `conversation_id`
-   **And** 后续的聊天请求都应携带该 ID

#### Scenario: 开启新会话
-   **Given** 用户点击“新会话”或“清空”按钮
-   **Then** 前端应生成一个新的 `conversation_id`
-   **And** 清空界面上的聊天记录
-   **And** 后端将识别为新的空白会话

### Requirement: 历史记录大小管理 (History Size Management)
系统必须 (MUST) 限制存储的对话历史大小。

#### Scenario: 滑动窗口
-   **Given** 历史记录超过配置的 `MAX_HISTORY_TURNS`
-   **Then** 系统应自动修剪旧消息

### Requirement: 向后兼容性 (Backward Compatibility)
系统必须 (MUST) 继续支持不带 `conversation_id` 的无状态请求。

#### Scenario: 客户端未发送 conversation_id
-   **Given** 客户端发送请求 `messages=[...]` 但**没有** `conversation_id`
-   **When** 系统处理该请求时
-   **Then** 系统应完全按照请求中的 `messages` 列表进行处理 (无状态模式)
-   **And** 不应与 Redis 历史存储进行任何交互
