-- =============================================================================
-- 闸门二 · 事件抽帧（现场勘查）—— 异步补抽
--
-- 原文二章闸门表第 2 行（逐字）：
--   触发条件：命中规则 / 主动安全触发（AEB 等）/ 驾驶员接管 / 模型低置信度
--   频率    ：事件前 15 秒 + 后 5 秒共 20 秒窗口，1 秒 1 帧（约 20 帧）
--   用途    ：事件上下文精细挖掘，可异步补抽
--
-- 原文三章：
--   窗口为什么是「前 15 后 5」不对称？"因为事件的价值主要在「它是怎么发生的」——
--   前车切入、行人闯入、信号灯变化的过程都在事件之前；事件之后的 5 秒则用来确认
--   后果（是否制动、是否绕行）。1 秒 1 帧的密度足够还原因果链，又不至于把 20 秒
--   变成 600 张全量帧。"
--
--   时序细节："事件信息本身依赖规则引擎的输出，所以事件抽帧天然是「二次抽帧」——
--   常规抽帧先行，规则引擎事后识别出事件，再回头对对应窗口补抽。这就是「异步补抽」
--   机制：调度上把事件抽帧任务挂在规则结果之后，补抽与主链路解耦，谁也不阻塞谁。"
--
-- 成本账：保留 20/600 = 1/30，过滤掉 29/30。
--
-- ★ 因此本作业**必须**与 sampling_01 分开调度：它的上游是规则引擎结果，
--   不是 clip 入湖事件。两者共用一张目标表，靠主键 image_id Upsert 去重——
--   窗口内与常规抽帧重合的时刻会算出同一个 frame_index/image_id，
--   补抽不会写出重复图，只会把事件上下文补齐到那一行上。
--
-- 依赖 UDTF：SAMPLING_PLAN_EVENT(clip_start_ms, clip_end_ms, event_ms)
--   由 adas_lakehouse.sampling.engine.register_flink_udfs 注册，与 Python 侧
--   EventGate.offsets() 是同一套逻辑。
-- =============================================================================

SET 'pipeline.name' = 'adas-sampling-gate2-event-backfill';
SET 'execution.runtime-mode' = 'streaming';
SET 'execution.checkpointing.interval' = '60s';

USE CATALOG paimon;
USE adas_lakehouse;

-- -----------------------------------------------------------------------------
-- 事件源：四类触发条件，每一类都对应一种「模型表现与预期有偏差」的信号。
-- ⚠️ 原文未明确，本项目设计：原文没说四类事件各自落在哪张表。本项目从回传域的
--    ods_vehicle_trigger_event（主动安全 / 驾驶员接管 / 低置信度）与挖掘域的规则
--    命中结果汇成一个统一视图，trigger_type 取值与 sampling.EventTriggerType 对齐。
-- -----------------------------------------------------------------------------
CREATE TEMPORARY VIEW v_sampling_events AS
SELECT
  t.`data_id`,
  CASE t.`trigger_type`
    WHEN 'aeb'             THEN 'active_safety'
    WHEN 'active_safety'   THEN 'active_safety'
    WHEN 'takeover'        THEN 'driver_takeover'
    WHEN 'driver_takeover' THEN 'driver_takeover'
    WHEN 'low_confidence'  THEN 'low_confidence'
    ELSE 'rule_hit'
  END AS `event_trigger_type`,
  t.`trigger_time` AS `event_time`,
  t.`event_id` AS `source_id`,          -- 回传域主键，catalog 登记名为 event_id
  CAST(NULL AS STRING) AS `parent_artifact_id`
FROM `ods_vehicle_trigger_event` AS t
WHERE t.`data_id` IS NOT NULL
  AND t.`trigger_time` IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 事件 × clip × 摄像头 → 20 秒窗口内 1 秒 1 帧
