-- =============================================================================
-- 闸门三 · 推理抽帧（重点取证）—— 用打分代替随机选帧
--
-- 原文二章闸门表第 3 行（逐字）：
--   触发条件：进入 VLM 推理范围的 clip
--   频率    ：每 clip 打分选 1~5 关键帧
--   用途    ：按清晰度/目标丰富度/时间位置选帧，控制推理成本
--
-- 原文四章：
--   "GPU 推理成本与送进去的图片数量成正比，送全量帧既不经济也无必要（相邻帧高度相似）。"
--   "每个 clip 只选 1~5 张关键帧，选谁由打分决定，三个维度：
--      图像清晰度——模糊、过曝、遮挡的帧直接降权，对应帧表里的 frame_quality_score；
--      目标丰富度——画面里车辆、行人、交通设施越多，语义信息量越大；
--      时间位置——事件窗口中心、场景切换时刻的帧优先。"
--   "frame_quality_score 作为帧表字段落湖，选帧逻辑调整时可以重算分数重新圈选，
--     历史推理结果也能按当时的分数复盘。"
--   "结合上一篇讲的向量化成本分级——规则命中、事件抽帧产出的帧优先进 VLM，普通帧抽样
--     ——推理预算始终流向信息密度最高的图片。"
--
-- ★ 这道闸门**不产生新图片**：它在闸门一、二已落湖的帧里挑，只把 is_keyframe 置 TRUE。
--   所以它削的是 GPU 成本，不是存储成本；extract_level 保持产出该帧的闸门不变。
--
-- 三个维度分由打分作业（Python 侧 adas_lakehouse.sampling.scoring，接 CV 模型）
-- 回填到 frame_quality_score / object_richness_score / temporal_position_score，
-- 本 SQL 只做加权汇总与 Top-K 圈选——这样「选帧逻辑调整时可以重算分数重新圈选」。
--
-- ★ 列名以 catalog/tables/_mining.py（经 catalog.registry 聚合）为唯一事实源：
--   帧表里闸门归属是 `extract_level`、事件触发类型是 `trigger_event_type`、
--   clip 内偏移是 `frame_offset_sec`（**秒**，不是毫秒）、图片路径是 `image_object_key`。
--   sampling 子系统内存侧的 sampling_tier / event_trigger_type / clip_offset_ms
--   只活在 Python 进程里，落湖前由 sampling/table.py 的 FRAME_COLUMN_MAP 翻译。
--
-- ⚠️ 原文未明确，本项目设计（常量出处 sampling/constants.py）：
--   · 三个维度权重默认三等分（1/3 each）——原文只列了维度，没给权重；
--   · 综合分入选下限 0.5——原文只说选 1~5 张，没说怎么决定选几张；
--   · 关键帧最小间距 1 秒——原文说「相邻帧高度相似」但没给去重间隔；
--   · 多路摄像头场景下按 camera_id 分别应用 1~5 区间，否则前视一路会吃掉整个配额。
-- =============================================================================

SET 'pipeline.name' = 'adas-sampling-gate3-inference-keyframe';
SET 'execution.runtime-mode' = 'batch';

USE CATALOG paimon;
USE adas_lakehouse;

-- -----------------------------------------------------------------------------
-- 「进入 VLM 推理范围的 clip」的来源。
-- ⚠️ 原文未明确，本项目设计：原文只写了触发条件是「进入 VLM 推理范围的 clip」，
--    没说这个范围由谁定、落在哪张表。本项目**不臆造新表**，默认口径取原文四章
--    引用的向量化成本分级——"规则命中、事件抽帧产出的帧优先进 VLM"，即：
--    凡是有事件帧或规则命中帧的 clip 即在推理范围内。
--    真实项目若有独立的 VLM 调度表，替换本视图即可，下游逻辑不用动。
-- -----------------------------------------------------------------------------
CREATE TEMPORARY VIEW v_vlm_scope AS
SELECT DISTINCT `data_id`
FROM `dwd_mining_image_frame_detail`
WHERE `artifact_status` = 'active'
  AND (`extract_level` = 'event' OR `trigger_event_type` = 'rule_hit');

-- -----------------------------------------------------------------------------
-- 第一步：三维加权综合分（保留全部字段，供第三步整行 Upsert）
-- -----------------------------------------------------------------------------
CREATE TEMPORARY VIEW v_frame_scored AS
SELECT
  f.*,
  -- 三等分权重：1/3 + 1/3 + 1/3
  ( COALESCE(f.`frame_quality_score`,     0.0) * (1.0 / 3.0)
  + COALESCE(f.`object_richness_score`,   0.0) * (1.0 / 3.0)
  + COALESCE(f.`temporal_position_score`, 0.0) * (1.0 / 3.0)
  ) AS `computed_score`,
  -- 向量化成本分级：规则命中、事件抽帧产出的帧优先进 VLM
  CASE
    WHEN f.`extract_level` = 'event' THEN 1
    WHEN f.`trigger_event_type` = 'rule_hit' THEN 1
    ELSE 0
  END AS `vlm_priority`
