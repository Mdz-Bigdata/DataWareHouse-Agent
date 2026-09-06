# Tasks: 权限体系扩展实施路径

- [ ] **T1: 后端模型与服务适配**
    - [ ] 1.1 更新 `app/schemas/permission.py`，在 `PermissionSet` 中添加 `menus` 和 `elements` 字段。 <!-- id: be-schema -->
    - [ ] 1.2 修改 `app/services/permission_service.py` 中的 `_aggregate_permissions` 方法，支持 `menu` 和 `element` 类型。 <!-- id: be-logic -->
    - [ ] 1.3 更新 `_fetch_permission_details` 以支持新类型的展示（如返回菜单的中文标题）。 <!-- id: be-detail -->

- [ ] **T2: 前端基础能力建设**
    - [ ] 2.1 修改 `frontend/src/store/user.ts` (或对应的 Permission Store)，解析并存储菜单和功能点权限。 <!-- id: fe-store -->
    - [ ] 2.2 实现全局指令 `v-has-perm`。 <!-- id: fe-directive -->
    - [ ] 2.3 增强 `router/guard.ts`，增加基于 `meta.perm` 的权限拦截。 <!-- id: fe-guard -->

- [ ] **T3: UI 适配与业务接入**
    - [ ] 3.1 开发专用的 `NoPermission.vue` 提示页面。 <!-- id: fe-no-perm-ui -->
    - [ ] 3.2 重构侧边栏组件，接入动态菜单过滤逻辑。 <!-- id: fe-sidebar -->
    - [ ] 3.3 在核心页面（智能体管理、元数据管理）的敏感按钮上应用权限指令。 <!-- id: fe-biz -->

- [ ] **T4: 管理后台管理能力**
    - [ ] 4.1 改造权限分配弹窗：引入 Tab 切换，新增“界面权限”标签页并集成 Tree 控件。 <!-- id: fe-admin-ui -->
    - [ ] 4.2 编写 SQL 初始化脚本，注册现有的菜单项作为资源。 <!-- id: db-seed -->

- [ ] **T5: 验证与验收**
    - [ ] 5.1 编写单元测试验证 `PermissionService` 的聚合逻辑。 <!-- id: test-be -->
    - [ ] 5.2 模拟不同权限的用户登录，手动验证菜单过滤和按钮隐藏。 <!-- id: test-manual -->
