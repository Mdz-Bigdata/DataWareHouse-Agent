## ADDED Requirements
### Requirement: Configuration Audit Trail (配置审计追踪)
系统 **MUST** 为所有系统级配置（`system_configs`）的变更保留历史记录。

#### Scenario: 记录变更
- **WHEN** 用户或系统更新某个配置项（如 `llm_model_name` 或 `router_system_prompt`）
- **THEN** 系统在 `system_config_history` 表中创建一条新记录，包含：变更前的旧值、变更后的新值、操作人用户名、变更时间及变更原因（如有）。

### Requirement: Configuration History Query (配置历史查询)
系统 **MUST** 提供接口查询特定配置项的历史版本列表。

#### Scenario: 查询历史
- **WHEN** 管理员请求查询 `router_system_prompt` 的历史
- **THEN** 系统按时间倒序返回所有变更记录。
