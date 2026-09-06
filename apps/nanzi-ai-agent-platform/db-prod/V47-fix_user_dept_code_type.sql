-- V47: Fix User department field and type
-- Rename dept_id to dept_code and change type to VARCHAR to allow non-numeric codes.

ALTER TABLE `ai_agent_users` 
CHANGE COLUMN `dept_id` `dept_code` VARCHAR(50) NULL COMMENT '部门代码 (支持字母/数字)';
