-- Add sort_order field to ai_agents table
-- Larger values will be displayed first

ALTER TABLE `ai_agents` ADD COLUMN `sort_order` INT DEFAULT 0 COMMENT '排序字段，值越大越靠前';
