-- V48: 创建元数据导入数据库连接配置表
-- 用途：持久化保存用户在"从数据库加载 DDL"中配置的数据库连接信息，避免每次重复输入

CREATE TABLE IF NOT EXISTS `meta_db_connection_configs` (
    `id`            INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`          VARCHAR(100) NOT NULL COMMENT '连接别名，用户自定义，如"生产-业务库"',
    `db_type`       VARCHAR(20)  NOT NULL COMMENT '数据库类型: mysql | clickhouse | oracle',
    `host`          VARCHAR(255) NOT NULL COMMENT '数据库主机地址',
    `port`          INT          NOT NULL COMMENT '端口号',
    `db_user`       VARCHAR(100) NOT NULL COMMENT '数据库用户名',
    `password`      VARCHAR(255) NOT NULL DEFAULT '' COMMENT '数据库密码（明文存储）',
    `database_name` VARCHAR(100) NOT NULL COMMENT '数据库/库名',
    `created_by`    INT          NOT NULL DEFAULT 0 COMMENT '创建者用户 ID',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    INDEX `idx_created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='元数据导入-数据库连接配置';
