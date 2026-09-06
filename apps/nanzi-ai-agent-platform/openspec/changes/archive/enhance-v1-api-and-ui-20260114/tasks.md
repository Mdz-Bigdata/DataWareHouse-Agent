- [ ] **Backend: API 资源动态发现**
  - 在 `app/services/metadata_service.py` (或类似) 中实现 `get_v1_api_resources()`，扫描 `app.router`。
  - 确保返回格式包含 `method`, `path`, `summary`。

- [ ] **Backend: 扩展权限 API**
  - 更新 `app/api/portal/permission.py`，在获取权限资源列表时包含 API 资源。
  - 更新分配权限的接口逻辑（如果需要特殊处理 `api` 类型）。

- [ ] **Backend: API 鉴权中间件/依赖**
  - 在 `app/core/dependencies.py` 中实现 `verify_api_permission` 依赖。
  - 在 V1 接口的 `APIRouter` 中应用该依赖，或在 `app/main.py` 中全局应用（针对 `/api/v1` 前缀）。

- [ ] **Backend: 实现用户画像接口**
  - 创建 `app/api/v1/endpoints/users.py`。
  - 实现 `GET /profile` (支持 `username` 参数)。
  - 注册路由到 `app/api/v1/api.py`。

- [ ] **Frontend: 权限管理界面升级**
  - 修改 `frontend/src/views/UserManagement.vue` (或权限相关组件)。
  - 增加 "接口权限" Tab。
  - 调用后端接口获取 API 资源列表并渲染复选框。

- [ ] **Frontend: 智能体管理界面升级**
  - 修改 `frontend/src/views/AgentManagement.vue`。
  - 引入 Toggle Switch 组件（或使用 Tailwind 手写）。
  - 替换 "Active" 文本标签为 Switch。
  - 替换底部的状态切换按钮为 "预览" 按钮。
  - 实现 "预览" 按钮点击逻辑：构造 `/embed-chat` URL 并打开。

- [ ] **Validation**
  - 验证 V1 接口无权限时返回 403。
  - 验证 V1 接口有权限时正常访问。
  - 验证用户画像接口返回数据正确。
  - 验证 EmbedChat 预览能自动登录。
