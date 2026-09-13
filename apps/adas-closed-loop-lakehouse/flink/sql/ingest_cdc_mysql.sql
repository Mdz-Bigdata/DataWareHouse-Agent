-- ========================================================================
-- 通道一 · Flink CDC：各平台 MySQL 业务库 → ODS
-- 由 adas_lakehouse.ingest.sql 生成，请勿手工编辑。
-- 先执行 ingest_00_catalog.sql 建 Catalog 与会话参数。
-- 三阶段：全量快照 → 增量 binlog → 断点续传；业务库零侵入、不改代码。
-- ========================================================================


-- 通道一 · Flink CDC：collect_platform.collect_task → ods_collect_task
-- 承接数据：各平台 MySQL 业务库（产线 / 标注 / 训练等）；入湖方式：读 binlog 实时同步
-- 三阶段：全量快照 → 增量 binlog → 断点续传；同步延迟秒级，业务库零侵入不改代码
CREATE TEMPORARY TABLE `cdc_src_ods_collect_task` (
  `collect_task_id` STRING NOT NULL COMMENT '采集任务 ID',
  `task_name` STRING COMMENT '任务名称',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '执行车辆编码',
  `target_scene` STRING COMMENT '目标采集场景',
  `planned_duration_min` INT COMMENT '计划采集时长（分钟）',
  `actual_clip_count` INT COMMENT '实际产出 clip 数',
  `task_status` STRING COMMENT '任务状态',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  PRIMARY KEY (`collect_task_id`) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = 'localhost',
  'port' = '18606',
  'username' = 'adas',
  'password' = '${CDC_MYSQL_PASSWORD}',
  'database-name' = 'collect_platform',
  'table-name' = 'collect_task',
  'server-id' = '5400-5404',
  'scan.startup.mode' = 'initial',
  'scan.incremental.snapshot.enabled' = 'true'
);

-- 统一盖章：ODS 层系统字段 = _ingest_time + _source_system（domains.Layer.ODS.system_fields）
INSERT INTO `paimon`.`adas_lakehouse`.`ods_collect_task`
SELECT
  `collect_task_id`,
  `task_name`,
  `project_code`,
  `vehicle_code`,
  `target_scene`,
  `planned_duration_min`,
  `actual_clip_count`,
  `task_status`,
  `start_time`,
  `end_time`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  '采集管理系统' AS `_source_system`
FROM `cdc_src_ods_collect_task`;

-- 通道一 · Flink CDC：vehicle_platform.vehicle_info → ods_vehicle_info
-- 承接数据：各平台 MySQL 业务库（产线 / 标注 / 训练等）；入湖方式：读 binlog 实时同步
-- 三阶段：全量快照 → 增量 binlog → 断点续传；同步延迟秒级，业务库零侵入不改代码
CREATE TEMPORARY TABLE `cdc_src_ods_vehicle_info` (
  `vehicle_code` STRING NOT NULL COMMENT '车辆编码，data_id 第二段来源',
  `vin` STRING COMMENT '车架号',
  `vehicle_model` STRING COMMENT '车型',
  `fleet_name` STRING COMMENT '所属车队',
  `autonomy_level` STRING COMMENT '智驾等级',
  `register_date` DATE COMMENT '入队日期',
  `status` STRING COMMENT '车辆状态',
  PRIMARY KEY (`vehicle_code`) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = 'localhost',
  'port' = '18606',
  'username' = 'adas',
  'password' = '${CDC_MYSQL_PASSWORD}',
  'database-name' = 'vehicle_platform',
  'table-name' = 'vehicle_info',
  'server-id' = '5400-5404',
  'scan.startup.mode' = 'initial',
  'scan.incremental.snapshot.enabled' = 'true'
);

-- 统一盖章：ODS 层系统字段 = _ingest_time + _source_system（domains.Layer.ODS.system_fields）
INSERT INTO `paimon`.`adas_lakehouse`.`ods_vehicle_info`
SELECT
  `vehicle_code`,
  `vin`,
  `vehicle_model`,
  `fleet_name`,
  `autonomy_level`,
  `register_date`,
  `status`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  '车辆管理系统' AS `_source_system`
FROM `cdc_src_ods_vehicle_info`;

-- 通道一 · Flink CDC：config_platform.sensor_config → ods_sensor_config
-- 承接数据：各平台 MySQL 业务库（产线 / 标注 / 训练等）；入湖方式：读 binlog 实时同步
-- 三阶段：全量快照 → 增量 binlog → 断点续传；同步延迟秒级，业务库零侵入不改代码
CREATE TEMPORARY TABLE `cdc_src_ods_sensor_config` (
  `vehicle_code` STRING NOT NULL COMMENT '车辆编码',
  `sensor_id` STRING NOT NULL COMMENT '传感器 ID',
  `sensor_type` STRING COMMENT '类型：camera/lidar/radar/imu/gps',
  `sensor_model` STRING COMMENT '型号',
  `mount_position` STRING COMMENT '安装位置',
  `intrinsic_json` STRING COMMENT '内参（JSON）',
  `extrinsic_json` STRING COMMENT '外参（JSON）',
  `sample_rate_hz` DOUBLE COMMENT '采样率',
  `effective_from` TIMESTAMP(3) COMMENT '生效时间',
  PRIMARY KEY (`vehicle_code`, `sensor_id`) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = 'localhost',
  'port' = '18606',
  'username' = 'adas',
  'password' = '${CDC_MYSQL_PASSWORD}',
  'database-name' = 'config_platform',
  'table-name' = 'sensor_config',
  'server-id' = '5400-5404',
  'scan.startup.mode' = 'initial',
  'scan.incremental.snapshot.enabled' = 'true'
);

-- 统一盖章：ODS 层系统字段 = _ingest_time + _source_system（domains.Layer.ODS.system_fields）
INSERT INTO `paimon`.`adas_lakehouse`.`ods_sensor_config`
SELECT
  `vehicle_code`,
  `sensor_id`,
  `sensor_type`,
  `sensor_model`,
  `mount_position`,
  `intrinsic_json`,
  `extrinsic_json`,
  `sample_rate_hz`,
  `effective_from`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  '配置管理系统' AS `_source_system`
FROM `cdc_src_ods_sensor_config`;
