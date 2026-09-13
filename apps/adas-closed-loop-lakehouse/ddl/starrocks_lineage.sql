-- =============================================================================
-- StarRocks 侧的血缘查询支撑：属性回取视图 + 大批量影响分析的离线统计链路
--
-- 来源
--   [a13] 系列二·湖仓实战 第 5 篇《Paimon + Neo4j 湖图双引擎数据血缘追溯系统》
--         https://mp.weixin.qq.com/s/Yfrk2Z_izzzGSC_BQMsinQ
--   [a11] 系列一 第 4 篇《数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系》
--         https://mp.weixin.qq.com/s/bxyDkxNgLg7qhkJLqCjd9A
--
-- 本文件对应两条护栏（[a13] 六）：
--   · 属性单一事实源 —— 图库只存节点与关系（ID + 关系类型），属性、参数快照与指标
--     一律取自湖仓。下面的 v_lineage_* 视图就是「取自湖仓」的落地。
--   · 遍历边界     —— 大批量下游影响分析改走湖仓离线统计审计链路。
--     v_lineage_impact_by_algo_version 就是那条离线链路。
--
-- 查询三步走里，本文件服务第 ②③ 步（[a13] 五）：
--   ① Neo4j 多跳遍历找路径、定范围（只返回节点 ID 与关系类型）
--   → ② 按节点 ID 回 Paimon 补齐属性、参数快照与质量指标      ← 本文件
--   → ③ 组装输出带数据来源的血缘结果（表名 + ID）              ← 本文件提供 source_table 列
--
-- ⚠️ 说明：四张血缘事实表归 catalog/tables/ 下各域模块所有，本文件只建视图不建表。
--    dwd_badcase_detail 是本项目补的第五张（原文五类节点含 Badcase，
--    但 3.1 的四张事实表里没有它），列名为本项目推断。
--
-- 执行：
--   mysql -h $STARROCKS_FE_HOST -P 18630 -u root < ddl/starrocks_lineage.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. External Catalog：直查 Paimon，不做数据搬迁
--    连接参数对齐 adas_lakehouse.config（StarRocksConfig.external_catalog / PaimonConfig）
-- -----------------------------------------------------------------------------
CREATE EXTERNAL CATALOG IF NOT EXISTS `paimon_catalog`
PROPERTIES (
  'type' = 'paimon',
  'paimon.catalog.type' = 'filesystem',
  'paimon.catalog.warehouse' = 's3://adas-lakehouse/warehouse',
  'aws.s3.endpoint' = 'http://localhost:18600',
  'aws.s3.enable_path_style_access' = 'true',
  'aws.s3.access_key' = '${MINIO_ACCESS_KEY}',
  'aws.s3.secret_key' = '${MINIO_SECRET_KEY}'
);

CREATE DATABASE IF NOT EXISTS `adas_ads`;
USE `adas_ads`;

-- =============================================================================
-- 1. 属性回取视图：图库给 ID，这里给明细
--
-- 每个视图都带 source_table 列——护栏四「结果可审计」要求查询结果同时返回
-- 血缘路径与湖仓数据来源（表名 + ID）。视图自带表名，调用方无需再拼。
-- 与 adas_lakehouse.lineage.resolver.ATTRIBUTE_COLUMNS 的列清单一一对应。
-- =============================================================================

-- 1.1 Clip（追溯起点，图库 Clip 节点 / id = data_id）
CREATE VIEW IF NOT EXISTS `v_lineage_clip` AS
SELECT
  'dwd_collect_clip_detail' AS `source_table`,
  `data_id`,                 -- 一级 ID：clip 级终身锚点，重刷不变（[a11] 三·规则 3）
  `collect_task_id`,
  `vehicle_code`,            -- data_id 第二段来源
  `project_code`,
  `collect_start_time`,
  `collect_end_time`,
  `duration_sec`,            -- clip ≈ 1 分钟连续采集片段（[a11] 一）
  `road_type`,
  `weather`,
  `light_condition`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_collect_clip_detail`;

-- 1.2 Artifact（图库 Artifact 节点 / id = artifact_id）
--     param_snapshot 与 content_hash 是「永不落图库」的大属性，只在这里取
--     （[a13] 5.1：参数快照、质量分这些大属性全部回湖仓按 ID 批量取）。
CREATE VIEW IF NOT EXISTS `v_lineage_artifact` AS
SELECT
  'dwd_production_artifact_detail' AS `source_table`,
  `artifact_id`,             -- {data_id}_{step}_{algo_version}_{content_hash}，兼作图库节点 ID
  `data_id`,                 -- 所属 clip
  `stage`,                    -- align / slam / ann / qc / post
  `algo_version`,
  `content_hash`,            -- 内容相同则 artifact_id 相同，重试与重放天然幂等（[a11] 三·规则 1）
  `param_snapshot_json`,          -- 参数快照（JSON），可重放依据
  `parent_artifact_id`,      -- 实体血缘冗余字段（DERIVED_FROM 对账源）
  `superseded_by_artifact_id`,           -- 版本血缘冗余字段（SUPERSEDED_BY 对账源）
  `artifact_status`                   -- active / superseded / invalid
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_artifact_detail`;

