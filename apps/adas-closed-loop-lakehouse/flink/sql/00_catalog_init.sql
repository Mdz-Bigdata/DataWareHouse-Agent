-- ========================================================================
-- Flink SQL 会话前置 · 第 0 步：Paimon Catalog（S3/MinIO 后端）
--
-- flink/sql/ 下所有子系统脚本（ingest_* / quality_* / lineage_* / vector_*
-- / lifecycle_* / sampling_* / tags_*）都假定当前会话已经跑过本脚本——
-- 它们直接用 `adas_lakehouse`.`表名`，不再重复建 catalog。
--
-- 执行方式（任选其一）：
--   SQL Client : ./bin/sql-client.sh -f flink/sql/00_catalog_init.sql
--   SQL Gateway: 逐条 POST 到 http://localhost:18683（见 config.FlinkConfig）
--
-- 参数口径与 config.settings() 的默认值一致（docker/compose.yaml 起的那套）；
-- 换环境不要改本文件，改环境变量后重新导出：
--   MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY
--   PAIMON_WAREHOUSE_BUCKET / PAIMON_CATALOG / PAIMON_DATABASE / PAIMON_METASTORE
--   FLINK_PARALLELISM / FLINK_CHECKPOINT_INTERVAL_MS
--
-- ⚠️ 口令不落盘：s3.secret-key 只写 ${MINIO_SECRET_KEY} 占位符，由 Flink 的
--    环境变量 / secret 机制在提交时注入。任何情况下都不要把真实口令写进本文件。
-- ========================================================================

-- ------------------------------------------------------------------------
-- 1. Paimon Catalog
--    metastore=filesystem：元数据跟数据一起放在对象存储的 warehouse 目录下，
--    不依赖 Hive Metastore——这是本项目「一套存储、双引擎读」的前提：
--    Flink 写 Paimon，StarRocks 通过 External Catalog 直接读同一份元数据
--    （见 ddl/starrocks_*.sql 的 CREATE EXTERNAL CATALOG paimon_catalog）。
--    path.style.access=true 是 MinIO 必须项：MinIO 不支持 virtual-host 风格寻址。
-- ------------------------------------------------------------------------
CREATE CATALOG IF NOT EXISTS `paimon` WITH (
  'type' = 'paimon',
  'warehouse' = 's3://adas-lakehouse/warehouse',
  'metastore' = 'filesystem',
  's3.endpoint' = 'http://localhost:18600',
  's3.access-key' = 'adas',
  's3.secret-key' = '${MINIO_SECRET_KEY}',
  's3.path.style.access' = 'true'
);

USE CATALOG `paimon`;

-- ------------------------------------------------------------------------
-- 2. Database：88 张表（11 数据域 + 质量门禁伪域 1 张）全部建在这一个库里，
--    分层靠表名前缀（ods_/dwd_/dws_/ads_）区分，不再拆库——
--    跨层 JOIN 是 DWD 的日常，拆库只会平白多出一堆全限定名。
-- ------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `adas_lakehouse`;
USE `adas_lakehouse`;

-- ------------------------------------------------------------------------
-- 3. 会话参数
-- ------------------------------------------------------------------------
SET 'parallelism.default' = '2';

-- Checkpoint 是 CDC 断点续传的载体：作业重启后从上次位点继续读 binlog，
-- 不需要重新拉全量快照。间隔调小会增加小文件，调大会拉长故障恢复的重放窗口。
SET 'execution.checkpointing.interval' = '60000 ms';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';

-- Paimon 主键表自身即保证 upsert 语义，再让 Flink 做一次 upsert 物化纯属浪费状态。
SET 'table.exec.sink.upsert-materialize' = 'NONE';

-- 流式作业默认不做 idle 超时，防止低频分区（如某些 trigger_type）迟迟不推进水位线。
SET 'table.exec.source.idle-timeout' = '30 s';

-- ------------------------------------------------------------------------
-- 4. 建表从哪来
--    本脚本只建 catalog 与 database，不建表。88 张表的 DDL 由共享契约生成：
--      python3 scripts/export_ddl.py
--    产物 ddl/10_ods.sql / 20_dwd.sql / 30_dws.sql / 40_ads.sql 用的是
--    `paimon`.`adas_lakehouse`.`表名` 全限定名，可在本会话里直接执行。
--    校验：python -m adas_lakehouse.cli catalog-validate
-- ------------------------------------------------------------------------
