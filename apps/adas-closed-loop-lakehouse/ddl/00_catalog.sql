-- ========================================================================
-- 湖仓建表 · 第 0 步：Paimon Catalog 与 Database
-- 由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：python3 scripts/export_ddl.py
-- 连接参数取自 config.settings()：MINIO_* / PAIMON_* 环境变量可覆盖。
-- 执行顺序：00_catalog → 10_ods → 20_dwd → 30_dws → 40_ads。
-- 10~40 用的是 `catalog`.`database`.`table` 全限定名，本脚本必须先跑。
-- ========================================================================

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
CREATE DATABASE IF NOT EXISTS `adas_lakehouse`;
USE `adas_lakehouse`;
