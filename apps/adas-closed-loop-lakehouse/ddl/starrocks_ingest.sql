-- ========================================================================
-- 三通道入湖 · StarRocks 侧 DDL
-- 由 adas_lakehouse.ingest.sql 生成，请勿手工编辑；重新生成：
--   python -m adas_lakehouse.ingest.sql --write
-- 双路查询（[a5] 第七章）：外部表即席查 Paimon + 内表物化服务监控大屏
-- ========================================================================

-- 路径一 · External Catalog 直查 Paimon：零冗余、零搬运，Paimon 保持单一事实源
CREATE EXTERNAL CATALOG IF NOT EXISTS paimon_catalog
PROPERTIES (
    "type" = "paimon",
    "paimon.catalog.type" = "filesystem",
    "paimon.catalog.warehouse" = "s3://adas-lakehouse/warehouse",
    "aws.s3.endpoint" = "http://localhost:18600",
    "aws.s3.access_key" = "adas",
    "aws.s3.secret_key" = "${MINIO_SECRET_KEY}",
    "aws.s3.enable_path_style_access" = "true"
);

CREATE DATABASE IF NOT EXISTS adas_ads;
USE adas_ads;

-- ------------------------------------------------------------------------
-- 即席查 1：采集文件元信息（大文件外置——湖仓只存路径与元信息）
-- 「找回文件 / 验证文件 / 管理文件」三类问题在这里都能回答
-- ------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_collect_file_meta AS
SELECT
    `data_id`,
    `file_id`,
    `file_type`,
    `object_key`,
    `file_size_bytes`,
    `checksum_md5`,
    `sensor_id`,
    `duration_sec`,
    `_ingest_time`,
    `_source_system`
FROM paimon_catalog.adas_lakehouse.ods_data_file_meta;

-- ------------------------------------------------------------------------
-- 即席查 2：入湖门禁拦截明细（五步异常闭环的可视化入口）
-- ⚠️ 依赖 quality 子系统登记的 ods_quality_issue；该表按 dt 分区
-- ------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_ingest_gate_rejection AS
SELECT
    `dt`,
    `source_channel`,
    `severity`,
    `is_compliance_issue`,
    `check_codes`,
    `issue_reason`,
    `subject_id`,
    `data_id`,
    `intercepted_at`
FROM paimon_catalog.adas_lakehouse.ods_quality_issue;

-- ------------------------------------------------------------------------
-- 路径二 · 内表物化：入湖通道日级监控（高频、毫秒级，服务监控大屏）
-- 注意：这是 StarRocks 内表，不是 Paimon 表，不参与全湖 88 张表的口径与四段式命名
-- ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_channel_daily (
    `dt`                 DATE         NOT NULL COMMENT "统计日期",
    `source_channel`     VARCHAR(64)  NOT NULL COMMENT "通道：Flink CDC / Kafka / OSS 合规上传",
    `target_table`       VARCHAR(128) NOT NULL COMMENT "目标 ODS 表",
    `ingest_cnt`         BIGINT       COMMENT "入湖行数",
    `reject_cnt`         BIGINT       COMMENT "门禁拒绝行数",
    `p0_reject_cnt`      BIGINT       COMMENT "P0 拒绝行数（含合规问题）",
    `compliance_reject_cnt` BIGINT    COMMENT "脱敏标记缺失等合规问题行数",
    `reject_rate`        DECIMAL(9,6) COMMENT "拒绝率",
    `bytes_externalized` BIGINT       COMMENT "外置到 OSS 的文件字节数（不进湖）",
    `updated_at`         DATETIME     COMMENT "刷新时间"
)
ENGINE = OLAP
PRIMARY KEY (`dt`, `source_channel`, `target_table`)
PARTITION BY RANGE (`dt`) ()
DISTRIBUTED BY HASH (`source_channel`) BUCKETS 2
PROPERTIES (
    "replication_num" = "1",
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "DAY",
    "dynamic_partition.start" = "-90",
    "dynamic_partition.end" = "3",
    "dynamic_partition.prefix" = "p",
    "dynamic_partition.buckets" = "2"
);
-- 说明：dynamic_partition.start = -90 与 [a5] 第八章「连续 90 天无访问」降冷口径对齐，
-- 监控内表只保留最近 90 天，更早的数据回湖里查（Paimon 是单一事实源）。

-- 日级刷新（调度器每日跑一次；OSS 通道的拦截明细来自隔离表）
INSERT OVERWRITE ingest_channel_daily
SELECT
    CAST(m.`dt` AS DATE)                                   AS `dt`,
    m.`source_channel`                                     AS `source_channel`,
    m.`target_table`                                       AS `target_table`,
    m.`ingest_cnt`                                         AS `ingest_cnt`,
    m.`reject_cnt`                                         AS `reject_cnt`,
    m.`p0_reject_cnt`                                      AS `p0_reject_cnt`,
    m.`compliance_reject_cnt`                              AS `compliance_reject_cnt`,
    CASE WHEN m.`ingest_cnt` + m.`reject_cnt` = 0 THEN 0
         ELSE m.`reject_cnt` / (m.`ingest_cnt` + m.`reject_cnt`) END AS `reject_rate`,
    m.`bytes_externalized`                                 AS `bytes_externalized`,
    NOW()                                                  AS `updated_at`
FROM (
    SELECT
        DATE_FORMAT(`_ingest_time`, '%Y-%m-%d')  AS `dt`,
        'OSS 合规上传'                            AS `source_channel`,
        'ods_data_file_meta'                       AS `target_table`,
        COUNT(*)                                  AS `ingest_cnt`,
        0                                         AS `reject_cnt`,
        0                                         AS `p0_reject_cnt`,
        0                                         AS `compliance_reject_cnt`,
        SUM(`file_size_bytes`)                    AS `bytes_externalized`
    FROM paimon_catalog.adas_lakehouse.ods_data_file_meta
    GROUP BY DATE_FORMAT(`_ingest_time`, '%Y-%m-%d')
    UNION ALL
    SELECT
        `dt`,
        `source_channel`,
        `target_table`,
        0,
        COUNT(*),
        SUM(CASE WHEN `severity` = 'P0' THEN 1 ELSE 0 END),
        SUM(CASE WHEN `is_compliance_issue` THEN 1 ELSE 0 END),
        0
    FROM paimon_catalog.adas_lakehouse.ods_quality_issue
    GROUP BY `dt`, `source_channel`, `target_table`
) m;

-- ------------------------------------------------------------------------
-- 存储成本口径备注（治理归 lifecycle 子系统，这里只登记换算系数）
--   OSS 低频存储约 0.5x 标准存储成本
--   OSS 归档存储约 0.15x 标准存储成本
-- ------------------------------------------------------------------------
