## MODIFIED Requirements

### Requirement: 资源权限类型扩展
> 系统权限模型必须 (MUST) 支持 `api` 类型的资源，用于控制对 V1 外部接口的访问。

#### Scenario: 分配 API 访问权限
- **Given** 管理员在“权限管理”界面。
- **When** 选择“接口权限” (API Resources) 标签页。
- **Then** 系统展示所有可用的 V1 接口列表（动态获取，非硬编码）。
- **When** 管理员勾选 `POST /api/v1/chat/completions` 并保存。
- **Then** 该用户/角色获得调用该接口的权限。

### Requirement: API 运行时鉴权
> V1 接口必须 (MUST) 在执行业务逻辑前校验调用者是否拥有该接口的访问权限。

#### Scenario: 无权限调用 API
- **Given** 用户 A 没有 `POST /api/v1/chat/completions` 的权限。
- **When** 用户 A 使用合法的 API Key 请求该接口。
- **Then** 接口返回 `403 Forbidden`。
- **And** 错误信息提示“无权访问该接口”。

#### Scenario: 有权限调用 API
- **Given** 用户 B 拥有该接口权限。
- **When** 用户 B 请求该接口。
- **Then** 请求被正常处理。
