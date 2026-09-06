-- V39: Add conversation_id to AgentExecutionHistory
-- Description: Enables linking audit records to specific conversation flows.

ALTER TABLE `ai_agent_execution_history` 
ADD COLUMN `conversation_id` VARCHAR(50) NULL AFTER `trace_id`,
ADD INDEX `idx_conversation` (`conversation_id`);
