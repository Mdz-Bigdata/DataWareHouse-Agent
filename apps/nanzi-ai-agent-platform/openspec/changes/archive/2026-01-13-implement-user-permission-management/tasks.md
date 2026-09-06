# 任务清单

## Phase 1: 数据库与后端基础
- [ ] 1. 创建数据库迁移脚本 `db-prod/V26-create_user_permission_tables.sql`。 <!-- id: db-migration -->
    - 创建 `ai_agent_roles` 表 (预留)。
    - 创建 `ai_agent_resource_permissions` 表。
- [ ] 2. 更新 `app/models/user.py` 及新建 `app/models/permission.py`。 <!-- id: backend-models -->
- [ ] 3. 创建 Pydantic Schemas `app/schemas/permission.py`。 <!-- id: backend-schemas -->
- [ ] 4. 实现 `PermissionService` (CRUD, Cache Logic)。 <!-- id: backend-service -->
- [ ] 5. 实现 Internal API `app/api/portal/endpoints/management.py` (新增 permission routes)。 <!-- id: backend-api -->

## Phase 2: 鉴权集成与业务逻辑 (Enforcement)
- [ ] 6. 在 `PermissionService` 中完善 `check_permission` 逻辑与 Redis 缓存。 <!-- id: cache-logic -->
- [ ] 7. **业务逻辑修改 - 数据集**: 修改 Dataset 获取接口，对非 Admin 用户过滤未授权的数据集。 <!-- id: enforce-dataset -->
- [ ] 8. **业务逻辑修改 - 智能体**: 修改 Chat/Agent 执行入口，若无权限则拒绝执行或返回提示。 <!-- id: enforce-agent -->
- [ ] 9. **业务逻辑修改 - 对外接口**: 实现 Dependency `verify_v1_api_access`，拦截 `api/v1` 请求并校验 API 权限。 <!-- id: enforce-api-v1 -->

## Phase 3: 前端实现
- [ ] 10. 更新前端 API Client (`src/api/user.ts`)，增加权限相关接口。 <!-- id: frontend-api -->
- [ ] 9. 开发权限配置组件 `UserPermissionEditor.vue` (含 Tabs 和 资源选择器)。 <!-- id: frontend-component -->
- [ ] 10. 集成到用户管理编辑弹窗。 <!-- id: frontend-integration -->

## Phase 4: 验证与测试
- [ ] 11. 手动测试：配置权限，验证 API 返回及缓存行为。 <!-- id: verify-manual -->
- [ ] 12. 补充自动化测试 `tests/test_permission.py`。 <!-- id: verify-auto -->
