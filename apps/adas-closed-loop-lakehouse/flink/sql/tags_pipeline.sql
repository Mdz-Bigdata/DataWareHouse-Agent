-- ============================================================================
-- 统一标签体系 · 三源收口管道（Flink SQL / Paimon）
-- 来源：系列三《数据闭环统一标签体系设计：三来源标签的字典映射与去重治理》第二章
--   「三来源标签一律经统一标签服务写入，没有旁路。管道三步：
--     ① 字典映射 ② 幂等去重 ③ 血缘填充」
--
-- 本文件是 Python 侧 adas_lakehouse.tags.pipeline.UnifiedTagService 的 SQL 对照实现，
-- 用于批量回补与离线重刷；在线写入走统一标签服务。两侧口径必须一致：
--   · 映射顺序：active 精确/别名命中 → merged 改写 → 未命中进候选池（不入库）
--   · 幂等口径：主键 (data_id/image_id, tag_id, tag_source)，同主键取高 confidence
--   · artifact_id：SHA256(data_id|image_id|tag_id|tag_source|raw_tag) 取前 8 位，
--     与 adas_lakehouse.ids.content_hash 完全一致，两侧生成的 ID 必须相等
--
-- 变量替换（提交前由调度框架替换，或用 SET 定义）：
--   ${RUN_ID}        三级 ID，形如 'run_tag_20260911100000_ab12cd34'
--   ${ALGO_VERSION}  算法版本，形如 'v3'
--   ${DT}            处理日期，形如 '2026-09-11'
-- ============================================================================

SET 'pipeline.name' = 'adas-tags-pipeline';
SET 'execution.checkpointing.interval' = '60s';   -- 对齐 config.FlinkConfig.checkpoint_interval_ms = 60000

-- ---------------------------------------------------------------------------
-- 0. 字典匹配键打平：标准名 + 英文名 + alias_json 三路 UNION
--    「雨天/降雨/rain/下雨天」四个写法在这里全部指向同一个 tag_id，
--    这就是「检索时查一个漏三个」的解药。
-- ---------------------------------------------------------------------------
CREATE TEMPORARY VIEW tag_alias_flat AS
SELECT tag_id, tag_name, tag_category, tag_level, tag_status, merged_into_tag_id,
       LOWER(TRIM(tag_name)) AS alias_key, 'canonical' AS match_kind
FROM `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail`
UNION ALL
SELECT tag_id, tag_name, tag_category, tag_level, tag_status, merged_into_tag_id,
       LOWER(TRIM(tag_name_en)) AS alias_key, 'alias' AS match_kind
FROM `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail`
WHERE tag_name_en IS NOT NULL AND CHAR_LENGTH(TRIM(tag_name_en)) > 0
UNION ALL
SELECT d.tag_id, d.tag_name, d.tag_category, d.tag_level, d.tag_status, d.merged_into_tag_id,
       LOWER(TRIM(a.alias)) AS alias_key, 'alias' AS match_kind
FROM `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail` AS d
CROSS JOIN UNNEST(JSON_QUERY(d.alias_json, '$' RETURNING ARRAY<STRING>)) AS a(alias)
WHERE a.alias IS NOT NULL AND CHAR_LENGTH(TRIM(a.alias)) > 0;

-- 只有 active 参与正式匹配（candidate「不参与正式检索与统计」、deprecated 拒绝新写入）
CREATE TEMPORARY VIEW tag_alias_active AS
SELECT * FROM tag_alias_flat WHERE tag_status = 'active';

-- merged 墓碑单独一张：旧写法命中后改写到 merged_into_tag_id
CREATE TEMPORARY VIEW tag_alias_merged AS
SELECT * FROM tag_alias_flat WHERE tag_status = 'merged';

