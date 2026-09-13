-- =============================================================================
-- 链路三（对账）：T+1 定时扫描湖仓增量，按关系冗余字段 UPSERT 补齐图库
--
-- 来源
--   [a13] 四·链路三原文：
--     「T+1 / 定时扫描湖仓增量（_ingest_time），按关系字段 UPSERT 补齐 | 兜底；幂等可重复执行」
--   [a13] 六·双链路可靠性：
--     「湖仓冗余血缘字段为对账源，图库实时同步失败时由定期对账（T+1）UPSERT 补齐」
--
-- 这个文件只负责「捞出该补的行」，真正的 MERGE 由
-- adas_lakehouse.lineage.sync.ReconciliationJob 执行（Neo4j 无 Flink 连接器）。
-- 两条链路共用 events.fact_from_row 的同一套转换，所以对账补出来的图
-- 与实时链路写出来的完全一致——这是幂等的根本。
--
-- 时间窗口
--   T+1 = 滞后 1 天（adas_lakehouse.lineage.constants.RECONCILE_LAG_DAYS = 1）。
--   今天跑的作业扫的是昨天入湖的数据：[T-1 00:00:00, T 00:00:00)。
--   窗口计算见 lineage.sync.reconcile_window()，两边必须一致。
--
-- 幂等
--   本作业可在同一天重复执行任意次：只读湖仓、产出的 MERGE 语句集合相同，
--   图的最终状态不变。这正是「兜底」的定义。
--
-- 运行（批模式，跑完即退）：
--   ./bin/sql-client.sh -f flink/sql/lineage_reconcile_t1.sql \
--       -Dwindow.start='2024-01-15 00:00:00' -Dwindow.end='2024-01-16 00:00:00'
-- =============================================================================

SET 'pipeline.name' = 'adas-lineage-reconcile-t1';
SET 'execution.runtime-mode' = 'batch';
SET 'parallelism.default' = '2';

CREATE CATALOG `paimon` WITH (
  'type' = 'paimon',
  'warehouse' = 's3://adas-lakehouse/warehouse'
);
USE CATALOG `paimon`;
USE `adas_lakehouse`;

-- -----------------------------------------------------------------------------
-- 1. 增量扫描：五张事实表在 T+1 窗口内的新增/变更行
--
-- 与 lineage.sync.ReconciliationJob.scan_sql() 渲染出来的 SQL 语义一致
-- （那边走 StarRocks External Catalog，这边走 Flink 直读 Paimon，二选一即可）。
-- -----------------------------------------------------------------------------

-- ① Clip
SELECT `data_id`, `_ingest_time`
FROM `dwd_collect_clip_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
ORDER BY `_ingest_time`;

-- ② Artifact：一行同时是 CONTAINS / DERIVED_FROM / SUPERSEDED_BY 三类边的对账源
SELECT
  `artifact_id`, `data_id`, `stage`, `algo_version`, `content_hash`,
  `parent_artifact_id`,   -- DERIVED_FROM 对账源（[a13] 3.1 DDL 注释逐字）
  `superseded_by_artifact_id`,        -- SUPERSEDED_BY 对账源（[a13] 3.1 DDL 注释逐字）
  `artifact_status`,               -- active / superseded / invalid
  `_ingest_time`
FROM `dwd_production_artifact_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
ORDER BY `_ingest_time`;

-- ③ Run：INPUT / PRODUCED 对账源
--    注意 status='failed' 的行也扫出来——Run 节点要建，但按红线②不建边、不产生 Artifact 节点，
--    这个判断在 RunFact.produces_edges 里做，SQL 侧不过滤，否则失败的运行记录在图上查不到。
SELECT
  `run_id`, `stage`, `run_status`, `algo_version`,
  `input_artifact_ids`, `output_artifact_ids`,
  `_ingest_time`
FROM `dwd_production_run_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
ORDER BY `_ingest_time`;

-- ④ DatasetVersion：REFERENCES 对账源
SELECT
  `dataset_version_id`, `dataset_id`, `version`, `version_status`,
  `artifact_refs`,        -- REFERENCES 对账源（[a13] 3.1「冗余 artifact_refs」）
  `_ingest_time`
FROM `dwd_dataset_version_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
ORDER BY `_ingest_time`;

-- ⑤ Badcase：TRACED_TO 对账源
--    ⚠️ 表名与列名为本项目设计（原文四张事实表里没有 Badcase 表）
SELECT
  `badcase_id`, `data_id`, `evaluation_type`, `traced_artifact_ids`, `_ingest_time`
