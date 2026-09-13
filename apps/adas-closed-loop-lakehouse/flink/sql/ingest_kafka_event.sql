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
  `event_id` STRING NOT NULL COMMENT '触发事件 ID（车端生成，Kafka 消息业务唯一键）',
  `trigger_type` STRING NOT NULL COMMENT '触发类型：takeover/aeb/hard_brake/rule_hit/corner_case（分区字段）',
  `data_id` STRING COMMENT '回传片段对应 clip 的 data_id，车端随文件生成，回传数据的终身锚点',
  `vehicle_code` STRING COMMENT '触发车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `software_version` STRING COMMENT '触发时车端软件版本（部署域交接标识）',
  `trigger_rule_id` STRING COMMENT '命中的云端下发触发规则 ID',
  `trigger_time` TIMESTAMP(3) COMMENT '车端触发时刻',
  `gps_lat` DOUBLE COMMENT '触发点纬度',
  `gps_lon` DOUBLE COMMENT '触发点经度',
  `vehicle_speed_kph` DOUBLE COMMENT '触发时车速（km/h）',
  `road_type` STRING COMMENT '道路类型',
  `weather` STRING COMMENT '天气',
  `light_condition` STRING COMMENT '光照条件',
  `clip_duration_sec` DOUBLE COMMENT '回传片段时长（秒）',
  `upload_status` STRING COMMENT '回传上云状态',
  `kafka_offset` BIGINT COMMENT 'Kafka 消息位点，支持事件回放不丢数',
  `pre_trigger_seconds` DOUBLE COMMENT '回传片段覆盖触发前的秒数。对应 QG-KFK-003 片段截断：覆盖触发前 ≥ N 秒，不足则标记 truncated_flag 告警放行（N 见 thresholds.TRIGGER_PRE_SECONDS_DEFAULT）',
  `post_trigger_seconds` DOUBLE COMMENT '回传片段覆盖触发后的秒数。对应 QG-KFK-004 片段截断：覆盖触发后 ≥ M 秒，不足则标记 truncated_flag 告警放行（M 见 thresholds.TRIGGER_POST_SECONDS_DEFAULT）',
  `duplicate_rate` DOUBLE COMMENT '所属批次的事件重复率（0~1，批级统计量，随记录落表便于复盘）。对应 QG-KFK-006 重复率监控：事件 ID 幂等去重后重复率 > 5% 告警',
  `gps_jump_meters` DOUBLE COMMENT '相对上一采样点的定位跳变距离（米）。对应 QG-KFK-008 定位跳变：六维之准确性「定位无跳变」，超 100 米 P2 告警标记放行。⚠️ 原文未明确，本项目设计：原文只写「定位无跳变」这一句定性要求，既没给度量也没给米数；本项目落成「相对上一采样点的跳变距离」这一标量列',
  `vehicle_manufacture_time` TIMESTAMP(3) COMMENT '车辆出厂时间（随事件冗余落表，避免门禁为一条规则去 join 车辆档案）。QG-KFK-002 时间戳合理性的下界：trigger_time 早于出厂时间即不合理，上界是服务器时间 + 车端时钟漂移容忍窗（thresholds.CLOCK_DRIFT_TOLERANCE_SECONDS）'
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
-- ⚠️ 原文未明确，本项目设计：未来容忍 300 秒（车端与云端时钟漂移）、滞后容忍 30 天（车端离线缓存后补传）；两个数取自 constants，与 channels.KafkaChannel 同源
-- 分区字段 trigger_type 非空：分区表主键必须包含分区字段，空值会落进 __DEFAULT_PARTITION__
-- 不满足的事件走隔离分支，写法见 ingest_oss_file_meta.sql 的 STATEMENT SET
INSERT INTO `paimon`.`adas_lakehouse`.`ods_vehicle_trigger_event`
SELECT
  `event_id`,
  `trigger_type`,
  `data_id`,
  `vehicle_code`,
  `project_code`,
  `software_version`,
  `trigger_rule_id`,
  `trigger_time`,
  `gps_lat`,
  `gps_lon`,
  `vehicle_speed_kph`,
  `road_type`,
  `weather`,
  `light_condition`,
  `clip_duration_sec`,
  `upload_status`,
  `kafka_offset`,
  `pre_trigger_seconds`,
  `post_trigger_seconds`,
  `duplicate_rate`,
  `gps_jump_meters`,
  `vehicle_manufacture_time`,
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
  `event_type` STRING NOT NULL COMMENT '事件类型：stage_start/stage_end/stage_fail/upload_done/deliver_done',
  `data_id` STRING COMMENT '关联数据单元 data_id',
  `artifact_id` STRING COMMENT '关联产物 artifact_id',
  `run_id` STRING COMMENT '关联处理运行 run_id',
  `line_task_id` STRING COMMENT '关联产线任务 ID',
  `stage_code` STRING COMMENT '产线环节编码',
  `event_status` STRING COMMENT '事件携带的状态值',
  `event_time` TIMESTAMP(3) COMMENT '事件发生时间',
  `producer_platform` STRING COMMENT '事件产生平台',
  `kafka_topic` STRING COMMENT '来源 Kafka topic',
  `kafka_partition` INT COMMENT 'Kafka 分区号，支持按位点回放',
  `kafka_offset` BIGINT COMMENT 'Kafka 位点',
  `trace_id` STRING COMMENT '链路追踪 ID',
  `payload_json` STRING COMMENT '原始事件体（JSON）'
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
-- ⚠️ 原文未明确，本项目设计：未来容忍 300 秒（车端与云端时钟漂移）、滞后容忍 30 天（车端离线缓存后补传）；两个数取自 constants，与 channels.KafkaChannel 同源
-- 分区字段 event_type 非空：分区表主键必须包含分区字段，空值会落进 __DEFAULT_PARTITION__
-- 不满足的事件走隔离分支，写法见 ingest_oss_file_meta.sql 的 STATEMENT SET
INSERT INTO `paimon`.`adas_lakehouse`.`ods_production_kafka_event`
SELECT
  `event_id`,
  `event_type`,
  `data_id`,
  `artifact_id`,
  `run_id`,
  `line_task_id`,
  `stage_code`,
  `event_status`,
  `event_time`,
  `producer_platform`,
  `kafka_topic`,
  `kafka_partition`,
  `kafka_offset`,
  `trace_id`,
  `payload_json`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  '产线埋点' AS `_source_system`
FROM `kafka_src_ods_production_kafka_event`
WHERE `event_time` IS NOT NULL
  AND `event_time` <= TIMESTAMPADD(SECOND, 300, CURRENT_TIMESTAMP)
  AND `event_time` >= TIMESTAMPADD(DAY, -30, CURRENT_TIMESTAMP)
  AND `event_type` IS NOT NULL AND `event_type` <> '';
