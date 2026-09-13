-- =============================================================================
-- 链路二（实时）：监听湖仓 changelog，产出血缘变更消息
--
-- 来源
--   [a13] 系列二·湖仓实战 第 5 篇《Paimon + Neo4j 湖图双引擎数据血缘追溯系统》
--         https://mp.weixin.qq.com/s/Yfrk2Z_izzzGSC_BQMsinQ
--   [a11] 系列一 第 4 篇《数据闭环全局 data_id 设计：贯穿智驾全链路的三级 ID 体系》
--         https://mp.weixin.qq.com/s/bxyDkxNgLg7qhkJLqCjd9A
--
-- [a13] 四·链路二原文：
--   「监听湖仓 binlog / 变更消息，MERGE 图库节点与关系 | 低延迟；失败不阻塞湖仓写入」
--
-- 红线①（[a13] 4.2）：实时链路失败不阻塞湖仓写入——湖仓永远是事实源。
--   本作业只负责「湖仓 changelog → Kafka 变更消息」，Kafka 是解耦点：
--   图库挂掉时消息堆在 topic 里，湖仓写入完全不受影响。
--
-- ⚠️ 原文未明确，本项目设计：
--   1. 原文只说「监听 binlog / 变更消息」，没指定中间件。Neo4j 没有官方 Flink 连接器，
--      因此这里落地成 Paimon changelog → Kafka topic `lineage.change.event`，
--      再由 adas_lakehouse.lineage.sync.RealtimeLineageSync 消费并 MERGE 进图库。
--   2. 消息 schema（table / op / row）为本项目约定，与 sync.handle_message 对齐。
--   3. -U（更新前镜像）与 -D（删除）在消费端丢弃：血缘节点只增不删
--      （[a13] 3.2 实践提醒：重刷绝不是删旧写新——旧产物与旧分支必须完整保留）。
--
-- 依赖的四张湖仓事实表（[a13] 3.1，归 catalog/tables/ 下各域模块所有，本文件只读）：
--   dwd_collect_clip_detail          追溯起点，图库 Clip 节点
--   dwd_production_artifact_detail   Artifact 节点；冗余 parent_artifact_id / superseded_by_artifact_id
--   dwd_production_run_detail        Run 节点；冗余 input·output_artifact_ids
--   dwd_dataset_version_detail       DatasetVersion 节点；冗余 artifact_refs
--   dwd_badcase_detail    ⚠️ 本项目补：原文五类节点含 Badcase，四张事实表却没有它
--
-- 运行：
--   ./bin/sql-client.sh -f flink/sql/lineage_realtime_sync.sql
-- =============================================================================

SET 'pipeline.name' = 'adas-lineage-realtime-sync';
-- 并行度与 checkpoint 间隔对齐 adas_lakehouse.config.FlinkConfig 的默认值
SET 'parallelism.default' = '2';
SET 'execution.checkpointing.interval' = '60s';
-- 实时链路读的是变更流，必须以 streaming 模式跑
SET 'execution.runtime-mode' = 'streaming';

CREATE CATALOG `paimon` WITH (
  'type' = 'paimon',
  'warehouse' = 's3://adas-lakehouse/warehouse'
);
USE CATALOG `paimon`;
USE `adas_lakehouse`;

-- -----------------------------------------------------------------------------
-- 输出：血缘变更消息 topic
--
-- 一个 topic 承载五类表的变更，按 table 字段分派。
-- 分区键取 node_id，保证同一个节点的变更保序——乱序会让 status 从 superseded
-- 倒退回 active（状态机在消费端会拦，但保序能从根上少一类告警）。
-- -----------------------------------------------------------------------------
CREATE TEMPORARY TABLE `lineage_change_event` (
  `table`     STRING,   -- 湖仓表名，消费端按它分派到对应的 Fact 类型
  `op`        STRING,   -- +I / +U / -U / -D（-U 与 -D 消费端丢弃）
  `node_id`   STRING,   -- 该行对应的图库节点 ID（三级 ID 之一），兼作 Kafka 分区键
  `row`       STRING,   -- 该行的 JSON，字段见各 Fact 类的 _FACT_FIELDS
  `event_time` TIMESTAMP(3)
) WITH (
  'connector' = 'kafka',
  'topic' = 'lineage.change.event',
  'properties.bootstrap.servers' = 'localhost:18692',
  'key.format' = 'raw',
  'key.fields' = 'node_id',
  'value.format' = 'json',
  'value.json.ignore-parse-errors' = 'false',
  'sink.partitioner' = 'default'
);

