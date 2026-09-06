-- Simple migration
ALTER TABLE ai_agents ADD COLUMN is_enabled BOOLEAN DEFAULT 1 NOT NULL COMMENT '是否启用' AFTER is_system;
