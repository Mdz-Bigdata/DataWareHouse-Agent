## ADDED Requirements

### Requirement: 用户画像查询接口
> 系统必须 (MUST) 提供查询用户详情（包含权限信息）的 V1 接口。

#### Scenario: 根据用户名获取画像
- **Given** 已认证的客户端（拥有 `sys_user_read` 权限或 Admin 角色）。
- **When** 发起 `GET /api/v1/users/profile?username=target_user` 请求。
- **Then** 返回 `target_user` 的详细信息。
- **And** 响应包含该用户的：
  - 基本信息 (ID, Username, Display Name)
  - 角色列表 (Roles)
  - 资源权限列表 (Permissions: Agents, Datasets, APIs)

#### Scenario: 接口鉴权
- **Given** 未经授权的客户端。
- **When** 尝试访问 `/api/v1/users/profile`。
- **Then** 返回 `401 Unauthorized` 或 `403 Forbidden`。