-- -----------------------------------------------------------------------------
-- 链路二主体：五张事实表的 changelog → 变更消息
--
-- 关键点：SELECT 里只取「图库建图需要的列」——节点 ID + 关系冗余字段 + 遍历键。
-- param_snapshot / params / content_hash 这些大属性一律不进消息体，
-- 它们是护栏「属性单一事实源」的黑名单，查询时回湖仓按 ID 取
-- （[a13] 5.1 原话：参数快照、质量分这些大属性全部回湖仓按 ID 批量取）。
-- -----------------------------------------------------------------------------
EXECUTE STATEMENT SET
BEGIN

-- ① Clip：追溯起点。纯 ID 节点，连遍历键都不需要。
INSERT INTO `lineage_change_event`
SELECT
  'dwd_collect_clip_detail' AS `table`,
  '+U' AS `op`,
  `data_id` AS `node_id`,
  JSON_OBJECT('data_id' VALUE `data_id`) AS `row`,
  `_ingest_time` AS `event_time`
FROM `dwd_collect_clip_detail`
/*+ OPTIONS('scan.mode' = 'latest') */;

-- ② Artifact：CONTAINS（data_id）+ DERIVED_FROM（parent_artifact_id）
--    + SUPERSEDED_BY（superseded_by_artifact_id）三类边的来源都在这一行里。
--    列名逐字取自 [a13] 3.1 的建表节选。
INSERT INTO `lineage_change_event`
SELECT
  'dwd_production_artifact_detail' AS `table`,
  '+U' AS `op`,
  `artifact_id` AS `node_id`,
  JSON_OBJECT(
    'artifact_id'        VALUE `artifact_id`,
    'data_id'            VALUE `data_id`,
    'step'               VALUE `stage`,
    'algo_version'       VALUE `algo_version`,
    'content_hash'       VALUE `content_hash`,
    'parent_artifact_id' VALUE `parent_artifact_id`,
    'superseded_by_artifact_id'      VALUE `superseded_by_artifact_id`,
    'status'             VALUE `artifact_status`
  ) AS `row`,
  `_ingest_time` AS `event_time`
FROM `dwd_production_artifact_detail`
/*+ OPTIONS('scan.mode' = 'latest') */;

-- ③ Run：INPUT / PRODUCED 边的来源。
--    红线②「failed 的 run 不产生 Artifact 节点，其输入输出不建边」在消费端
--    （RunFact.produces_edges）执行，这里照样发出来——失败的运行记录本身要能查到。
INSERT INTO `lineage_change_event`
SELECT
  'dwd_production_run_detail' AS `table`,
  '+U' AS `op`,
  `run_id` AS `node_id`,
  JSON_OBJECT(
    'run_id'              VALUE `run_id`,
    'stage'               VALUE `stage`,
    'status'              VALUE `run_status`,
    'algo_version'        VALUE `algo_version`,
    'input_artifact_ids'  VALUE `input_artifact_ids`,
    'output_artifact_ids' VALUE `output_artifact_ids`
  ) AS `row`,
  `_ingest_time` AS `event_time`
FROM `dwd_production_run_detail`
/*+ OPTIONS('scan.mode' = 'latest') */;

-- ④ DatasetVersion：REFERENCES 边的来源（artifact_refs）。
--    红线③：数据集绑定产物版本后，即使产物被替代，该数据集版本依然可复现——
--    所以 artifact_refs 里是具体 artifact_id（含 content_hash），不是「最新版」的符号引用。
INSERT INTO `lineage_change_event`
SELECT
  'dwd_dataset_version_detail' AS `table`,
  '+U' AS `op`,
  `dataset_version_id` AS `node_id`,
  JSON_OBJECT(
    'dataset_version_id' VALUE `dataset_version_id`,
    'dataset_id'         VALUE `dataset_id`,
    'version'            VALUE `version`,
    'status'             VALUE `version_status`,
    'artifact_refs'      VALUE `artifact_refs`
  ) AS `row`,
  `_ingest_time` AS `event_time`
FROM `dwd_dataset_version_detail`
/*+ OPTIONS('scan.mode' = 'latest') */;

-- ⑤ Badcase：TRACED_TO 边的来源，反向追溯的入口。
--    ⚠️ 表名与 traced_artifact_ids 列名为本项目设计——原文四张事实表里没有 Badcase 表，
--    但 Badcase 是 [a13] 二列出的五类节点之一，TRACED_TO 是七类关系之一。
INSERT INTO `lineage_change_event`
SELECT
  'dwd_badcase_detail' AS `table`,
  '+U' AS `op`,
  `badcase_id` AS `node_id`,
  JSON_OBJECT(
    'badcase_id'          VALUE `badcase_id`,
    'data_id'             VALUE `data_id`,
    'evaluation_type'     VALUE `evaluation_type`,
    'traced_artifact_ids' VALUE `traced_artifact_ids`
  ) AS `row`,
  `_ingest_time` AS `event_time`
FROM `dwd_badcase_detail`
/*+ OPTIONS('scan.mode' = 'latest') */;

END;
