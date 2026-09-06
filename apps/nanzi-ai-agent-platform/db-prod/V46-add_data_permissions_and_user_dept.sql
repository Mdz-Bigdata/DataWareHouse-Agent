-- V46: Add Data Permissions Configuration and User Department Info
-- Description: Supports row-level security for ChatBI and hierarchical organization management.

-- 1. Extend Users table with Department info and Extra Data
ALTER TABLE `ai_agent_users` 
ADD COLUMN `dept_id` INT NULL COMMENT '部门唯一标识 ID' AFTER `role`,
ADD COLUMN `org_path` VARCHAR(255) NULL COMMENT '组织结构全路径 (例如: yovole/sh/dc1)' AFTER `dept_id`,
ADD COLUMN `extra_data` TEXT NULL COMMENT '预留扩展字段 (存储 JSON 格式信息)' AFTER `org_path`;

-- 2. Extend Datasets table with Permission configs
ALTER TABLE `meta_datasets`
ADD COLUMN `enable_data_perm` TINYINT(1) DEFAULT 0 COMMENT '是否启用精细化数据权限校验 (1:启用, 0:禁用)' AFTER `status`,
ADD COLUMN `row_filter_config` JSON NULL COMMENT '行级权限配置策略 (支持 User/Role 分层)' AFTER `enable_data_perm`;

-- Add index for org_path to optimize hierarchical filtering
CREATE INDEX `idx_user_org_path` ON `ai_agent_users` (`org_path`);