-- ---------------------------------------------------------------------------
-- 1. 三来源原始标签收到一个视图（列对齐，缺的补 NULL）
--    ① 采集标签：人工约定，无 rule/model 血缘
--    ② 规则标签：跟着规则版本走
--    ③ 模型标签：VLM 自由发挥，必须带 confidence
-- ---------------------------------------------------------------------------
CREATE TEMPORARY VIEW raw_tag_union AS
SELECT
  data_id, CAST(NULL AS STRING) AS image_id, raw_tag,
  'collect' AS tag_source,
  CAST(NULL AS DOUBLE) AS confidence,
  CAST(NULL AS STRING) AS rule_id, CAST(NULL AS STRING) AS rule_version,
  CAST(NULL AS STRING) AS model_name, CAST(NULL AS STRING) AS model_version,
  CAST(NULL AS STRING) AS infer_job_id, CAST(NULL AS STRING) AS caption_text,
  project_code, vehicle_code, tag_time
FROM ods_collect_raw_tag                      -- 采集系统随车带回的标签
UNION ALL
SELECT
  data_id, image_id, raw_tag, 'rule',
  confidence, rule_id, rule_version,
  CAST(NULL AS STRING), CAST(NULL AS STRING), infer_job_id, CAST(NULL AS STRING),
  project_code, vehicle_code, tag_time
FROM dwd_mining_rule_hit_detail               -- 规则挖掘引擎的命中产出
UNION ALL
SELECT
  data_id, image_id, raw_tag, 'model',
  confidence, CAST(NULL AS STRING), CAST(NULL AS STRING),
  model_name, model_version, infer_job_id, caption_text,
  project_code, vehicle_code, tag_time
FROM dwd_mining_vlm_infer_detail;             -- VLM 推理产出

-- ---------------------------------------------------------------------------
-- 2. ① 字典映射：active 命中优先，其次 merged 改写，都不中的判为 candidate
-- ---------------------------------------------------------------------------
CREATE TEMPORARY VIEW mapped_tag AS
SELECT
  r.*,
  COALESCE(m.tag_id, mg.merged_into_tag_id)   AS mapped_tag_id,
  COALESCE(m.tag_name, mg.tag_name)           AS mapped_tag_name,
  COALESCE(m.tag_category, mg.tag_category)   AS mapped_tag_category,
  COALESCE(m.tag_level, mg.tag_level)         AS mapped_tag_level,
  CASE
    WHEN m.tag_id IS NOT NULL AND m.match_kind = 'canonical' THEN 'canonical'
    WHEN m.tag_id IS NOT NULL                                THEN 'alias'
    WHEN mg.merged_into_tag_id IS NOT NULL                   THEN 'merged_redirect'
    ELSE 'candidate'
  END                                          AS mapping_type
FROM raw_tag_union AS r
LEFT JOIN tag_alias_active AS m  ON LOWER(TRIM(r.raw_tag)) = m.alias_key
LEFT JOIN tag_alias_merged AS mg ON LOWER(TRIM(r.raw_tag)) = mg.alias_key;

-- 防爆炸第一道闸：未匹配的**不入事实表**，只在候选池视图里累计证据等审核。
-- ⚠️ 原文未明确，本项目设计：原文说「进候选池待审」但没给候选池表名，
--   本项目不为它新增湖仓表，候选池由统一标签服务（Python 侧 CandidatePool）承载，
--   这里只提供一张视图供审核平台直接查批量回补场景下的候选。
CREATE TEMPORARY VIEW tag_candidate_pool_v AS
SELECT
  LOWER(TRIM(raw_tag))         AS normalized,
  MIN(raw_tag)                 AS sample_raw_form,
  LISTAGG(tag_source, ',')     AS sources,
  COUNT(*)                     AS hit_cnt,
  MIN(tag_time)                AS first_seen,
  MAX(tag_time)                AS last_seen,
  MIN(data_id)                 AS sample_data_id
FROM mapped_tag
WHERE mapping_type = 'candidate'
GROUP BY LOWER(TRIM(raw_tag));