FROM `dwd_badcase_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
ORDER BY `_ingest_time`;

-- -----------------------------------------------------------------------------
-- 2. 湖-图一致性体检：把冗余关系字段摊平成「应该存在的边」清单
--
-- 输出交给 lineage.sync.ReconciliationJob.audit_missing_edges() 逐条去图库核对，
-- 缺哪条补哪条。以湖仓为准——护栏二「湖仓冗余血缘字段为对账源」。
--
-- ⚠️ 原文未明确多值列（input_artifact_ids / output_artifact_ids / artifact_refs /
--    traced_artifact_ids）的编码方式。本项目按 JSON 数组或逗号分隔两种写法兼容；
--    下面的摊平用逗号分隔实现（JSON 写法先去掉方括号与引号），与
--    lineage.events.parse_id_list() 的兼容策略一致。
-- -----------------------------------------------------------------------------

-- 2.1 实体血缘：CONTAINS（clip → 产物）
SELECT
  'CONTAINS' AS `rel_type`,
  `data_id`  AS `src_id`,
  `artifact_id` AS `dst_id`,
  'dwd_production_artifact_detail.data_id' AS `reconcile_source`
FROM `dwd_production_artifact_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND `data_id` IS NOT NULL;

-- 2.2 实体血缘：DERIVED_FROM（子产物 → 父产物）
SELECT
  'DERIVED_FROM' AS `rel_type`,
  `artifact_id`  AS `src_id`,
  `parent_artifact_id` AS `dst_id`,
  'dwd_production_artifact_detail.parent_artifact_id' AS `reconcile_source`
FROM `dwd_production_artifact_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND `parent_artifact_id` IS NOT NULL
  AND `parent_artifact_id` <> '';

-- 2.3 版本血缘：SUPERSEDED_BY（旧产物 → 新产物）
--     [a13] 3.2：SLAM v3 → v4 重刷后，旧产物 status=superseded 且 superseded_by_artifact_id 指向新产物。
SELECT
  'SUPERSEDED_BY' AS `rel_type`,
  `artifact_id`   AS `src_id`,
  `superseded_by_artifact_id` AS `dst_id`,
  'dwd_production_artifact_detail.superseded_by_artifact_id' AS `reconcile_source`
FROM `dwd_production_artifact_detail`
WHERE `_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND `_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND `superseded_by_artifact_id` IS NOT NULL
  AND `superseded_by_artifact_id` <> '';

-- 2.4 运行血缘：INPUT（run → 输入产物），多值列摊平
SELECT
  'INPUT'  AS `rel_type`,
  r.`run_id` AS `src_id`,
  TRIM(t.`artifact_id`) AS `dst_id`,
  'dwd_production_run_detail.input_artifact_ids' AS `reconcile_source`
FROM `dwd_production_run_detail` AS r
CROSS JOIN UNNEST(
  SPLIT(REGEXP_REPLACE(r.`input_artifact_ids`, '[\\[\\]"]', ''), ',')
) AS t (`artifact_id`)
WHERE r.`_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND r.`_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND r.`run_status` = 'success'          -- 红线②：只有 success 的 run 建边
  AND r.`input_artifact_ids` IS NOT NULL
  AND TRIM(t.`artifact_id`) <> '';

-- 2.5 运行血缘：PRODUCED（run → 输出产物）
SELECT
  'PRODUCED' AS `rel_type`,
  r.`run_id` AS `src_id`,
  TRIM(t.`artifact_id`) AS `dst_id`,
  'dwd_production_run_detail.output_artifact_ids' AS `reconcile_source`
FROM `dwd_production_run_detail` AS r
CROSS JOIN UNNEST(
  SPLIT(REGEXP_REPLACE(r.`output_artifact_ids`, '[\\[\\]"]', ''), ',')
) AS t (`artifact_id`)
WHERE r.`_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND r.`_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND r.`run_status` = 'success'
  AND r.`output_artifact_ids` IS NOT NULL
  AND TRIM(t.`artifact_id`) <> '';

-- 2.6 实体血缘：REFERENCES（数据集版本 → 产物）
SELECT
  'REFERENCES' AS `rel_type`,
  d.`dataset_version_id` AS `src_id`,
  TRIM(t.`artifact_id`) AS `dst_id`,
  'dwd_dataset_version_detail.artifact_refs' AS `reconcile_source`
FROM `dwd_dataset_version_detail` AS d
CROSS JOIN UNNEST(
  SPLIT(REGEXP_REPLACE(d.`artifact_refs`, '[\\[\\]"]', ''), ',')
) AS t (`artifact_id`)
WHERE d.`_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND d.`_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND d.`artifact_refs` IS NOT NULL
  AND TRIM(t.`artifact_id`) <> '';

-- 2.7 反向追溯入口：TRACED_TO（Badcase → 产物）
--     ⚠️ 表名与列名为本项目设计
SELECT
  'TRACED_TO' AS `rel_type`,
  b.`badcase_id` AS `src_id`,
  TRIM(t.`artifact_id`) AS `dst_id`,
  'dwd_badcase_detail.traced_artifact_ids' AS `reconcile_source`
FROM `dwd_badcase_detail` AS b
CROSS JOIN UNNEST(
  SPLIT(REGEXP_REPLACE(b.`traced_artifact_ids`, '[\\[\\]"]', ''), ',')
) AS t (`artifact_id`)
WHERE b.`_ingest_time` >= TIMESTAMP '2024-01-15 00:00:00'
  AND b.`_ingest_time` <  TIMESTAMP '2024-01-16 00:00:00'
  AND b.`traced_artifact_ids` IS NOT NULL
  AND TRIM(t.`artifact_id`) <> '';
