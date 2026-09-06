-- V34: Add model and temperature info to execution traces
-- Used for tracking tool-level runtime configuration in audit logs

ALTER TABLE `ai_agent_execution_traces` 
ADD COLUMN `model` VARCHAR(100) DEFAULT NULL COMMENT '实际使用的模型名称' AFTER `error_message`,
ADD COLUMN `temperature` FLOAT DEFAULT NULL COMMENT '使用的温度系数' AFTER `model`;
