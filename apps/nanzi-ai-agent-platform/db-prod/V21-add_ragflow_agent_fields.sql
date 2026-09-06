-- Migration V21: Add RAGFlow engine support to ai_agents table
-- Created: 2026-01-07
-- Purpose: Add fields to support remote RAGFlow engine for ai_agents

-- 1. Add engine_type and engine_config columns
ALTER TABLE ai_agents 
ADD COLUMN engine_type VARCHAR(20) DEFAULT 'LOCAL' AFTER name,
ADD COLUMN engine_config JSON NULL AFTER engine_type;

-- 2. Add comments (for MySQL/MariaDB)
ALTER TABLE ai_agents 
MODIFY COLUMN engine_type VARCHAR(20) DEFAULT 'LOCAL' COMMENT '执行引擎类型: LOCAL (内置 LLM), RAGFLOW (远程 RAGFlow 代理)',
MODIFY COLUMN engine_config JSON NULL COMMENT '引擎专用配置，如 ragflow_app_id, dataset_ids 等';

-- 3. Update CHANGELOG
-- Added V21: Integrate RAGFlow dual-mode support (Agent & Tool).