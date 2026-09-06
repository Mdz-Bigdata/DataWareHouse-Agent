- [x] **后端: 数据库 & 模型**
    - [x] 创建 `ai_models` 表的 SQL 迁移脚本。
    - [x] 创建 `agent_execution_history` 表的 SQL 更新脚本 (添加 `model_config_id` 字段)。
    - [x] 创建 SQLAlchemy 模型 `app/models/ai_model.py`。
    - [x] 创建 Pydantic Schemas `app/schemas/ai_model.py`。

- [x] **后端: API & 逻辑**
    - [x] 实现 `app/api/portal/endpoints/models.py` (CRUD 接口)。
    - [x] 在 `app/api/api.py` 中注册新的路由。
    - [x] 更新 `app/services/ai/config.py` 以使用 `ai_models` 进行配置解析。
    - [x] 更新 `app/services/ai/agent_service.py` 以在历史记录中保存模型信息。

- [x] **前端: 系统配置**
    - [x] 创建 `frontend/src/api/model.ts` 用于 API 调用。
    - [x] 修改 `frontend/src/views/SystemConfig.vue`，添加 “模型管理” 标签页。
    - [x] 实现 模型列表 和 编辑/新建模态框 组件。
    - [x] 更新 “参数配置” 中的 `llm_model_name` 为下拉选择框。

- [x] **前端: 智能体管理**
    - [x] 修改 `frontend/src/views/AgentManagement.vue` 以获取模型列表。
    - [x] 更新 版本配置模态框，使用下拉框选择模型。

- [x] **验证 & 清理**
    - [x] 手动验证完整流程。
    - [x] 确保 `db-prod` 中的脚本已更新。