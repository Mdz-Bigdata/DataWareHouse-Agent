-- 加速按月按用户汇总 Token 用量
ALTER TABLE `ai_agent_execution_history`
  ADD INDEX `ix_username_created_at` (`username`, `created_at`);
