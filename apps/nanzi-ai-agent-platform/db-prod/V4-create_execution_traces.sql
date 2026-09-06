CREATE TABLE IF NOT EXISTS `ai_agent_execution_traces` (
    `id` BIGINT PRIMARY KEY AUTO_INCREMENT,
    `trace_id` VARCHAR(64) NOT NULL COMMENT '关联 Access Log 的 Trace ID',
    `step_number` INT NOT NULL COMMENT '步骤序号，从 1 开始',
    `event_type` VARCHAR(20) NOT NULL COMMENT '事件类型: thought, tool_call, tool_result, final_answer, error',
    `agent_name` VARCHAR(50) COMMENT '执行该步骤的 Agent 名称',
    `tool_name` VARCHAR(100) COMMENT '工具名称 (仅 tool_call/tool_result)',
    `tool_input` JSON COMMENT '工具入参',
    `tool_output` JSON COMMENT '工具出参',
    `execution_time_ms` FLOAT COMMENT '该步骤耗时 (毫秒)',
    `status` VARCHAR(20) DEFAULT 'success' COMMENT '状态: success, error',
    `error_message` TEXT COMMENT '错误详情',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_trace_id` (`trace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体执行链路日志表';
