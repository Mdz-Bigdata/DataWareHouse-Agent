-- V61: Align indexes observed in the current production schema
-- Created at: 2026-05-28

ALTER TABLE `ai_agent_execution_history`
ADD INDEX `idx_agent_created` (`agent_id`, `created_at`);

ALTER TABLE `system_configs`
ADD INDEX `idx_category_updated` (`category`, `updated_at`);
