-- V31: Add metadata changelog table
-- Description: 添加元数据变更日志表，记录所有元数据对象的创建、更新、删除操作

-- 创建元数据变更日志表
CREATE TABLE `meta_changelog` (
    `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `resource_type` VARCHAR(20) NOT NULL COMMENT '资源类型: dataset/table/column/metric/relationship',
    `resource_id` VARCHAR(50) NOT NULL COMMENT '资源ID',
    `operation` VARCHAR(20) NOT NULL COMMENT '操作类型: create/update/delete',
    `old_data` JSON COMMENT '变更前数据',
    `new_data` JSON COMMENT '变更后数据',
    `changed_fields` JSON COMMENT '变更字段列表（仅update操作）',
    `user_id` INT COMMENT '操作用户ID',
    `user_name` VARCHAR(64) COMMENT '操作用户名',
    `reason` TEXT COMMENT '变更原因',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    
    PRIMARY KEY (`id`),
    INDEX `idx_resource` (`resource_type`, `resource_id`),
    INDEX `idx_user` (`user_id`),
    INDEX `idx_created` (`created_at`),
    INDEX `idx_operation` (`operation`),
    INDEX `idx_resource_operation` (`resource_type`, `resource_id`, `operation`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='元数据变更日志表';

-- 添加外键约束（如果用户表存在）
-- ALTER TABLE `meta_changelog` ADD CONSTRAINT `fk_changelog_user` 
--     FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL;

-- 创建变更日志统计视图（可选，用于快速查询统计信息）
CREATE OR REPLACE VIEW `v_meta_changelog_stats` AS
SELECT 
    DATE(created_at) as change_date,
    resource_type,
    operation,
    COUNT(*) as change_count,
    COUNT(DISTINCT user_id) as user_count,
    COUNT(DISTINCT resource_id) as resource_count
FROM meta_changelog 
WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
GROUP BY DATE(created_at), resource_type, operation
ORDER BY change_date DESC, resource_type, operation;

-- 示例查询注释：
-- 查询某个数据集的所有变更历史
-- SELECT * FROM meta_changelog 
-- WHERE resource_type = 'dataset' AND resource_id = '123' 
-- ORDER BY created_at DESC;

-- 查询最近7天的变更统计
-- SELECT * FROM v_meta_changelog_stats 
-- WHERE change_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY);

-- 查询某个用户的所有操作
-- SELECT resource_type, operation, COUNT(*) as count 
-- FROM meta_changelog 
-- WHERE user_id = 123 
-- GROUP BY resource_type, operation;
