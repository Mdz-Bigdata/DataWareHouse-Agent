CREATE TABLE IF NOT EXISTS `system_config_history` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `config_key` VARCHAR(255) NOT NULL,
    `old_value` MEDIUMTEXT COMMENT '变更前的值',
    `new_value` MEDIUMTEXT COMMENT '变更后的值',
    `description` VARCHAR(255) COMMENT '变更描述/备注',
    `changed_by` VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '操作人',
    `change_type` VARCHAR(20) DEFAULT 'UPDATE' COMMENT 'UPDATE, CREATE, DELETE',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_config_key` (`config_key`),
    INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置变更历史审计表';
