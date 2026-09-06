-- V38: Create Agent Scheduled Tasks Table
-- Description: Adds persistence for cron-based agent automation tasks.

CREATE TABLE IF NOT EXISTS `ai_agent_scheduled_tasks` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `name` VARCHAR(100) NOT NULL COMMENT '任务显示名称',
    `user_id` INT NOT NULL COMMENT '创建者 ID',
    `agent_id` VARCHAR(50) NOT NULL COMMENT '绑定的智能体 ID',
    `conversation_id` VARCHAR(50) NOT NULL COMMENT '绑定的会话 ID，执行结果发到这里',
    `cron_expr` VARCHAR(50) NOT NULL COMMENT 'Cron 表达式 (e.g. 0 8 * * *)',
    `prompt` TEXT NOT NULL COMMENT '给智能体的具体指令',
    `source` VARCHAR(20) DEFAULT 'web' COMMENT '来源: web, agent',
    `status` SMALLINT DEFAULT 1 COMMENT '状态: 0-已停止, 1-运行中, 2-异常',
    `config` JSON DEFAULT NULL COMMENT '额外配置: 如通知渠道、重试策略',
    `run_count` INT DEFAULT 0 COMMENT '累计执行次数',
    `last_run_id` VARCHAR(50) DEFAULT NULL COMMENT '最近一次执行的 Trace ID',
    `last_run_at` DATETIME DEFAULT NULL COMMENT '上次执行时间',
    `next_run_at` DATETIME DEFAULT NULL COMMENT '下次预计执行时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    -- Constraints & Indexes
    INDEX `idx_user_tasks` (`user_id`),
    INDEX `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体定时任务定义表';