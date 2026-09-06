# Spec Delta: 用户认证扩展 (SSO)

## MODIFIED Requirements

### 增加 SSO 认证支持
系统应支持通过 Yovole 统一认证系统进行身份验证。

#### Scenario: 成功通过 SSO 登录
- **Given** 用户拥有有效的 Yovole SSO 凭据
- **And** 该用户在本地系统 `ai_agent_users` 中已存在且状态为“启用”
- **When** 用户调用 `/api/portal/auth/sso/login` 接口并提供正确的凭据
- **Then** 系统应返回 HTTP 200
- **And** 响应中应包含用户的本地权限信息和 API Key
- **And** 系统应设置认证 Cookie (`admin_token`)

#### Scenario: SSO 认证成功但本地无账号
- **Given** 用户通过了 SSO 身份验证
- **And** 本地数据库中不存在匹配该 `user_name` 的记录
- **When** 用户尝试 SSO 登录
- **Then** 系统应返回 HTTP 401
- **And** 错误信息应提示“请联系管理员开通系统权限”

#### Scenario: 用户账号被禁用
- **Given** 用户通过了 SSO 身份验证
- **And** 本地数据库中该用户状态为 `0` (禁用)
- **When** 用户尝试 SSO 登录
- **Then** 系统应返回 HTTP 401
- **And** 错误信息应提示“账户已被禁用”
