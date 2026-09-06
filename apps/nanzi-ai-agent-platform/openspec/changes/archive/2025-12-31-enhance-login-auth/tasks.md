# 任务列表 (Tasks)

- [ ] **数据库变更 (Database)**
  - [ ] 创建迁移脚本 `db-prod/V2-add_user_password.sql`。
  - [ ] 在 `ai_agent_users` 表中添加 `password_hash` 字段。
  - [ ] 应用数据库迁移。

- [ ] **后端实现 (Backend)**
  - [ ] 更新 `AuthService`：引入 `passlib` (bcrypt) 进行密码哈希与校验。
  - [ ] 更新 `POST /auth/login` 接口：支持 `{username, password}` 或 `{api_key}` 双重验证逻辑。
  - [ ] 新增 `PUT /auth/password` 接口：允许已登录用户修改自己的密码。
  - [ ] 安全加固：确保管理员接口 `POST /management/users` 不包含密码字段。

- [ ] **前端登录页 (Frontend Login)**
  - [ ] 改造 `Login.vue`：实现 Tab 切换 (账号密码 | API Key | YES系统)。
  - [ ] 实现 账号密码登录 表单及交互逻辑。
  - [ ] 处理 "未设置密码" 的错误提示，引导用户使用 API Key 登录。

- [ ] **前端个人中心 (Personal Center)**
  - [ ] 新增 "个人中心" 模块 (入口放置在侧边栏底部)。
  - [ ] 实现 "修改密码" 功能表单。

- [ ] **验证 (Verification)**
  - [ ] 验证旧版 API Key 登录依然可用。
  - [ ] 验证全流程：首次 API Key 登录 -> 设置密码 -> 退出 -> 使用密码登录。
  - [ ] 验证未设置密码时的错误引导。