-- ---------------------------------------------------------------------------
-- 3. ② 幂等去重 + ③ 血缘填充 → clip 级事实表
--    去重：Paimon 主键表 (data_id, tag_id, tag_source) 天然 Upsert；
--          批内同主键多条先用 ROW_NUMBER 收敛，置信度高者胜（对齐 Python 侧 R1）
--    血缘：tag_source / rule_id / model_name / model_version / confidence /
--          infer_job_id + run_id / artifact_id（回答「从哪来、可信度多少」）
--    准入：模型标签 confidence >= 0.50（⚠️ 门限为本项目设计，原文未给数值）
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_mining_data_tag_detail`
SELECT
  data_id, mapped_tag_id, mapped_tag_name, mapped_tag_category, tag_source, mapped_tag_level,
  confidence, rule_id, rule_version, model_name, model_version, infer_job_id,
  ${RUN_ID}                                                        AS run_id,
  CONCAT(data_id, '_tag_', ${ALGO_VERSION}, '_',
         SUBSTR(SHA256(CONCAT_WS('|', data_id, '', mapped_tag_id, tag_source, raw_tag)), 1, 8)
  )                                                                AS artifact_id,
  CAST(NULL AS STRING)                                             AS parent_artifact_id,
  'active'                                                         AS artifact_status,
  -- 模型产出永远先过审再上岗；采集/规则口径确定，直接 approved
  CASE WHEN tag_source = 'model' THEN 'pending' ELSE 'approved' END AS review_status,
  CAST(NULL AS STRING)                                             AS review_operator,
  CAST(NULL AS TIMESTAMP(3))                                       AS review_time,
  raw_tag                                                          AS source_raw_tag,
  mapping_type,
  ''                                                               AS mapping_note,
  CAST(NULL AS STRING)                                             AS conflict_resolution,
  TRUE                                                             AS valid_flag,
  project_code, vehicle_code, tag_time
FROM (
  SELECT *, ROW_NUMBER() OVER (
      PARTITION BY data_id, mapped_tag_id, tag_source
      ORDER BY confidence DESC NULLS LAST, tag_time DESC
    ) AS rn
  FROM mapped_tag
  WHERE mapping_type <> 'candidate'
    AND image_id IS NULL
    -- 血缘必填校验：规则标签要 rule_id；模型标签要 model_name/model_version/confidence
    AND (tag_source <> 'rule'  OR rule_id IS NOT NULL)
    AND (tag_source <> 'model' OR (model_name IS NOT NULL AND model_version IS NOT NULL
                                   AND confidence IS NOT NULL AND confidence >= 0.50))
) WHERE rn = 1;

-- ---------------------------------------------------------------------------
-- 4. image 级事实表：直接打在图片上的标签（含 CAPTION 特殊标签）
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_mining_image_tag_detail`
SELECT
  image_id, data_id, mapped_tag_id, mapped_tag_name, mapped_tag_category, tag_source,
  mapped_tag_level, confidence, rule_id, rule_version, model_name, model_version, infer_job_id,
  ${RUN_ID},
  CONCAT(data_id, '_tag_', ${ALGO_VERSION}, '_',
         SUBSTR(SHA256(CONCAT_WS('|', data_id, image_id, mapped_tag_id, tag_source, raw_tag)), 1, 8)),
  CAST(NULL AS STRING), 'active',
  CASE WHEN tag_source = 'model' THEN 'pending' ELSE 'approved' END,
  CAST(NULL AS STRING), CAST(NULL AS TIMESTAMP(3)),
  raw_tag, mapping_type, '', CAST(NULL AS STRING), TRUE,
  project_code, vehicle_code, tag_time,
  FALSE                                                            AS inherited_from_clip,
  caption_text
FROM (
  SELECT *, ROW_NUMBER() OVER (
      PARTITION BY image_id, mapped_tag_id, tag_source
      ORDER BY confidence DESC NULLS LAST, tag_time DESC
    ) AS rn
  FROM mapped_tag
  WHERE mapping_type <> 'candidate' AND image_id IS NOT NULL
    AND (tag_source <> 'rule'  OR rule_id IS NOT NULL)
    AND (tag_source <> 'model' OR (model_name IS NOT NULL AND model_version IS NOT NULL
                                   AND confidence IS NOT NULL AND confidence >= 0.50))
) WHERE rn = 1;

