-- ========================================================================
-- 通道二 · Kafka：事件流 → ODS
-- 由 adas_lakehouse.ingest.sql 生成，请勿手工编辑。
-- 产线埋点、训练指标、车端触发事件各自独立 Topic，保留回放能力。
-- ========================================================================


-- 通道二 · Kafka：vehicle.trigger.event → ods_vehicle_trigger_event
-- 承接数据：事件流（产线埋点 / 训练指标 / 车端触发）；入湖方式：Flink 实时消费
-- 回放：各自独立 Topic，消费失败可从上次位点重新消费，不丢事件
--   重放历史 → 'scan.startup.mode' = 'timestamp' + 'scan.startup.timestamp-millis' = '...'
--   精确回放 → 'scan.startup.mode' = 'specific-offsets' + 'scan.startup.specific-offsets' = 'partition:0,offset:42'
-- ODS 只做原样入湖、不做窗口聚合，因此不声明 WATERMARK
CREATE TEMPORARY TABLE `kafka_src_ods_vehicle_trigger_event` (
  `event_id` STRING NOT NULL COMMENT '触发事件 ID',
  `trigger_type` STRING NOT NULL COMMENT '触发类型（分区字段）',
  `data_id` STRING COMMENT '关联 clip 的 data_id',
  `vehicle_code` STRING COMMENT '车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `trigger_time` TIMESTAMP(3) COMMENT '触发时间',
  `gps_lat` DOUBLE COMMENT '触发点纬度',
  `gps_lon` DOUBLE COMMENT '触发点经度',
  `speed_kph` DOUBLE COMMENT '触发时车速',
  `scene_tag` STRING COMMENT '场景标签',
  `model_version` STRING COMMENT '车端模型版本',
  `upload_status` STRING COMMENT '回传状态'
) WITH (
  'connector' = 'kafka',
  'topic' = 'vehicle.trigger.event',
  'properties.bootstrap.servers' = 'localhost:18692',
  'properties.group.id' = 'adas-lakehouse-ods_vehicle_trigger_event',
  'scan.startup.mode' = 'group-offsets',
  'properties.auto.offset.reset' = 'earliest',
  'format' = 'json',
  'json.ignore-parse-errors' = 'false',
  'json.fail-on-missing-field' = 'false'
);

-- 分源门禁：Kafka 通道查「时空合理」
-- ⚠️ 原文未明确，本项目设计：未来容忍 300 秒（车端与云端时钟漂移）、滞后容忍 30 天（车端离线缓存后补传）
-- 分区字段 trigger_type 非空：分区表主键必须包含分区字段，空值会落进 __DEFAULT_PARTITION__
-- 不满足的事件走隔离分支，写法见 ingest_oss_file_meta.sql 的 STATEMENT SET
INSERT INTO `paimon`.`adas_lakehouse`.`ods_vehicle_trigger_event`
SELECT
  `event_id`,
  `trigger_type`,
  `data_id`,
  `vehicle_code`,
  `project_code`,
  `trigger_time`,
  `gps_lat`,
  `gps_lon`,
  `speed_kph`,
  `scene_tag`,
  `model_version`,
  `upload_status`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  '车云平台' AS `_source_system`
FROM `kafka_src_ods_vehicle_trigger_event`
WHERE `trigger_time` IS NOT NULL
  AND `trigger_time` <= TIMESTAMPADD(SECOND, 300, CURRENT_TIMESTAMP)
  AND `trigger_time` >= TIMESTAMPADD(DAY, -30, CURRENT_TIMESTAMP)
  AND (`gps_lat` IS NULL OR `gps_lat` BETWEEN -90 AND 90)
  AND (`gps_lon` IS NULL OR `gps_lon` BETWEEN -180 AND 180)
  AND `trigger_type` IS NOT NULL AND `trigger_type` <> '';

-- 通道二 · Kafka：production.event → ods_production_kafka_event
-- 承接数据：事件流（产线埋点 / 训练指标 / 车端触发）；入湖方式：Flink 实时消费
-- 回放：各自独立 Topic，消费失败可从上次位点重新消费，不丢事件
--   重放历史 → 'scan.startup.mode' = 'timestamp' + 'scan.startup.timestamp-millis' = '...'
--   精确回放 → 'scan.startup.mode' = 'specific-offsets' + 'scan.startup.specific-offsets' = 'partition:0,offset:42'
-- ODS 只做原样入湖、不做窗口聚合，因此不声明 WATERMARK
CREATE TEMPORARY TABLE `kafka_src_ods_production_kafka_event` (
  `event_id` STRING NOT NULL COMMENT '事件 ID',
  `event_type` STRING NOT NULL COMMENT '事件类型（分区字段）',
  `data_id` STRING COMMENT '关联 clip 的 data_id',
  `artifact_id` STRING COMMENT '二级 ID：处理产物',
  `run_id` STRING COMMENT '三级 ID：处理运行',
  `stage` STRING COMMENT '产线环节',
  `event_time` TIMESTAMP(3) COMMENT '事件时间',
  `event_status` STRING COMMENT '事件状态',
  `payload_json` STRING COMMENT '事件原始载荷（原样入湖）'
) WITH (
  'connector' = 'kafka',
  'topic' = 'production.event',
  'properties.bootstrap.servers' = 'localhost:18692',
  'properties.group.id' = 'adas-lakehouse-ods_production_kafka_event',
  'scan.startup.mode' = 'group-offsets',
  'properties.auto.offset.reset' = 'earliest',
  'format' = 'json',
  'json.ignore-parse-errors' = 'false',
  'json.fail-on-missing-field' = 'false'
);

-- 分源门禁：Kafka 通道查「时空合理」
-- ⚠️ 原文未明确，本项目设计：未来容忍 300 秒（车端与云端时钟漂移）、滞后容忍 30 天（车端离线缓存后补传）
-- 分区字段 event_type 非空：分区表主键必须包含分区字段，空值会落进 __DEFAULT_PARTITION__
-- 不满足的事件走隔离分支，写法见 ingest_oss_file_meta.sql 的 STATEMENT SET
INSERT INTO `paimon`.`adas_lakehouse`.`ods_production_kafka_event`
SELECT
  `event_id`,
  `event_type`,
  `data_id`,
  `artifact_id`,
  `run_id`,
  `stage`,
  `event_time`,
  `event_status`,
  `payload_json`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  '产线埋点' AS `_source_system`
FROM `kafka_src_ods_production_kafka_event`
WHERE `event_time` IS NOT NULL
  AND `event_time` <= TIMESTAMPADD(SECOND, 300, CURRENT_TIMESTAMP)
  AND `event_time` >= TIMESTAMPADD(DAY, -30, CURRENT_TIMESTAMP)
  AND `event_type` IS NOT NULL AND `event_type` <> '';
