-- =============================================================================
-- 分层抽帧 · StarRocks 侧：帧表的直查、检索与三道闸门成本对账
--
-- 来源：系列三 · 数据挖掘与 AI 第 2 篇《分层抽帧策略：从 TB 级采集数据中提取
--       高价值帧：三道成本闸门》（2026-09-10）
--       https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ
--
-- 双路查询（沿用系列二的既定架构，见 config.StarRocksConfig）：
--   ① External Catalog 直查 Paimon——不搬数据，适合全量扫描与对账；
--   ② 内表物化——毫秒级直查，适合检索页「按视角过滤、按层级过滤、只看关键帧」。
--
-- 原文五章第 1 个实现要点给检索定了两个必须支持的动作：
--   "逐图保留 camera_id——检索时可按视角过滤，训练时可按视角组合"
-- 所以内表的排序键把 camera_id 放在前排。
--
-- 连接默认值对齐 config.settings().starrocks：
--   fe_host=localhost, query_port=18630, external_catalog=paimon_catalog,
--   internal_database=adas_ads
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 一、External Catalog：直查 Paimon 帧表
-- -----------------------------------------------------------------------------
CREATE EXTERNAL CATALOG IF NOT EXISTS paimon_catalog
PROPERTIES (
  'type' = 'paimon',
  'paimon.catalog.type' = 'filesystem',
  'paimon.catalog.warehouse' = 's3://adas-lakehouse/warehouse',
  'aws.s3.enable_path_style_access' = 'true',
  'aws.s3.endpoint' = 'http://localhost:18600',
  'aws.s3.access_key' = 'adas',
  'aws.s3.secret_key' = 'adas-secret'
);

CREATE DATABASE IF NOT EXISTS adas_ads;

-- -----------------------------------------------------------------------------
-- 二、内表物化：帧检索表（毫秒级直查）
--
-- 主键 image_id 与湖仓一致——image_id 内嵌 data_id，免查表即可回溯采集单元。
-- 排序键 (camera_id, sampling_tier, is_keyframe) 直接服务检索页的三个过滤器。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adas_ads.ads_mining_image_frame_search (
  `image_id`                VARCHAR(128)  NOT NULL COMMENT '帧图片 ID，内嵌 data_id',
  `camera_id`               VARCHAR(64)   NOT NULL COMMENT '摄像头视角，检索按视角过滤',
  `sampling_tier`           VARCHAR(16)   NOT NULL COMMENT '产出该帧的闸门：routine/event',
  `is_keyframe`             BOOLEAN       NOT NULL COMMENT '是否被推理抽帧选中（每 clip 1~5 张）',
  `data_id`                 VARCHAR(128)  NOT NULL COMMENT '所属 clip 的终身锚点',
  `artifact_id`             VARCHAR(256)  NULL     COMMENT '二级 ID：本帧产物',
  `parent_artifact_id`      VARCHAR(256)  NULL     COMMENT '血缘父产物',
  `run_id`                  VARCHAR(128)  NULL     COMMENT '三级 ID：本次抽帧运行',
  `artifact_status`         VARCHAR(16)   NULL     COMMENT 'active/superseded/invalid',
  `project_code`            VARCHAR(64)   NULL     COMMENT '所属项目',
  `vehicle_code`            VARCHAR(32)   NULL     COMMENT '车辆编码',
  `frame_group_id`          VARCHAR(160)  NULL     COMMENT '多路同步组：同一时刻的多路图片为一组样本',
  `frame_index`             INT           NULL     COMMENT '帧序号（原生 30fps 帧号）',
  `frame_timestamp`         DATETIME      NULL     COMMENT '帧时间戳',
  `clip_offset_ms`          BIGINT        NULL     COMMENT '相对 clip 起点的偏移（毫秒）',
  `gps_lat`                 DOUBLE        NULL     COMMENT 'GPS 纬度',
  `gps_lon`                 DOUBLE        NULL     COMMENT 'GPS 经度',
  `file_path`               VARCHAR(512)  NULL     COMMENT '帧图片对象存储路径',
  `sampling_interval_sec`   DOUBLE        NULL     COMMENT '常规 2 秒 1 帧 / 事件 1 秒 1 帧',
  `event_trigger_type`      VARCHAR(32)   NULL     COMMENT 'rule_hit/active_safety/driver_takeover/low_confidence',
  `event_time`              DATETIME      NULL     COMMENT '事件时刻（窗口中心）',
  `event_window_start`      DATETIME      NULL     COMMENT '事件前 15 秒',
  `event_window_end`        DATETIME      NULL     COMMENT '事件后 5 秒',
  `frame_quality_score`     DOUBLE        NULL     COMMENT '图像清晰度分',
  `object_richness_score`   DOUBLE        NULL     COMMENT '目标丰富度分',
  `temporal_position_score` DOUBLE        NULL     COMMENT '时间位置分',
  `keyframe_score`          DOUBLE        NULL     COMMENT '三维加权综合分',
  `desensitization_status`  VARCHAR(32)   NULL     COMMENT '双脱敏校验结论',
  `algo_version`            VARCHAR(32)   NULL     COMMENT '抽帧/打分算法版本',
  `update_time`             DATETIME      NULL     COMMENT '业务更新时间'
)
PRIMARY KEY (`image_id`, `camera_id`, `sampling_tier`, `is_keyframe`)
COMMENT '抽帧图片检索表（三道成本闸门产物的毫秒级直查副本）'
DISTRIBUTED BY HASH(`image_id`) BUCKETS 16
ORDER BY (`camera_id`, `sampling_tier`, `is_keyframe`)
PROPERTIES (
  'replication_num' = '1',
  'enable_persistent_index' = 'true'
);

