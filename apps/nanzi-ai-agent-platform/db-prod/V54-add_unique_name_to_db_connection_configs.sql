-- V54: 数据源名称唯一
-- 用途：避免数据源管理中出现同名连接，便于元数据导入时按名称识别。

ALTER TABLE `meta_db_connection_configs`
    ADD UNIQUE KEY `uk_meta_db_connection_configs_name` (`name`);
