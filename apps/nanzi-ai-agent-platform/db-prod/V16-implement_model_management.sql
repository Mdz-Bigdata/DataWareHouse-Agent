-- ==========================================
-- V16: Implement Model Management System
-- Combined migration for:
-- 1. Creating ai_models table and init data
-- 2. Updating agent_execution_history for model tracking
-- 3. Cleaning up legacy system configs
-- ==========================================

-- 1. Create AI Models Table
CREATE TABLE IF NOT EXISTS ai_models (
    id CHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL COMMENT 'Display Name',
    model_id VARCHAR(255) NOT NULL COMMENT 'Actual Model ID for API',
    provider VARCHAR(50) NOT NULL COMMENT 'e.g., openai, azure, ollama',
    type VARCHAR(50) NOT NULL COMMENT 'e.g., llm, embedding',
    api_base_url VARCHAR(512) COMMENT 'API Base URL Override',
    api_key VARCHAR(512) COMMENT 'API Key Override',
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Initialize DeepSeek-V3.2
INSERT IGNORE INTO ai_models (id, name, model_id, provider, type, api_base_url, api_key, is_active)
VALUES (
    'deepseek-v3.2', 
    'DeepSeek-V3.2', 
    'DeepSeek-V3.2', 
    'deepseek', 
    'llm', 
    'https://ds-api.yovole.com/v1', 
    'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', 
    1
);

-- 2. Update ai_agent_execution_history
-- Add model tracking columns
-- Note: Using simple ALTER statements (requires MySQL 5.7+). 
-- If running on an existing DB where columns might exist, manual check required or ignore errors.
ALTER TABLE ai_agent_execution_history ADD COLUMN model_config_id CHAR(36) COMMENT 'Reference to ai_models.id';
ALTER TABLE ai_agent_execution_history ADD COLUMN model_id VARCHAR(255) COMMENT 'Snapshot of the model identifier used';

-- Index for querying by model
CREATE INDEX idx_history_model_id ON ai_agent_execution_history(model_id);
CREATE INDEX idx_history_model_config_id ON ai_agent_execution_history(model_config_id);

-- 3. Cleanup Legacy Configs
-- Remove global LLM configs that are now managed via ai_models table
DELETE FROM system_configs WHERE `key` IN ('llm_base_url', 'llm_api_key');

-- Update description for default model
UPDATE system_configs SET description = '系统默认模型' WHERE `key` = 'llm_model_name';