FROM `dwd_mining_image_frame_detail` AS f
WHERE f.`artifact_status` = 'active'
  -- 进入 VLM 推理范围的 clip 才走这道闸门
  AND f.`data_id` IN (SELECT `data_id` FROM v_vlm_scope);

-- -----------------------------------------------------------------------------
-- 第二步：按 (data_id, camera_id) 分组排序，优先档在前、综合分次之
-- 相邻帧去重：与前一张入选帧间隔 < 1 秒的跳过（原文「相邻帧高度相似」）
-- frame_offset_sec 的单位是秒，所以间距比较用 1.0 而不是 1000。
-- -----------------------------------------------------------------------------
CREATE TEMPORARY VIEW v_frame_ranked AS
SELECT
  s.*,
  ROW_NUMBER() OVER (
    PARTITION BY s.`data_id`, s.`camera_id`
    ORDER BY s.`vlm_priority` DESC, s.`computed_score` DESC, s.`frame_offset_sec` ASC
  ) AS `rn`,
  LAG(s.`frame_offset_sec`) OVER (
    PARTITION BY s.`data_id`, s.`camera_id`
    ORDER BY s.`vlm_priority` DESC, s.`computed_score` DESC, s.`frame_offset_sec` ASC
  ) AS `prev_offset_sec`
FROM v_frame_scored AS s;

-- -----------------------------------------------------------------------------
-- 第三步：回写 is_keyframe 与综合分。每 clip 每路摄像头选 1~5 张：
--   · 综合分 >= 0.5 且排名在前 5 的入选；
--   · rn = 1 无条件入选，保证下限 1 张（原文区间是 1~5，不是 0~5）。
--
-- ★ 必须整行 Upsert，不能只写 (image_id, is_keyframe) 几列：帧表用的是 Paimon
--   默认的 deduplicate merge-engine，部分列 INSERT 会把没写的列覆盖成 NULL。
--   （若把表改成 'merge-engine' = 'partial-update'，才可以只写变更列。）
-- ★ 下面的列顺序**必须与 registry 里 dwd_mining_image_frame_detail 的列顺序逐列对齐**
--   （业务列 + DWD 层系统列 _ingest_time / update_time）；改表结构时同步改这里。
-- -----------------------------------------------------------------------------
INSERT INTO `dwd_mining_image_frame_detail`
SELECT
  r.`image_id`,
  r.`data_id`,
  r.`artifact_id`,
  r.`parent_artifact_id`,
  r.`artifact_status`,
  r.`run_id`,
  r.`extract_level`,             -- 保持产出该帧的闸门不变：闸门三不产新帧，只打标
  r.`sampling_interval_sec`,
  r.`camera_id`,
  r.`camera_position`,
  r.`frame_group_id`,
  r.`frame_index`,
  r.`frame_timestamp`,
  r.`frame_offset_sec`,
  r.`frame_quality_score`,
  r.`object_richness_score`,
  r.`temporal_position_score`,
  r.`computed_score` AS `keyframe_score`,
  TRUE AS `is_keyframe`,
  r.`trigger_event_type`,
  r.`event_time`,
  r.`event_window_start_time`,
  r.`event_window_end_time`,
  r.`gps_lat`,
  r.`gps_lon`,
  r.`image_object_key`,
  r.`image_size_bytes`,
  r.`image_width`,
  r.`image_height`,
  r.`desensitize_status`,
  r.`algo_version`,
  r.`create_time`,               -- 原样带回：本作业只打标，不是新建记录
  r.`project_code`,
  r.`vehicle_code`,
  r.`_ingest_time`,
  CURRENT_TIMESTAMP AS `update_time`
FROM v_frame_ranked AS r
WHERE r.`rn` = 1                                     -- 下限：至少 1 张
   OR ( r.`rn` <= 5                                  -- 上限：至多 5 张
        AND r.`computed_score` >= 0.5                -- 入选下限
        AND ( r.`prev_offset_sec` IS NULL
              OR ABS(r.`frame_offset_sec` - r.`prev_offset_sec`) >= 1.0 )  -- 最小间距 1 秒
      );

-- -----------------------------------------------------------------------------
-- 成本对账：这道闸门到底把 GPU 账单压到了几分之一
-- -----------------------------------------------------------------------------
-- SELECT
--   `data_id`,
--   COUNT(*)                                              AS candidate_frames,
--   SUM(CASE WHEN `is_keyframe` THEN 1 ELSE 0 END)        AS vlm_frames,
--   SUM(CASE WHEN `is_keyframe` THEN 1 ELSE 0 END) / COUNT(*) AS keep_ratio
-- FROM `dwd_mining_image_frame_detail`
-- WHERE `artifact_status` = 'active'
-- GROUP BY `data_id`;