-- ---------------------------------------------------------------------------
-- 5. clip → image 标签继承
--    原文：tag_level 区分 clip 级 / image 级 / 可继承——clip 标签可自动继承到
--    它抽出来的每一张图片。只有 tag_level='inheritable' 且未被互斥裁决判负的才继承。
--    主键相同即 Upsert，重复执行无副作用。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_mining_image_tag_detail`
SELECT
  f.image_id, t.data_id, t.tag_id, t.tag_name, t.tag_category, t.tag_source, t.tag_level,
  t.confidence, t.rule_id, t.rule_version, t.model_name, t.model_version, t.infer_job_id,
  t.run_id, t.artifact_id, t.parent_artifact_id, t.artifact_status,
  t.review_status, t.review_operator, t.review_time,
  t.source_raw_tag, t.mapping_type,
  '继承自 clip 标签（tag_level=inheritable）'                       AS mapping_note,
  t.conflict_resolution, t.valid_flag, t.project_code, t.vehicle_code, t.tag_time,
  TRUE                                                             AS inherited_from_clip,
  CAST(NULL AS STRING)                                             AS caption_text
FROM `paimon`.`adas_lakehouse`.`dwd_mining_data_tag_detail` AS t
JOIN `paimon`.`adas_lakehouse`.`dwd_mining_image_frame_detail` AS f   -- 抽帧产物
  ON f.data_id = t.data_id
WHERE t.tag_level = 'inheritable' AND t.valid_flag = TRUE;

-- ---------------------------------------------------------------------------
-- 6. caption 冗余到向量表
--    原文：caption 以 tag_category='CAPTION' 的特殊标签写入图片标签表，
--    同时冗余一份到向量表——结构化过滤和语义检索用同一份说明，不用两套维护。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_mining_image_vector_detail` (
  image_id, data_id, caption, model_name, model_version, artifact_id, run_id, update_time
)
SELECT
  image_id, data_id, caption_text, model_name, model_version, artifact_id, run_id, tag_time
FROM `paimon`.`adas_lakehouse`.`dwd_mining_image_tag_detail`
WHERE tag_category = 'CAPTION' AND caption_text IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 7. 同层互斥裁决（ISO 34504 / SOTIF 同层互斥原则）
--    ⚠️ 原文未明确，本项目设计：裁决顺序 = 来源优先级(collect<rule<model) →
--    confidence 降序 → tag_id 字典序。落败方不删除，只置 valid_flag=false 并留痕，
--    「模型当时说了什么」永远可回溯（配合 Paimon 时间旅行）。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_mining_data_tag_detail`
SELECT
  t.data_id, t.tag_id, t.tag_name, t.tag_category, t.tag_source, t.tag_level,
  t.confidence, t.rule_id, t.rule_version, t.model_name, t.model_version, t.infer_job_id,
  t.run_id, t.artifact_id, t.parent_artifact_id, t.artifact_status,
  t.review_status, t.review_operator, t.review_time,
  t.source_raw_tag, t.mapping_type, t.mapping_note,
  CONCAT('mutex:loser:source_priority>confidence>tag_id:', d.mutex_group) AS conflict_resolution,
  FALSE                                                             AS valid_flag,
  t.project_code, t.vehicle_code, t.tag_time
FROM (
  SELECT t.*, d.mutex_group,
         ROW_NUMBER() OVER (
           PARTITION BY t.data_id, d.mutex_group
           ORDER BY CASE t.tag_source WHEN 'collect' THEN 1 WHEN 'rule' THEN 2 ELSE 3 END,
                    t.confidence DESC NULLS LAST, t.tag_id
         ) AS mutex_rank
  FROM `paimon`.`adas_lakehouse`.`dwd_mining_data_tag_detail` AS t
  JOIN `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail` AS d ON d.tag_id = t.tag_id
  WHERE d.mutex_group IS NOT NULL AND t.valid_flag = TRUE AND t.tag_category <> 'CAPTION'
) AS t
JOIN `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail` AS d ON d.tag_id = t.tag_id
WHERE t.mutex_rank > 1;
