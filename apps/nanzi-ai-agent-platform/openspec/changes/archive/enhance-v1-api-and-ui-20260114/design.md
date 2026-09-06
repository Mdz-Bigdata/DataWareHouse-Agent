# Design: 动态 API 权限与交互优化

## 1. 动态 V1 API 资源发现
为了避免硬编码 API 列表，系统将在运行时（或按需）扫描 FastAPI 的路由表。

### 机制
1.  **Source**: `app.main:app` (FastAPI instance).
2.  **Filter**: 筛选 path 以 `/api/v1/` 开头的所有路由。
3.  **Identifier**: 使用 `route.path` + `route.methods` 作为唯一标识 (e.g., `POST:/api/v1/chat/completions`).
4.  **Metadata**: 提取 docstring 或 summary 作为资源描述。

### 权限映射
数据库中 `ai_agent_resource_permissions` 表的 `resource_type` 为 `api`。
`resource_id` 存储格式为 `{method}:{path_template}`，例如 `POST:/api/v1/chat/completions`。

## 2. 用户画像接口鉴权
新接口 `GET /api/v1/users/profile` 需要特殊的鉴权逻辑。
- **Input**: `username` (query param) 或 implicit (current token owner).
- **Validation**:
  - 调用者必须拥有有效的 API Key。
  - 如果查询他人信息，调用者需具备 `admin` 角色或特定权限（本次暂定仅允许查询 Token 对应用户，即 `/profile` 不带参数，或带参数但在 Service 层校验）。
  - *修正*：需求描述为“根据账号名获取...”，这意味着是一个管理端或服务间调用的接口。我们将要求调用者具备 `sys_user_read` 权限或 Admin 角色。

## 3. Agent Management UI
### Toggle Switch
替换原有的“启用/禁用”文本徽章或按钮。
- **UI**: macOS/iOS 风格的绿色开关。
- **Action**: 点击直接调用 `PATCH /agents/{id}` 更新 `is_enabled`。

### EmbedChat Preview
- **Button**: "预览" (Preview) 图标按钮。
- **Action**: 打开新窗口/Tab，URL 格式：
  `/embed-chat?token={current_user_api_key}&agent_id={agent_id}&theme=light`
  - 注意：需要使用当前登录用户的 API Key（或临时生成的 Token）来通过鉴权。考虑到安全性，建议前端调用后端“获取临时预览 Token”接口，或直接使用当前 Session Token (如果 EmbedChat 支持 Cookie/Header 鉴权)。
  - *现状确认*：EmbedChat 目前支持 `token` URL 参数。我们将使用用户的 API Key 或 JWT。
