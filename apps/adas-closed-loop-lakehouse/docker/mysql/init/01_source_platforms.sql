-- ============================================================================
-- 模拟源系统：三个平台业务库，供通道一 Flink CDC 读 binlog 入湖。
--
-- 表结构逐列对齐 flink/sql/ingest_cdc_mysql.sql 里的 CDC 源表 DDL
-- （那份文件由 src/adas_lakehouse/ingest/sql.py 生成，是唯一事实来源）：
--
--   collect_platform.collect_task   → ods_collect_task    PK(collect_task_id)
--   vehicle_platform.vehicle_info   → ods_vehicle_info    PK(vehicle_code)
--   config_platform.sensor_config   → ods_sensor_config   PK(vehicle_code, sensor_id)
--
-- Flink SQL 里的 STRING 在这里落成 VARCHAR/TEXT，TIMESTAMP(3) 落成 DATETIME(3)，
-- 长度是本文件自选的（源系统真实长度未知），不影响 CDC 类型推导。
--
-- ⚠️ 这是本地开发用的假数据，不是任何真实车队的数据。
-- ============================================================================

SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- 1. 采集管理系统
-- ----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `collect_platform`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `collect_platform`.`collect_task` (
  `collect_task_id`      VARCHAR(128) NOT NULL COMMENT '采集任务 ID',
  `task_name`            VARCHAR(200)          COMMENT '任务名称',
  `project_code`         VARCHAR(64)           COMMENT '所属项目',
  `vehicle_code`         VARCHAR(64)           COMMENT '执行车辆编码',
  `target_scene`         VARCHAR(128)          COMMENT '目标采集场景',
  `planned_duration_min` INT                   COMMENT '计划采集时长（分钟）',
  `actual_clip_count`    INT                   COMMENT '实际产出 clip 数',
  `task_status`          VARCHAR(32)           COMMENT '任务状态',
  `start_time`           DATETIME(3)           COMMENT '开始时间',
  `end_time`             DATETIME(3)           COMMENT '结束时间',
  PRIMARY KEY (`collect_task_id`)
) ENGINE=InnoDB COMMENT='采集任务；CDC → ods_collect_task';

INSERT IGNORE INTO `collect_platform`.`collect_task`
  (`collect_task_id`, `task_name`, `project_code`, `vehicle_code`, `target_scene`,
   `planned_duration_min`, `actual_clip_count`, `task_status`, `start_time`, `end_time`)
VALUES
  ('CT_20240115_0001', '城区夜间雨天定向采集', 'PRJ_URBAN_NOA', 'BP01', 'urban_night_rain',
   240, 186, 'finished',   '2024-01-15 19:00:00.000', '2024-01-15 23:12:00.000'),
  ('CT_20240118_0002', '高速匝道汇入场景补采', 'PRJ_HIGHWAY_NOA', 'BP02', 'highway_ramp_merge',
   180, 141, 'finished',   '2024-01-18 09:30:00.000', '2024-01-18 12:41:00.000'),
  ('CT_20240203_0003', '地库泊车 corner case 采集', 'PRJ_APA', 'HX07', 'parking_underground',
   120,  64, 'running',    '2024-02-03 14:00:00.000', NULL);

-- ----------------------------------------------------------------------------
-- 2. 车辆管理系统
--    vehicle_code 是 data_id 的第二段（ids.new_data_id），必须是 [A-Z0-9]+
-- ----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `vehicle_platform`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `vehicle_platform`.`vehicle_info` (
  `vehicle_code`    VARCHAR(64) NOT NULL COMMENT '车辆编码，data_id 第二段来源',
  `vin`             VARCHAR(32)          COMMENT '车架号',
  `vehicle_model`   VARCHAR(64)          COMMENT '车型',
  `fleet_name`      VARCHAR(64)          COMMENT '所属车队',
  `autonomy_level`  VARCHAR(16)          COMMENT '智驾等级',
  `register_date`   DATE                 COMMENT '入队日期',
  `status`          VARCHAR(32)          COMMENT '车辆状态',
  PRIMARY KEY (`vehicle_code`)
) ENGINE=InnoDB COMMENT='车辆档案；CDC → ods_vehicle_info';

