-- V55: 数据源增加备注字段
-- 用途：说明数据源用途，便于用户在数据源管理和元数据导入时识别连接。

ALTER TABLE `meta_db_connection_configs`
    ADD COLUMN `description` VARCHAR(500) NOT NULL DEFAULT '' COMMENT '备注/用途说明' AFTER `database_name`;