-- 1.3 Run（图库 Run 节点 / id = run_id）
--     params 同样不落图库；[a13] 5.1 的反向追溯要 ORDER BY r.start_time，
--     start_time 也不在图上，排序在这里做。
CREATE VIEW IF NOT EXISTS `v_lineage_run` AS
SELECT
  'dwd_production_run_detail' AS `source_table`,
  `run_id`,                  -- 三级 ID：一次执行一条，绑定算法版本与参数快照
  `stage`,
  `run_status`,                  -- running → success / failed / cancelled（[a13] 4.2）
  `algo_version`,
  `param_snapshot_json`,                  -- 参数快照，示例 {"max_iter":200}（[a13] 4.1）
  `input_artifact_ids`,      -- INPUT 对账源
  `output_artifact_ids`,     -- PRODUCED 对账源
  `start_time`,
  `end_time`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_run_detail`;

-- 1.4 DatasetVersion（图库 DatasetVersion 节点 / id = dataset_version_id，示例 DS_0001_V2）
CREATE VIEW IF NOT EXISTS `v_lineage_dataset_version` AS
SELECT
  'dwd_dataset_version_detail' AS `source_table`,
  `dataset_version_id`,
  `dataset_id`,
  `version`,
  `version_status`,                  -- draft → released → deprecated（[a13] 4.2）
  `artifact_refs`            -- REFERENCES 对账源；锁定引用的产物版本列表，保证可复现
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_dataset_version_detail`;

-- 1.5 Badcase（图库 Badcase 节点 / id = badcase_id，示例 BC_20240120_001）
--     ⚠️ 原文未明确，本项目设计：原文四张事实表里没有 Badcase 表
CREATE VIEW IF NOT EXISTS `v_lineage_badcase` AS
SELECT
  'dwd_badcase_detail' AS `source_table`,
  `badcase_id`,
  `data_id`,
  `evaluation_type`,
  `traced_artifact_ids`      -- TRACED_TO 对账源
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_badcase_detail`;

-- =============================================================================
-- 2. 关系摊平视图：多值冗余列 → 一行一条边
--
-- 服务两件事：① T+1 对账的湖-图差异体检；② 大批量影响分析的离线统计。
--
-- ⚠️ 原文未明确多值列的编码方式（[a13] 3.1 只写「冗余 input·output_artifact_ids」/
--    「冗余 artifact_refs」）。这里按 JSON 数组与逗号分隔两种写法兼容：
--    先用 REGEXP_REPLACE 剥掉方括号与引号，再按逗号 SPLIT。
--    与 adas_lakehouse.lineage.events.parse_id_list() 的兼容策略一致。
-- =============================================================================

-- 2.1 数据集版本 → 引用的产物（REFERENCES 边）
CREATE VIEW IF NOT EXISTS `v_lineage_dataset_artifact_ref` AS
SELECT
  d.`dataset_version_id`,
  d.`dataset_id`,
  d.`version`,
  d.`version_status` AS `dataset_status`,
  TRIM(ref) AS `artifact_id`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_dataset_version_detail` d,
     UNNEST(SPLIT(REGEXP_REPLACE(d.`artifact_refs`, '[\\[\\]"]', ''), ',')) AS t(ref)
WHERE d.`artifact_refs` IS NOT NULL
  AND TRIM(ref) <> '';

-- 2.2 运行 → 输入 / 输出产物（INPUT / PRODUCED 边）
--     红线②：只有 success 的 run 建边——failed 的 run 不产生 Artifact 节点、其输入输出不建边
CREATE VIEW IF NOT EXISTS `v_lineage_run_artifact_edge` AS
SELECT
  r.`run_id`, r.`stage`, r.`run_status`,
  'INPUT' AS `rel_type`,
  TRIM(aid) AS `artifact_id`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_run_detail` r,
     UNNEST(SPLIT(REGEXP_REPLACE(r.`input_artifact_ids`, '[\\[\\]"]', ''), ',')) AS t(aid)
WHERE r.`run_status` = 'success'
  AND r.`input_artifact_ids` IS NOT NULL
  AND TRIM(aid) <> ''
UNION ALL
SELECT
  r.`run_id`, r.`stage`, r.`run_status`,
  'PRODUCED' AS `rel_type`,
  TRIM(aid) AS `artifact_id`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_run_detail` r,
     UNNEST(SPLIT(REGEXP_REPLACE(r.`output_artifact_ids`, '[\\[\\]"]', ''), ',')) AS t(aid)
