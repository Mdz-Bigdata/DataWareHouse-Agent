# 任务清单：SSO 集成

- [ ] **配置更新**
    - [ ] 在 `app/core/config.py` 中添加 SSO 环境变量配置。
    - [ ] 更新 `env.example`，添加 SSO 相关占位符。

- [ ] **服务层实现**
    - [ ] 在 `app/services/auth_service.py` 中实现 `authenticate_sso_user` 方法。
    - [ ] 确保方法包含：远程 SSO 调用、本地用户映射、异常处理。

- [ ] **接口层实现**
    - [ ] 在 `app/api/portal/endpoints/auth.py` 中新增 `SSOLoginRequest` 和 `/sso/login` 路由。
    - [ ] 复用现有的 Token 生成和 Cookie 设置逻辑。

- [ ] **测试验证**
    - [ ] 编写模拟 SSO 响应的单元测试。
    - [ ] 验证用户不存在或被禁用时的错误处理逻辑。

- [ ] **前端集成**
    - [ ] 在登录页面添加“SSO 登录”切换或独立按钮。
    - [ ] 对接后端 `/sso/login` 接口。