INSERT IGNORE INTO `vehicle_platform`.`vehicle_info`
  (`vehicle_code`, `vin`, `vehicle_model`, `fleet_name`, `autonomy_level`, `register_date`, `status`)
VALUES
  ('BP01', 'TESTVIN0000000001', 'DemoCar-Pro',  '城区采集一队', 'L2+', '2023-09-01', 'active'),
  ('BP02', 'TESTVIN0000000002', 'DemoCar-Pro',  '高速采集二队', 'L2+', '2023-09-01', 'active'),
  ('HX07', 'TESTVIN0000000007', 'DemoCar-Lite', '泊车专项队',   'L2',  '2023-11-15', 'maintenance');

-- ----------------------------------------------------------------------------
-- 3. 配置管理系统（传感器内外参）
-- ----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `config_platform`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `config_platform`.`sensor_config` (
  `vehicle_code`    VARCHAR(64) NOT NULL COMMENT '车辆编码',
  `sensor_id`       VARCHAR(64) NOT NULL COMMENT '传感器 ID',
  `sensor_type`     VARCHAR(32)          COMMENT '类型：camera/lidar/radar/imu/gps',
  `sensor_model`    VARCHAR(64)          COMMENT '型号',
  `mount_position`  VARCHAR(64)          COMMENT '安装位置',
  `intrinsic_json`  TEXT                 COMMENT '内参（JSON）',
  `extrinsic_json`  TEXT                 COMMENT '外参（JSON）',
  `sample_rate_hz`  DOUBLE               COMMENT '采样率',
  `effective_from`  DATETIME(3)          COMMENT '生效时间',
  PRIMARY KEY (`vehicle_code`, `sensor_id`)
) ENGINE=InnoDB COMMENT='传感器内外参；CDC → ods_sensor_config';

INSERT IGNORE INTO `config_platform`.`sensor_config`
  (`vehicle_code`, `sensor_id`, `sensor_type`, `sensor_model`, `mount_position`,
   `intrinsic_json`, `extrinsic_json`, `sample_rate_hz`, `effective_from`)
VALUES
  ('BP01', 'cam_front_wide', 'camera', 'DemoCam-120', 'front_windshield',
   '{"fx":1200.0,"fy":1200.0,"cx":960.0,"cy":540.0}',
   '{"x":1.52,"y":0.0,"z":1.38,"roll":0.0,"pitch":0.0,"yaw":0.0}', 30.0, '2023-09-01 00:00:00.000'),
  ('BP01', 'lidar_top',      'lidar',  'DemoLidar-128', 'roof_center',
   NULL,
   '{"x":0.0,"y":0.0,"z":1.90,"roll":0.0,"pitch":0.0,"yaw":0.0}', 10.0, '2023-09-01 00:00:00.000'),
  ('BP02', 'cam_front_wide', 'camera', 'DemoCam-120', 'front_windshield',
   '{"fx":1198.5,"fy":1198.5,"cx":960.0,"cy":540.0}',
   '{"x":1.52,"y":0.0,"z":1.38,"roll":0.0,"pitch":0.0,"yaw":0.0}', 30.0, '2023-09-01 00:00:00.000'),
  ('HX07', 'cam_avm_rear',   'camera', 'DemoFisheye-190', 'rear_bumper',
   '{"fx":420.0,"fy":420.0,"cx":640.0,"cy":360.0}',
   '{"x":-0.95,"y":0.0,"z":0.72,"roll":0.0,"pitch":-15.0,"yaw":180.0}', 25.0, '2023-11-15 00:00:00.000');

-- ----------------------------------------------------------------------------
-- 4. CDC 账号授权
--    账号来自 ingest/channels.py 的 CdcSourceConfig（CDC_MYSQL_USERNAME 默认 adas），
--    用户本体由 compose 的 MYSQL_USER / MYSQL_PASSWORD 创建，这里只补权限。
--
--    REPLICATION SLAVE / REPLICATION CLIENT 是读 binlog 的必需项；
--    RELOAD + SHOW DATABASES 供全量快照阶段用（增量快照框架无锁，但仍要这两个）。
-- ----------------------------------------------------------------------------
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT
  ON *.* TO 'adas'@'%';
FLUSH PRIVILEGES;