WHERE r.`run_status` = 'success'
  AND r.`output_artifact_ids` IS NOT NULL
  AND TRIM(aid) <> '';

-- 2.3 Badcase → 追溯到的产物（TRACED_TO 边）
CREATE VIEW IF NOT EXISTS `v_lineage_badcase_artifact_ref` AS
SELECT
  b.`badcase_id`,
  b.`data_id`,
  b.`evaluation_type`,
  TRIM(aid) AS `artifact_id`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_badcase_detail` b,
     UNNEST(SPLIT(REGEXP_REPLACE(b.`traced_artifact_ids`, '[\\[\\]"]', ''), ',')) AS t(aid)
WHERE b.`traced_artifact_ids` IS NOT NULL
  AND TRIM(aid) <> '';

-- =============================================================================
-- 3. 影响分析的离线统计链路（护栏三的逃生通道）
--
-- [a13] 六·遍历边界：「大批量下游影响分析改走湖仓离线统计审计链路」
-- [a13] 高频踩坑：  「超大规模的下游影响评估别在图库里硬算，走湖仓离线统计才是正解」
--
-- 触发条件（⚠️ 原文未给阈值，本项目设定）：图库一跳命中的旧版本产物数
-- 超过 adas_lakehouse.lineage.constants.OFFLINE_IMPACT_FANOUT_THRESHOLD = 10000
-- 时，LineageQueryService.impact_analysis() 不在图库硬算，直接返回这里的 SQL。
-- =============================================================================

-- 3.1 按 (step, algo_version) 统计仍引用旧版本产物的数据集版本
--     与 [a13] 5.1 的影响分析 Cypher 等价，只是把遍历换成了 JOIN：
--       MATCH (a:Artifact {step:'slam', algo_version:'v3'})<-[:REFERENCES]-(d:DatasetVersion)
--       RETURN DISTINCT d.dataset_id, d.version, d.status;
CREATE VIEW IF NOT EXISTS `v_lineage_impact_by_algo_version` AS
SELECT
  a.`stage`,
  a.`algo_version`,
  r.`dataset_id`,
  r.`version`,
  r.`dataset_status`,
  COUNT(DISTINCT a.`artifact_id`) AS `stale_artifact_cnt`,
  COUNT(DISTINCT a.`data_id`)     AS `affected_clip_cnt`,
  MAX(CASE WHEN a.`superseded_by_artifact_id` IS NOT NULL AND a.`superseded_by_artifact_id` <> ''
           THEN 1 ELSE 0 END)     AS `has_newer_version`
FROM `v_lineage_dataset_artifact_ref` r
JOIN `paimon_catalog`.`adas_lakehouse`.`dwd_production_artifact_detail` a
  ON a.`artifact_id` = r.`artifact_id`
GROUP BY a.`stage`, a.`algo_version`, r.`dataset_id`, r.`version`, r.`dataset_status`;

-- 3.2 重刷排期看板：某算法升级后的整体影响面
--     用法（SLAM v3 → v4 的重刷排期，[a13] 3.2 / [a11] 五 的场景）：
--       SELECT * FROM v_lineage_backfill_scope
--       WHERE step = 'slam' AND algo_version = 'v3';
--     「历史百万级 clip 要不要重新处理」（[a11] 五）——affected_clip_cnt 就是这个数。
CREATE VIEW IF NOT EXISTS `v_lineage_backfill_scope` AS
SELECT
  `stage`,
  `algo_version`,
  COUNT(DISTINCT `dataset_id`)            AS `affected_dataset_cnt`,
  COUNT(*)                                AS `affected_dataset_version_cnt`,
  SUM(`stale_artifact_cnt`)               AS `stale_artifact_cnt`,
  SUM(`affected_clip_cnt`)                AS `affected_clip_cnt`,
  SUM(CASE WHEN `dataset_status` = 'released' THEN 1 ELSE 0 END) AS `released_version_cnt`
FROM `v_lineage_impact_by_algo_version`
GROUP BY `stage`, `algo_version`;

-- =============================================================================
-- 4. 版本分支视图：同一 clip 同一环节的多版本并列（可对比）
--
-- [a13] 三·版本血缘：「同一环节不同算法版本产物构成版本分支」
-- [a13] 3.2：       「同一 clip 的 SLAM 环节存在 v3 / v4 两个分支，
--                     直接对比两版的标注与评测效果」
-- =============================================================================
CREATE VIEW IF NOT EXISTS `v_lineage_version_branch` AS
SELECT
  a.`data_id`,
  a.`stage`,
  a.`algo_version`,
  a.`artifact_id`,
  a.`content_hash`,
  a.`artifact_status`,                -- 旧分支保留为 superseded，绝不删除（[a13] 3.2 实践提醒）
  a.`superseded_by_artifact_id`,
  a.`param_snapshot_json`,
  COUNT(*) OVER (PARTITION BY a.`data_id`, a.`stage`) AS `branch_cnt`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_artifact_detail` a;

