-- V53: Increase length of event_type and status columns in trace and history tables
-- To fix Data too long error for columns like 'start_dataset_enhance'

ALTER TABLE `ai_agent_execution_traces` 
    MODIFY COLUMN `event_type` VARCHAR(50) NOT NULL COMMENT '事件类型: thought, tool_call, tool_result, final_answer, error, router, etc.',
    MODIFY COLUMN `status` VARCHAR(50) DEFAULT 'success' COMMENT '状态: success, error';

ALTER TABLE `ai_agent_execution_history`
    MODIFY COLUMN `status` VARCHAR(50) DEFAULT 'success' COMMENT '状态: success, failed, error';
