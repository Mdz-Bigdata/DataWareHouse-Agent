# Spec: 菜单动态显示控制

## ADDED Requirements

### 1. 菜单权限聚合
系统 **MUST** 在获取用户信息时，聚合当前用户（及所属角色）的所有 `resource_type='menu'` 的权限记录。

### 2. 左侧导航动态过滤
前端侧边栏 **MUST** 根据用户的 `menus` 权限列表动态渲染。
#### Scenario: 菜单可见性验证
- **GIVEN** 用户 A 拥有 `menu:metadata` 权限，但没有 `menu:system:config` 权限
- **WHEN** 用户 A 登录系统
- **THEN** 左侧菜单栏显示“元数据管理”
- **AND** 隐藏“系统配置”入口

### 3. 路由安全拦截
前端路由守卫 **MUST** 校验目标路径所需的菜单权限。
#### Scenario: 直接 URL 访问拦截
- **WHEN** 未获得 `menu:audit` 权限的用户手动在浏览器地址栏输入 `/audit/logs`
- **THEN** 系统将其重定向至 403 页面或首页，并弹出权限提示

### 4. 无权限兜底引导
当系统检测到用户不具备任何菜单访问权限时，**MUST** 展示引导提示。
#### Scenario: 空权限登录重定向
- **GIVEN** 用户 B 是普通用户，且没有任何关联的角色或直接分配的 `menu` 权限
- **WHEN** 用户 B 成功登录系统
- **THEN** 系统自动跳转至专属的 `/no-permission` 提示页面
- **AND** 隐藏常规的功能布局（如侧边栏、顶部导航等）
