-- ----------------------------------------------------------------
-- V36: Add Agent Max Context Messages Configuration
-- 限制发送给 LLM 的历史消息条数，优化 Token 消耗和响应速度
-- ----------------------------------------------------------------

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('agent_max_context_messages', '20', '发送给 LLM 的最大历史消息条目数 (建议 10-30, 20条约10轮对话)', 'agent', 0);