-- -----------------------------------------------------------------------------
CREATE TEMPORARY VIEW v_event_clip_cameras AS
SELECT
  c.`data_id`,
  c.`collect_start_time`,
  c.`duration_sec`,
  c.`project_code`,
  c.`vehicle_code`,
  c.`gps_start_lat`,
  c.`gps_start_lon`,
  LOWER(s.`sensor_id`) AS `camera_id`,
  e.`event_trigger_type`,
  e.`event_time`,
  e.`parent_artifact_id`,
  -- 事件前 15 秒 + 后 5 秒 = 20 秒窗口（原文三章，逐字）
  TIMESTAMPADD(SECOND, -15, e.`event_time`) AS `event_window_start`,
  TIMESTAMPADD(SECOND,   5, e.`event_time`) AS `event_window_end`,
  UNIX_TIMESTAMP(DATE_FORMAT(c.`collect_start_time`, 'yyyy-MM-dd HH:mm:ss')) * 1000 AS `clip_start_ms`,
  UNIX_TIMESTAMP(DATE_FORMAT(c.`collect_start_time`, 'yyyy-MM-dd HH:mm:ss')) * 1000
    + CAST(c.`duration_sec` * 1000 AS BIGINT) AS `clip_end_ms`,
  UNIX_TIMESTAMP(DATE_FORMAT(e.`event_time`, 'yyyy-MM-dd HH:mm:ss')) * 1000 AS `event_ms`
FROM v_sampling_events AS e
JOIN `dwd_collect_clip_detail` AS c
  ON c.`data_id` = e.`data_id`
JOIN `ods_sensor_config` AS s
  ON s.`vehicle_code` = c.`vehicle_code`
 AND s.`sensor_type` = 'camera'
-- ★ 抽帧前置脱敏校验同样适用于补抽——合规红线没有「补抽豁免」这一说
WHERE c.`compliance_status` = 'double_desensitized'
  AND c.`upload_status` = 'success'
  AND c.`duration_sec` > 0;

INSERT INTO `dwd_mining_image_frame_detail`
SELECT
  CONCAT(v.`data_id`, '_', v.`camera_id`, '_F', LPAD(CAST(p.`frame_index` AS STRING), 6, '0')) AS `image_id`,
  v.`data_id`,
  v.`camera_id`,
  p.`frame_index`,
  CAST(TO_TIMESTAMP_LTZ(v.`clip_start_ms` + p.`clip_offset_ms`, 3) AS TIMESTAMP(3)) AS `frame_timestamp`,
  v.`gps_start_lat` AS `gps_lat`,
  v.`gps_start_lon` AS `gps_lon`,
  CONCAT('frames/', v.`data_id`, '/', v.`camera_id`, '/', LPAD(CAST(p.`frame_index` AS STRING), 6, '0'), '.jpg') AS `file_path`,
  CAST(NULL AS DOUBLE) AS `frame_quality_score`,
  CAST(NULL AS STRING) AS `artifact_id`,
  v.`parent_artifact_id`,
  CAST(NULL AS STRING) AS `run_id`,
  'active' AS `artifact_status`,
  v.`project_code`,
  v.`vehicle_code`,
  CONCAT(v.`data_id`, '_G', LPAD(CAST(p.`clip_offset_ms` AS STRING), 9, '0')) AS `frame_group_id`,
  CAST(NULL AS BIGINT) AS `file_size_bytes`,
  CAST(NULL AS INT) AS `image_width`,
  CAST(NULL AS INT) AS `image_height`,
  p.`clip_offset_ms`,
  'event' AS `sampling_tier`,
  1.0 AS `sampling_interval_sec`,                  -- 原文：1 秒 1 帧
  v.`event_trigger_type`,
  v.`event_time`,
  v.`event_window_start`,                          -- 事件前 15 秒
  v.`event_window_end`,                            -- 事件后 5 秒
  CAST(NULL AS DOUBLE) AS `object_richness_score`,
  CAST(NULL AS DOUBLE) AS `temporal_position_score`,
  CAST(NULL AS DOUBLE) AS `keyframe_score`,
  FALSE AS `is_keyframe`,
  'double_desensitized' AS `desensitization_status`,
  'v1' AS `algo_version`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  CURRENT_TIMESTAMP AS `update_time`
FROM v_event_clip_cameras AS v,
LATERAL TABLE(
  SAMPLING_PLAN_EVENT(v.`clip_start_ms`, v.`clip_end_ms`, v.`event_ms`)
) AS p(`frame_index`, `clip_offset_ms`);
