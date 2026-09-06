# 任务列表：集中化系统配置

- [x] **数据库与后端基础** <!-- id: 0 -->
    - [x] 在 `db-prod/` 下创建迁移脚本 `V{Timestamp}-create_system_configs.sql` (包含 fields: key, value, description, category, is_secret)。 <!-- id: 1 -->
    - [x] 实现 `ConfigService` 类 (`get(key, default)`, `set(key, value)`)。 <!-- id: 2 -->
    - [x] 实现 Redis 缓存逻辑：`get` 时查缓存，`set` 时更新/删除缓存 (确保 DB 更新后缓存一致)。 <!-- id: 3 -->
- [x] **API 接口实现** <!-- id: 4 -->
    - [x] 实现 `GET /api/portal/system/configs` (按 Category 分组返回，敏感字段掩码处理)。 <!-- id: 5 -->
    - [x] 实现 `PUT /api/portal/system/configs` (批量更新配置，并刷新 Redis)。 <!-- id: 6 -->
- [x] **业务服务集成 (动态配置)** <!-- id: 7 -->
    - [x] 重构 `LLMFactory` (client.py)，使其优先从 `ConfigService` 获取 `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`。 <!-- id: 8 -->
    - [x] 重构 `data_api.py`，使其优先从 `ConfigService` 获取 `EXTERNAL_SQL_API_URL` 等配置。 <!-- id: 9 -->
- [x] **前端实现** <!-- id: 10 -->
    - [x] 在 `SystemConfig.vue` 中添加“配置管理” (Configuration) 标签页/模块。 <!-- id: 11 -->
    - [x] 创建配置表单组件 (支持敏感信息的掩码显示和编辑)。 <!-- id: 12 -->
    - [x] 连接保存/重置接口，并处理加载状态。 <!-- id: 13 -->
- [x] **验证** <!-- id: 14 -->
    - [x] 验证：在 UI 修改 LLM Model 后，下一次对话立即生效。 <!-- id: 15 -->
    - [x] 验证：API Key 的掩码/取消掩码功能正常。 <!-- id: 16 -->
    - [x] 验证：Redis 缓存是否正确建立和刷新 (通过日志或 Redis 命令行检查)。 <!-- id: 17 -->
