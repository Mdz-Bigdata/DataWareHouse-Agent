-- V33: Expand comment/description fields to support longer version notes
-- Date: 2026-01-21
-- Purpose: Fix issue where long version notes fail to save due to VARCHAR(255) limit.

-- 1. Modify ai_agent_versions
ALTER TABLE ai_agent_versions MODIFY COLUMN comment TEXT COMMENT '版本变动说明';

-- 2. Modify system_config_history
ALTER TABLE system_config_history MODIFY COLUMN description TEXT COMMENT '变更描述/备注';