-- 从 Paimon 灌入（按需全量或增量）
-- INSERT INTO adas_ads.ads_mining_image_frame_search
-- SELECT
--   image_id, camera_id, sampling_tier, is_keyframe, data_id, artifact_id,
--   parent_artifact_id, run_id, artifact_status, project_code, vehicle_code,
--   frame_group_id, frame_index, frame_timestamp, clip_offset_ms, gps_lat, gps_lon,
--   file_path, sampling_interval_sec, event_trigger_type, event_time,
--   event_window_start, event_window_end, frame_quality_score, object_richness_score,
--   temporal_position_score, keyframe_score, desensitization_status, algo_version,
--   update_time
-- FROM paimon_catalog.adas_lakehouse.dwd_mining_image_frame_detail
-- WHERE artifact_status = 'active';

-- =============================================================================
-- 三、三道闸门的成本对账视图
--
-- 原文二章："抽帧的每一层策略，本质上是一道成本闸门。频率越高，覆盖越细，
--            但存储、算力与下游推理成本同步放大。"
--
-- 原文里的应然比例（全部由原文数字精确相除得到，未做四舍五入）：
--   闸门一 常规抽帧：2 秒 1 帧 ÷ 30 fps       = 1/60  ≈ 0.016667
--   闸门二 事件抽帧：约 20 帧 ÷ 600 张全量帧   = 1/30  ≈ 0.033333
--   闸门三 推理抽帧：1~5 关键帧 ÷ 每 clip 30 帧 = 1/30 ~ 1/6
--
-- ⚠️ 原文没有给任何耗时或金额数字，所以这里的对账只用帧数，不编造单价。
-- =============================================================================

