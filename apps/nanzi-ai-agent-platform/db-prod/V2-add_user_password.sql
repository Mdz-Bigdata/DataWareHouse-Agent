-- Add password support to users table
ALTER TABLE `ai_agent_users`
ADD COLUMN `password_hash` VARCHAR(255) NULL COMMENT '用户密码哈希 (bcrypt)' AFTER `user_name`;

-- Initialize admin password (optional, user can set it later via API Key)
-- We don't set a default password for security.
