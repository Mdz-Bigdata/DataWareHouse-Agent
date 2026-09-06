-- ----------------------------------------------------------------
-- V12: Fix Collation Mismatch for Agent Execution History Table
-- 修复 ai_agent_execution_history 表的字符集排序规则，使其与 ai_agents 表保持一致
-- 解决 dashboard 页面智能体卡片无法显示的问题
-- ----------------------------------------------------------------

-- 修复 ai_agent_execution_history 表的字符集排序规则
-- 从 utf8mb4_0900_ai_ci 改为 utf8mb4_unicode_ci 以匹配 ai_agents 表

-- 转换整个表的默认字符集排序规则
ALTER TABLE ai_agent_execution_history CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 逐个更新字符串列的字符集排序规则
ALTER TABLE ai_agent_execution_history 
  MODIFY agent_id varchar(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Agent ID';

ALTER TABLE ai_agent_execution_history 
  MODIFY trace_id varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '关联 Trace ID';

ALTER TABLE ai_agent_execution_history 
  MODIFY user_id varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '用户 ID';

ALTER TABLE ai_agent_execution_history 
  MODIFY username varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '用户名';

ALTER TABLE ai_agent_execution_history 
  MODIFY query text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT '用户提问';

ALTER TABLE ai_agent_execution_history 
  MODIFY summary text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT 'AI 响应/总结';

ALTER TABLE ai_agent_execution_history 
  MODIFY status varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT 'success' COMMENT '状态: success, failed';

ALTER TABLE ai_agent_execution_history 
  MODIFY agent_version varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Agent 版本';
