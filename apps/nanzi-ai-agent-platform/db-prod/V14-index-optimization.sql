-- ----------------------------------------------------------------
-- V14: Database Performance Index Optimization
-- 数据库性能索引优化 - 生产环境执行脚本
-- ----------------------------------------------------------------

-- 1. 访问日志表索引优化
-- ai_agent_access_logs 表
ALTER TABLE ai_agent_access_logs ADD INDEX idx_status_created (status_code, created_at);
ALTER TABLE ai_agent_access_logs ADD INDEX idx_endpoint_created (endpoint(100), created_at);
ALTER TABLE ai_agent_access_logs ADD INDEX idx_trace_id_status (trace_id, status_code);
ALTER TABLE ai_agent_access_logs ADD INDEX idx_user_status_time (user_name, status_code, created_at);
ALTER TABLE ai_agent_access_logs ADD INDEX idx_endpoint_status_time (endpoint(50), status_code, created_at);

-- 2. 智能体执行历史索引优化
-- ai_agent_execution_history 表
ALTER TABLE ai_agent_execution_history ADD INDEX idx_user_created (user_id, created_at);
ALTER TABLE ai_agent_execution_history ADD INDEX idx_status_created (status, created_at);
ALTER TABLE ai_agent_execution_history ADD INDEX idx_agent_status_time (agent_id, status, created_at);
ALTER TABLE ai_agent_execution_history ADD INDEX idx_user_status_time (user_id, status, created_at);

-- 3. 智能体执行链路索引优化
-- ai_agent_execution_traces 表
ALTER TABLE ai_agent_execution_traces ADD INDEX idx_trace_created (trace_id, created_at);
ALTER TABLE ai_agent_execution_traces ADD INDEX idx_event_created (event_type, created_at);
ALTER TABLE ai_agent_execution_traces ADD INDEX idx_tool_created (tool_name, created_at);
ALTER TABLE ai_agent_execution_traces ADD INDEX idx_status_created (status, created_at);
ALTER TABLE ai_agent_execution_traces ADD INDEX idx_trace_event_time (trace_id, event_type, created_at);
ALTER TABLE ai_agent_execution_traces ADD INDEX idx_trace_status_time (trace_id, status, created_at);

-- 4. 系统配置表索引优化
-- system_configs 表
ALTER TABLE system_configs ADD INDEX idx_key_updated (`key`, updated_at);

-- 5. 元数据表索引优化
-- meta_datasets 表
ALTER TABLE meta_datasets ADD INDEX idx_data_source_created (data_source, created_at);

-- meta_tables 表
ALTER TABLE meta_tables ADD INDEX idx_dataset_created (dataset_id, created_at);
ALTER TABLE meta_tables ADD INDEX idx_status_created (status, created_at);

-- meta_columns 表
ALTER TABLE meta_columns ADD INDEX idx_table_created (table_id, created_at);
ALTER TABLE meta_columns ADD INDEX idx_type_created (type, created_at);

-- meta_metrics 表
ALTER TABLE meta_metrics ADD INDEX idx_dataset_created (dataset_id, created_at);
ALTER TABLE meta_metrics ADD INDEX idx_name_created (name, created_at);

-- 6. 智能体系统索引优化
-- ai_agents 表
ALTER TABLE ai_agents ADD INDEX idx_is_system_created (is_system, created_at);
ALTER TABLE ai_agents ADD INDEX idx_owner_created (owner_group, created_at);

-- ai_agent_versions 表
ALTER TABLE ai_agent_versions ADD INDEX idx_agent_status (agent_id, status);
ALTER TABLE ai_agent_versions ADD INDEX idx_agent_version (agent_id, version_number);
ALTER TABLE ai_agent_versions ADD INDEX idx_status_created (status, created_at);

-- 7. 系统配置历史表索引优化
-- system_config_history 表
ALTER TABLE system_config_history ADD INDEX idx_config_created (config_key, created_at);
ALTER TABLE system_config_history ADD INDEX idx_changed_by_created (changed_by, created_at);
ALTER TABLE system_config_history ADD INDEX idx_type_created (change_type, created_at);

-- 8. 快捷指令表索引优化
-- slash_commands 表
ALTER TABLE slash_commands ADD INDEX idx_sort_created (sort_order, created_at);
ALTER TABLE slash_commands ADD INDEX idx_created_by_created (created_by, created_at);
