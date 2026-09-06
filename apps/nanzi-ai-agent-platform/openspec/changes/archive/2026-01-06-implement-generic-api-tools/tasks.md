# Tasks: 实现通用 HTTP 工具

## Pre-requisites
- [x] 确认数据库连接配置正确，且有权限执行 DDL。

## Phase 1: Backend Implementation
- [x] **DB**: 编写 `db-prod/V17-create_sys_api_tools.sql` 创建工具配置表。 <!-- id: db-migration -->
- [x] **Model**: 创建 `app/models/tool.py` 定义 `SysApiTool` SQLAlchemy 模型。 <!-- id: backend-model -->
- [x] **Schema**: 创建 `app/schemas/tool.py` 定义 Pydantic DTOs (Create/Update/Response)。 <!-- id: backend-schema -->
- [x] **Service**: 创建 `app/services/ai/tools/generic_api.py` 实现 `GenericApiToolFactory` 和 `_execute_request` 逻辑。 <!-- id: backend-service-logic -->
- [x] **Registry**: 修改 `app/services/ai/tools/registry.py` 或 `AgentManager` 以支持从 DB 加载动态工具。 <!-- id: backend-registry-update -->
- [x] **API**: 创建 `app/api/v1/tools.py` 实现工具管理的 CRUD 接口。 <!-- id: backend-api-crud -->
- [x] **Router**: 在 `app/main.py` 或 `api/v1/__init__.py` 中注册新的 APIRouter。 <!-- id: backend-router -->

## Phase 2: Frontend Implementation
- [x] **API Client**: 在 `frontend/src/api/` 中增加 `tool.ts` 用于调用后端 CRUD 接口。 <!-- id: frontend-api -->
- [x] **View**: 创建 `frontend/src/views/settings/ToolManagement.vue` 列表与编辑页。 <!-- id: frontend-view -->
- [x] **Route**: 在 `frontend/src/router/index.ts` (或 settings 路由配置) 中添加“工具管理” Tab 的路由。 <!-- id: frontend-route -->
- [x] **UI Component**: 实现参数定义编辑器 (Parameter Schema Editor) 组件。 <!-- id: frontend-schema-editor -->

## Phase 3: Verification
- [x] **Test**: 添加一个测试工具 (e.g., httpbin.org)，并在 Agent 对话中验证其是否能被成功调用。 <!-- id: verification-e2e -->