-- =============================================================================
-- 5. 湖-图对账体检：T+1 窗口内「湖仓说应该有」的全部边
--
-- 与 flink/sql/lineage_reconcile_t1.sql 第 2 节等价，供 StarRocks 侧执行。
-- adas_lakehouse.lineage.sync.ReconciliationJob.audit_missing_edges() 拿这份清单
-- 逐条去图库核对，缺哪条补哪条——护栏二「湖仓冗余血缘字段为对账源」。
-- =============================================================================
CREATE VIEW IF NOT EXISTS `v_lineage_expected_edges` AS
SELECT 'CONTAINS' AS `rel_type`, `data_id` AS `src_id`, `artifact_id` AS `dst_id`,
       'dwd_production_artifact_detail.data_id' AS `reconcile_source`, `_ingest_time`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_artifact_detail`
WHERE `data_id` IS NOT NULL AND `data_id` <> ''
UNION ALL
SELECT 'DERIVED_FROM', `artifact_id`, `parent_artifact_id`,
       'dwd_production_artifact_detail.parent_artifact_id', `_ingest_time`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_artifact_detail`
WHERE `parent_artifact_id` IS NOT NULL AND `parent_artifact_id` <> ''
UNION ALL
SELECT 'SUPERSEDED_BY', `artifact_id`, `superseded_by_artifact_id`,
       'dwd_production_artifact_detail.superseded_by_artifact_id', `_ingest_time`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_production_artifact_detail`
WHERE `superseded_by_artifact_id` IS NOT NULL AND `superseded_by_artifact_id` <> ''
UNION ALL
SELECT e.`rel_type`, e.`run_id`, e.`artifact_id`,
       CONCAT('dwd_production_run_detail.',
              CASE WHEN e.`rel_type` = 'INPUT' THEN 'input_artifact_ids'
                   ELSE 'output_artifact_ids' END),
       r.`_ingest_time`
FROM `v_lineage_run_artifact_edge` e
JOIN `paimon_catalog`.`adas_lakehouse`.`dwd_production_run_detail` r
  ON r.`run_id` = e.`run_id`
UNION ALL
SELECT 'REFERENCES', v.`dataset_version_id`, v.`artifact_id`,
       'dwd_dataset_version_detail.artifact_refs', d.`_ingest_time`
FROM `v_lineage_dataset_artifact_ref` v
JOIN `paimon_catalog`.`adas_lakehouse`.`dwd_dataset_version_detail` d
  ON d.`dataset_version_id` = v.`dataset_version_id`
UNION ALL
SELECT 'TRACED_TO', t.`badcase_id`, t.`artifact_id`,
       'dwd_badcase_detail.traced_artifact_ids', b.`_ingest_time`
FROM `v_lineage_badcase_artifact_ref` t
JOIN `paimon_catalog`.`adas_lakehouse`.`dwd_badcase_detail` b
  ON b.`badcase_id` = t.`badcase_id`;

-- =============================================================================
-- 6. 典型用法（对照 [a13] 五 的四个查询方向）
--
-- 正向追踪（clip → 数据集）：图库先给路径，这里补明细
--   SELECT * FROM v_lineage_artifact
--   WHERE artifact_id IN (<Neo4j 遍历返回的 ID 集合>);
--
-- 反向追溯（Badcase → 运行参数）：[a13] 5.1 的 Cypher 只取 ID，参数在这里取
--   SELECT r.run_id, r.algo_version, r.params, a.param_snapshot, a.content_hash
--   FROM v_lineage_run r
--   JOIN v_lineage_run_artifact_edge e ON e.run_id = r.run_id AND e.rel_type = 'PRODUCED'
--   JOIN v_lineage_artifact a ON a.artifact_id = e.artifact_id
--   WHERE a.artifact_id IN (<Neo4j 从 Badcase 追到的 artifact_id>)
--   ORDER BY r.start_time DESC;
--
-- 版本分支对比（SLAM v3 vs v4）：
--   SELECT * FROM v_lineage_version_branch
--   WHERE data_id = 'COLLECT_BP_20240115143022_a3f8' AND step = 'slam';
--
-- 影响分析（重刷排期，大批量走这里而不是图库）：
--   SELECT * FROM v_lineage_backfill_scope WHERE step = 'slam' AND algo_version = 'v3';
-- =============================================================================
