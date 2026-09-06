# user-authentication Specification

## Purpose
TBD - created by archiving change enhance-login-auth. Update Purpose after archive.
## Requirements
### Requirement: 密码登录支持 (Password Login Support)
系统必须支持用户名/密码认证方式 (MUST)。

#### 场景: 密码登录成功
前置条件: 用户已设置密码
当 用户输入正确的用户名和密码
那么 用户登录成功并重定向至 Dashboard。

#### 场景: 密码错误
前置条件: 用户存在
当 用户输入错误的密码
那么 显示错误信息 "用户名或密码错误"。

#### 场景: 未设置密码
前置条件: 用户尚未设置密码
当 用户尝试使用密码登录
那么 显示提示信息 "尚未设置密码，请先使用 API Key 登录并设置密码"。

### Requirement: 认证界面 (Authentication UI)
登录页面必须通过选项卡 (Tabs) 提供多种登录方式 (MUST)。

#### 场景: 切换选项卡
前置条件: 在登录页面
当 用户点击 "账号密码登录"
那么 显示用户名和密码输入框。
当 用户点击 "API Key 登录"
那么 显示 API Key 输入框。
当 用户点击 "YES 系统"
那么 显示 "敬请期待" 或预留状态占位符。

### Requirement: 密码管理 (Password Management)
用户必须能够自行管理密码 (MUST)。

#### 场景: 设置密码
前置条件: 用户已登录
当 用户进入 个人中心 -> 安全设置
那么 用户可以设置新密码。

#### 场景: 管理员设置密码
前置条件: 管理员用户具备用户编辑权限
当 管理员在用户管理界面为其他用户点击「设置密码」并提交新密码
那么 系统保存该用户的密码哈希，且 **不** 返回或展示明文/哈希密码。

#### 场景: 管理员不可查看密码
前置条件: 管理员用户
当 管理员在用户管理界面查看其他用户
那么 管理员 **无法** 查看其他用户的明文或哈希密码。

### Requirement: 用户上下文增强 (User Context Enrichment)
系统 **MUST** 从 `ai_agent_users` 表中提取并向智能体传递用户的业务维度信息（如 `dept_id`, `org_path`），作为权限校验的输入。

#### Scenario: 数据结构扩展
用户表 **MUST** 包含 `dept_id` (Integer) 和 `org_path` (String) 字段，分别存储用户所属部门 ID 和组织全路径。

#### Scenario: 维度提取逻辑
在每次请求开始时，系统 **MUST** 根据 `user_id` 预加载维度数据，并封装到 `AgentContext.user_dimensions` 中。

#### Scenario: 变量映射
系统 **MUST** 支持核心变量的动态注入，包括 `{user.id}`, `{user.dept_id}`, `{user.org_path}`, `{user.role}`。

#### Scenario: 管理员豁免
当用户角色为 'admin' 时，系统 **MUST** 在 `user_dimensions` 中包含 `is_admin: true` 标记。

