-- ========================================================================
-- 三通道入湖 · 公共前置：Paimon Catalog 与会话参数
-- 由 adas_lakehouse.ingest.sql 生成，请勿手工编辑；重新生成：
--   python -m adas_lakehouse.ingest.sql --write
-- 连接信息取自 config.settings()（MinIO / Paimon / Flink 三段）。
-- ========================================================================

CREATE CATALOG `paimon` WITH (
  'type' = 'paimon',
  'warehouse' = 's3://adas-lakehouse/warehouse',
  'metastore' = 'filesystem',
  's3.endpoint' = 'http://localhost:18600',
  's3.access-key' = 'adas',
  's3.secret-key' = '${MINIO_SECRET_KEY}',
  's3.path.style.access' = 'true'
);

USE CATALOG `paimon`;
CREATE DATABASE IF NOT EXISTS `adas_lakehouse`;
USE `adas_lakehouse`;

SET 'parallelism.default' = '2';
-- 断点续传：CDC 三阶段的第三阶段由 checkpoint 承载，作业重启自动从上次位点继续
SET 'execution.checkpointing.interval' = '60000 ms';
SET 'execution.checkpointing.mode' = 'EXACTLY_ONCE';
SET 'table.exec.sink.upsert-materialize' = 'NONE';
