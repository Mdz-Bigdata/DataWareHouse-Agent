-- ----------------------------------------------------------------
-- V8: Create Agent Execution History Table
-- 用于记录 Agent 对话的高层历史记录 (Who asked What, and Response)
-- ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS `ai_agent_execution_history` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `agent_id` varchar(36) NOT NULL COMMENT 'Agent ID',
  `trace_id` varchar(64) NOT NULL COMMENT '关联 Trace ID',
  `user_id` varchar(64) DEFAULT NULL COMMENT '用户 ID',
  `username` varchar(64) DEFAULT NULL COMMENT '用户名',
  `query` text COMMENT '用户提问',
  `summary` text COMMENT 'AI 响应/总结',
  `total_tokens` int(11) DEFAULT '0' COMMENT '消耗 Token 数 (预留)',
  `execution_time_ms` float DEFAULT NULL COMMENT '耗时(ms)',
  `status` varchar(20) DEFAULT 'success' COMMENT '状态: success, failed',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_trace_id` (`trace_id`),
  KEY `ix_agent_id` (`agent_id`),
  KEY `ix_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 对话历史记录表';
