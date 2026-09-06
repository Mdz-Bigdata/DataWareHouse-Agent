# 模型管理设计文档

## 数据库 Schema

```sql
CREATE TABLE ai_models (
    id CHAR(36) PRIMARY KEY, -- UUID
    name VARCHAR(255) NOT NULL,
    model_id VARCHAR(255) NOT NULL, -- 发送给 API 的实际字符串, 例如 "gpt-4o"
    provider VARCHAR(50) NOT NULL, -- "openai", "azure", "anthropic", "google", "local"
    type VARCHAR(50) NOT NULL, -- "llm", "embedding", "multimodal", "rerank"
    api_base_url VARCHAR(512),
    api_key VARCHAR(512), -- 建议加密存储，或暂时明文（内部工具）
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

ALTER TABLE agent_execution_history ADD COLUMN model_config_id CHAR(36);
ALTER TABLE agent_execution_history ADD COLUMN model_id VARCHAR(255); -- 快照方便阅读
```

## API 设计

### `GET /api/portal/models`
获取模型列表。
查询参数: `type` (可选).

### `POST /api/portal/models`
创建新模型。

### `PUT /api/portal/models/{id}`
更新模型。

### `DELETE /api/portal/models/{id}`
软删除或物理删除。

## UI 设计
### 系统配置页面 (System Config Page)
- **新标签页**: "模型管理" (Model Management) (顺序: 2, 位于 "参数配置" 之后)
- **布局**: 列表视图，包含列 (名称, 模型 ID, 类型, 提供商, 状态)。操作: 编辑, 删除。
- **添加/编辑 模态框**: 表单字段。
    - 提供商: 下拉选择 (OpenAI, Azure 等)
    - 类型: 下拉选择 (LLM, Embedding 等)
    - API Key/Base URL: 选填（若为空则可能使用系统默认或通过其他方式配置）

## 集成点
1. **Config Service**: 当获取系统默认的 `llm_model_name` 时，如果它匹配 `ai_models` 中的条目，则使用该条目的凭据（如果有）覆盖环境变量。
2. **Router Service**: 当 Router 选择 Agent，且 Agent 绑定了特定模型版本时，`AgentConfigProvider` 使用该模型名称。它应在 `ai_models` 中查找具体配置。
