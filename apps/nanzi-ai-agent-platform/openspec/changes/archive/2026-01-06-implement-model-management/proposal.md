# 实现模型管理

**目标**: 在系统配置中集中管理 AI 模型，以防止硬编码模型字符串，并方便切换和配置不同的模型（LLM、多模态、Embedding）。

## 需要用户审查
- [ ] **数据库迁移**: 将创建一个新表 `ai_models`。
- [ ] **UI 变更**: 系统配置页面将新增一个 “模型管理” 标签页。
- [ ] **数据迁移**: Agent 版本和系统配置中现有的硬编码 “gpt-4o” 或类似字符串应映射到新的模型条目。

## 建议变更

### 数据库
- **新表**: `ai_models`
    - `id` (UUID, 主键)
    - `name` (String, 显示名称，如 "GPT-4o")
    - `model_id` (String, 实际 API 模型 ID，如 "gpt-4o")
    - `provider` (String, 如 "openai", "azure", "ollama")
    - `type` (String, 如 "llm", "embedding", "multimodal")
    - `api_base` (String, 可选覆盖地址)
    - `api_key` (String, 可选覆盖密钥，加密存储或掩码显示)
    - `is_active` (Boolean, 默认为 true)
    - `created_at` (DateTime)
    - `updated_at` (DateTime)

- **表修改**: `agent_execution_history`
    - 添加 `model_config_id` (UUID, 对应 ai_models.id) 以追踪使用了哪个具体配置。
    - 添加 `model_id` (String) 以快照形式记录使用的模型 ID。

### 后端 (`app/`)
#### 核心
- **新模型**: `app/models/ai_model.py` 定义 `AIModel`。
- **新 Schema**: `app/schemas/ai_model.py` (Pydantic 模型)。
- **新 API**: `app/api/portal/endpoints/models.py` 实现增删改查。

#### 服务
- **更新**: `app/services/ai/config.py` -> `AgentConfigProvider`。
    - 修改 `get_configured_llm`，不再仅依赖环境变量。如果 `llm_model_name` 匹配 `ai_models` 表中的记录，则优先使用该记录的 `api_key` 和 `base_url`。
- **更新**: `app/services/ai/agent_service.py`
    - 确保创建 `AgentExecutionHistory` 时记录使用的模型信息。

### 前端 (`frontend/`)
#### 系统配置 (System Config)
- **文件**: `frontend/src/views/SystemConfig.vue`
- **变更**:
    - 增加 “模型管理” (Model Management) 标签页。
    - 内容: 模型列表表格，以及 “添加模型” 按钮。
    - 模态框: 输入 名称, ID, 提供商, 类型, Base URL, API Key。
    - 更新 “参数配置” 标签页: `llm_model_name` 字段改为下拉选择框，数据来源为 `GET /models`。

#### 智能体管理 (Agent Management)
- **文件**: `frontend/src/views/AgentManagement.vue`
- **变更**:
    - 更新 “版本配置模态框”: “模型名称” 输入框改为下拉选择框，数据来源为 `GET /models`。

## 验证计划
### 自动化测试
- **后端**:
    - `tests/api/test_models.py`: 测试 AI 模型的增删改查。
    - `tests/services/test_agent_config.py`: 测试从数据库解析 LLM 配置。

### 手动验证
1. **设置**: 运行迁移脚本。
2. **UI 测试**:
    - 进入 系统配置 -> 模型管理。
    - 创建一个模型 "My GPT-4"，ID 为 "gpt-4-turbo" 并填入测试 Key。
    - 确认列表显示正常。
    - 进入 参数配置。将默认模型修改为 "My GPT-4" (应在下拉列表中可选)。
    - 保存。
3. **Agent 测试**:
    - 创建/编辑一个 Agent 版本。在下拉列表中选择 "My GPT-4"。
    - 与 Agent 对话。
    - 检查数据库 `agent_execution_history`，确认 `model_config_id` 或 `model_id` 已正确记录。
