-- ----------------------------------------------------------------
-- V13: Standardize All Tables to utf8mb4_unicode_ci Collation
-- 统一所有表的字符集排序规则为 utf8mb4_unicode_ci
-- 防止未来出现 JOIN 操作的 collation 冲突问题
-- ----------------------------------------------------------------

-- 1. Fix ai_agent_users table (V0)
ALTER TABLE ai_agent_users CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE ai_agent_users 
  MODIFY user_name VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY role VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT 'user',
  MODIFY remark VARCHAR(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL;

-- 2. Fix ai_agent_access_logs table (V0)
ALTER TABLE ai_agent_access_logs CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE ai_agent_access_logs 
  MODIFY trace_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY user_name VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  MODIFY endpoint VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY method VARCHAR(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY client_ip VARCHAR(45) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  MODIFY user_agent VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 3. Fix system_configs table (V1)
ALTER TABLE system_configs CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE system_configs 
  MODIFY `key` VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY description VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  MODIFY category VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'general';

-- 4. Fix meta_metrics table (V3)
ALTER TABLE meta_metrics CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE meta_metrics 
  MODIFY name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY display_name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY unit VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL;

-- 5. Fix meta_relationships table (V3)
ALTER TABLE meta_relationships CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE meta_relationships 
  MODIFY join_condition VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY join_type VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT 'LEFT';

-- 6. Fix ai_agent_execution_traces table (V4)
ALTER TABLE ai_agent_execution_traces CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
ALTER TABLE ai_agent_execution_traces 
  MODIFY trace_id VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY event_type VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  MODIFY agent_name VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  MODIFY tool_name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  MODIFY status VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT 'success';
