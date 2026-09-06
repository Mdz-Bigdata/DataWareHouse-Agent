# 变更提案: 集中化系统配置 (Centralize System Configuration)

## 为什么 (Why)
当前，关键的系统配置（如 LLM 设置、外部数据 API 设置）都硬编码在环境变量 (`.env`) 中。修改这些配置需要重启服务并访问服务器文件系统。为了实现“免运维 (Ops-free)”管理和运行时的灵活性，这些配置应通过系统管理界面进行管理。

## 变更内容 (What Changes)
- **数据库**: 在 `db-prod` 中引入以 `Vx` 开头的 SQL 脚本，创建 `system_configs` 表，用于存储键值对配置。
- **后端**:
    - 实现 `ConfigService`，支持从数据库读取配置，并以 `.env` 变量作为兜底 (Fallback)。
    - 实现 Redis 缓存机制：读取优先查缓存，更新配置时同步刷新或失效缓存。
    - 更新 `LLMFactory` 和 `DataAPI` 以使用动态配置值。
    - 添加 CRUD 系统配置的 API 接口。
- **前端**:
    - 更新 `SystemConfig.vue`，增加一个用于查看和编辑这些设置的表单。
    - 支持敏感字段（如 API Key）的脱敏显示。

## 影响 (Impact)
- **涉及 Specs**: `system-config` (新增)
- **涉及代码**: `app/core/config.py`, `app/core/llm/client.py`, `app/services/ai/tools/data_api.py`, `frontend/src/views/SystemConfig.vue`, `db-prod/`
