-- =============================================================================
-- 闸门一 · 常规抽帧（普查）—— 流模式（Flink，用于新入湖数据实时抽帧）
--
-- 原文二章闸门表第 1 行（逐字）：
--   触发条件：全量 clip
--   频率    ：默认 2 秒 1 帧
--   用途    ：基础场景覆盖，支撑标签统计与粗粒度检索
--
-- 成本账：保留 1/60 = (2 秒 1 帧 → 0.5 fps) ÷ (原文「10 秒 30 帧」→ 30 fps)，
--         过滤掉 59/60。这是三道闸门里唯一无条件对全量数据执行的一道。
--
-- 原文一章的边界：平台不碰接入链路，只消费产出——本作业的输入只有湖仓里已经
-- 走完「车端脱敏 → 合规云脱密 → 智驾云入湖」的合规数据。
--
-- 原文五章第 4 个实现要点：抽帧前置脱敏校验，未脱敏数据一律拒绝抽帧。
-- 下面的 WHERE 子句就是这道闸在 SQL 侧的实现——它必须是过滤而不是标记。
--
-- 依赖 UDTF（由 Python 侧注册，保证批流共用同一套抽帧逻辑）：
--   from adas_lakehouse.sampling import register_flink_udfs
--   register_flink_udfs(t_env)
-- =============================================================================

SET 'pipeline.name' = 'adas-sampling-gate1-routine';
SET 'execution.runtime-mode' = 'streaming';
SET 'execution.checkpointing.interval' = '60s';

USE CATALOG paimon;
USE adas_lakehouse;

-- -----------------------------------------------------------------------------
-- 摄像头清单：多路摄像头同步要求逐图保留 camera_id（原文五章第 1 个实现要点）
-- 「智驾车同一时刻有前视、侧视、后视等多路摄像头，同一时刻的多路图片作为一组样本」
-- -----------------------------------------------------------------------------
CREATE TEMPORARY VIEW v_clip_cameras AS
SELECT
  c.`data_id`,
  c.`collect_start_time`,
  c.`duration_sec`,
  c.`project_code`,
  c.`vehicle_code`,
  c.`gps_start_lat`,
  c.`gps_start_lon`,
  LOWER(s.`sensor_id`) AS `camera_id`
FROM `dwd_collect_clip_detail` AS c
JOIN `ods_sensor_config` AS s
  ON s.`vehicle_code` = c.`vehicle_code`
 AND s.`sensor_type` = 'camera'
-- ★ 抽帧前置脱敏校验：未脱敏一律拒绝抽帧（合规红线在挖掘侧再设一道闸）
-- ⚠️ 原文未明确，本项目设计：原文只说「复用合规链路的脱敏标记」，没给标记字面量。
--    这里约定 compliance_status = 'double_desensitized' 表示车端脱敏 + 合规云脱密
--    两道都已完成；各项目若用别的字面量，在此处改映射，不要改下游逻辑。
WHERE c.`compliance_status` = 'double_desensitized'
  AND c.`upload_status` = 'success'
  AND c.`duration_sec` > 0;

-- -----------------------------------------------------------------------------
-- 常规抽帧：默认 2 秒 1 帧
-- SAMPLING_PLAN_ROUTINE(duration_sec) 展开出 (frame_index, clip_offset_ms)，
-- frame_index = ROUND(偏移秒数 × 30) 即原生帧号——闸门二按 1 秒 1 帧抽到同一时刻时
-- 会得到同一个 frame_index，也就是同一个 image_id，主键 Upsert 天然去重。
-- -----------------------------------------------------------------------------
INSERT INTO `dwd_mining_image_frame_detail`
SELECT
  -- image_id 内嵌 data_id，免查表即可回溯采集单元（原文一章、五章）
  CONCAT(v.`data_id`, '_', v.`camera_id`, '_F', LPAD(CAST(p.`frame_index` AS STRING), 6, '0')) AS `image_id`,
  v.`data_id`,
  v.`camera_id`,
  p.`frame_index`,
  CAST(TO_TIMESTAMP_LTZ(
        UNIX_TIMESTAMP(DATE_FORMAT(v.`collect_start_time`, 'yyyy-MM-dd HH:mm:ss')) * 1000
        + p.`clip_offset_ms`, 3) AS TIMESTAMP(3)) AS `frame_timestamp`,
  v.`gps_start_lat` AS `gps_lat`,
  v.`gps_start_lon` AS `gps_lon`,
  CONCAT('frames/', v.`data_id`, '/', v.`camera_id`, '/', LPAD(CAST(p.`frame_index` AS STRING), 6, '0'), '.jpg') AS `file_path`,
  CAST(NULL AS DOUBLE) AS `frame_quality_score`,   -- 由打分作业回填，见 sampling_03
  CAST(NULL AS STRING) AS `artifact_id`,           -- 由 Python 侧按三级 ID 规则派生
  CAST(NULL AS STRING) AS `parent_artifact_id`,
  CAST(NULL AS STRING) AS `run_id`,
  'active' AS `artifact_status`,
  v.`project_code`,
  v.`vehicle_code`,
  -- 多路同步组：同一 clip_offset_ms 的多路图片同组
  CONCAT(v.`data_id`, '_G', LPAD(CAST(p.`clip_offset_ms` AS STRING), 9, '0')) AS `frame_group_id`,
  CAST(NULL AS BIGINT) AS `file_size_bytes`,
  CAST(NULL AS INT) AS `image_width`,
  CAST(NULL AS INT) AS `image_height`,
  p.`clip_offset_ms`,
  'routine' AS `sampling_tier`,
  2.0 AS `sampling_interval_sec`,                  -- 原文：默认 2 秒 1 帧
  CAST(NULL AS STRING) AS `event_trigger_type`,
  CAST(NULL AS TIMESTAMP(3)) AS `event_time`,
  CAST(NULL AS TIMESTAMP(3)) AS `event_window_start`,
  CAST(NULL AS TIMESTAMP(3)) AS `event_window_end`,
  CAST(NULL AS DOUBLE) AS `object_richness_score`,
  CAST(NULL AS DOUBLE) AS `temporal_position_score`,
  CAST(NULL AS DOUBLE) AS `keyframe_score`,
  FALSE AS `is_keyframe`,
  'double_desensitized' AS `desensitization_status`,
  'v1' AS `algo_version`,
  CURRENT_TIMESTAMP AS `_ingest_time`,
  CURRENT_TIMESTAMP AS `update_time`
FROM v_clip_cameras AS v,
LATERAL TABLE(SAMPLING_PLAN_ROUTINE(v.`duration_sec`)) AS p(`frame_index`, `clip_offset_ms`);