CREATE VIEW IF NOT EXISTS adas_ads.v_sampling_gate_cost AS
SELECT
  f.`data_id`,
  c.`duration_sec`,
  COUNT(DISTINCT f.`camera_id`)                                      AS camera_count,
  -- 全量抽帧基线：30 fps × 时长 × 路数（30 来自原文「10 秒 30 帧就是 300 张图」）
  CAST(30 * c.`duration_sec` * COUNT(DISTINCT f.`camera_id`) AS BIGINT) AS full_frames,
  SUM(CASE WHEN f.`sampling_tier` = 'routine' THEN 1 ELSE 0 END)     AS gate1_routine_frames,
  SUM(CASE WHEN f.`sampling_tier` = 'event'   THEN 1 ELSE 0 END)     AS gate2_event_frames,
  SUM(CASE WHEN f.`event_trigger_type` IS NOT NULL
                AND f.`event_trigger_type` <> '' THEN 1 ELSE 0 END)  AS gate2_event_covered_frames,
  SUM(CASE WHEN f.`is_keyframe` THEN 1 ELSE 0 END)                   AS gate3_vlm_frames,
  COUNT(*)                                                           AS lake_frames,
  -- 三道闸门合起来把湖仓压到了几分之一
  COUNT(*) / NULLIF(30 * c.`duration_sec` * COUNT(DISTINCT f.`camera_id`), 0) AS overall_keep_ratio,
  -- 最贵的 GPU 那档压到了几分之一
  SUM(CASE WHEN f.`is_keyframe` THEN 1 ELSE 0 END)
    / NULLIF(30 * c.`duration_sec` * COUNT(DISTINCT f.`camera_id`), 0)        AS vlm_keep_ratio
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_frame_detail AS f
JOIN paimon_catalog.adas_lakehouse.dwd_collect_clip_detail AS c
  ON c.`data_id` = f.`data_id`
WHERE f.`artifact_status` = 'active'
GROUP BY f.`data_id`, c.`duration_sec`;

-- -----------------------------------------------------------------------------
-- 四、事件窗口覆盖视图：核对「前 15 后 5 共 20 秒、1 秒 1 帧、约 20 帧」
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS adas_ads.v_sampling_event_window AS
SELECT
  `data_id`,
  `camera_id`,
  `event_trigger_type`,
  `event_time`,
  `event_window_start`,
  `event_window_end`,
  TIMESTAMPDIFF(SECOND, `event_window_start`, `event_time`) AS pre_seconds,   -- 应为 15
  TIMESTAMPDIFF(SECOND, `event_time`, `event_window_end`)   AS post_seconds,  -- 应为 5
  TIMESTAMPDIFF(SECOND, `event_window_start`, `event_window_end`) AS window_seconds,  -- 应为 20
  COUNT(*) AS frames_in_window,                                               -- 应约为 20
  -- 同一 20 秒窗口全量抽帧会是 600 张（原文：「不至于把 20 秒变成 600 张全量帧」）
  600 AS full_frames_in_window,
  COUNT(*) / 600.0 AS window_keep_ratio                                       -- 应约为 1/30
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_frame_detail
WHERE `artifact_status` = 'active'
  AND `event_time` IS NOT NULL
GROUP BY `data_id`, `camera_id`, `event_trigger_type`,
         `event_time`, `event_window_start`, `event_window_end`;

-- -----------------------------------------------------------------------------
-- 五、多路摄像头同步组完整性：同一时刻的多路图片是否齐全
-- 原文五章："同一时刻的多路图片作为一组样本……训练时可按视角组合"
-- 组不齐的样本不能用于多视角训练，这个视图是训练集圈选前的体检表。
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS adas_ads.v_sampling_camera_group AS
SELECT
  `frame_group_id`,
  `data_id`,
  MIN(`frame_timestamp`)         AS captured_at,
  COUNT(DISTINCT `camera_id`)    AS camera_count,
  GROUP_CONCAT(DISTINCT `camera_id`) AS camera_ids,
  SUM(CASE WHEN `is_keyframe` THEN 1 ELSE 0 END) AS keyframe_count
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_frame_detail
WHERE `artifact_status` = 'active'
  AND `frame_group_id` IS NOT NULL
GROUP BY `frame_group_id`, `data_id`;

-- -----------------------------------------------------------------------------
-- 六、合规兜底核查：帧表里绝不该出现未通过双脱敏校验的行
-- 原文五章："未脱敏数据一律拒绝抽帧——合规红线在挖掘侧再设一道闸。"
-- 这条查询的正确结果永远是 0 行；非 0 即事故。
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS adas_ads.v_sampling_compliance_violation AS
SELECT
  `data_id`,
  `image_id`,
  `desensitization_status`,
  `_ingest_time`
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_frame_detail
WHERE `desensitization_status` IS NULL
   OR `desensitization_status` <> 'double_desensitized';
