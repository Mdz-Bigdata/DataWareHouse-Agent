-- starrocks_lifecycle.sql —— 存储生命周期治理的 StarRocks 侧
--
-- 来源：a14.md 第四章②（dws_closed_loop_storage_cost_daily 由 StarRocks 离线聚合）、
--      第五章①（扫描决策 = StarRocks 定时任务 T+1）、第六章（成本看板与两条预算告警线）。
--
-- 架构：明细表 dwd_closed_loop_storage_lifecycle 是 Paimon 主键表，StarRocks 经
--      External Catalog 直查、**只读**；成本日表是 StarRocks 内表（主键模型），
--      看板从内表出。写 Paimon 明细表的永远只有 Flink 一家，避免双写乱序。
--
-- 目录：
--   §1 内表 DDL（成本日表 + 治理审计流水）
--   §2 扫描决策：三张候选清单视图（降冷 / 淘汰 / 删除）
--   §3 成本日表的 T+1 聚合任务
--   §4 成本看板 v_storage_cost_dashboard
--   §5 两条预算告警线

-- ===========================================================================
-- §1 内表 DDL
-- ===========================================================================

CREATE DATABASE IF NOT EXISTS `adas_ads`;

-- 存储成本日指标表（原文第四章②）。
-- 主键维度：统计日期 × 介质 × 分层 × 数据类型 × 来源域。
-- 分桶 2 —— 五维聚合后单日行数在千行量级，桶多了全是小文件。
CREATE TABLE IF NOT EXISTS `adas_ads`.`dws_closed_loop_storage_cost_daily` (
    `stat_date`             DATE            NOT NULL COMMENT '统计日期',
    `storage_media`         VARCHAR(32)     NOT NULL COMMENT 'oss_standard/oss_ia/oss_archive/oss_deep_archive/nas',
    `lifecycle_stage`       VARCHAR(32)     NOT NULL COMMENT 'hot/warm/cold/archive/pending_delete/deleted',
    `data_type`             VARCHAR(32)     NOT NULL COMMENT 'raw/intermediate/dataset/model/temp',
    `source_domain`         VARCHAR(64)     NOT NULL COMMENT '来源域',
    `total_capacity_tb`     DECIMAL(18, 6)  NULL COMMENT '容量合计（TB）',
    `daily_cost_yuan`       DECIMAL(18, 4)  NULL COMMENT '当日折算成本（元，按云厂商计价折算）',
    `preheat_volume_tb`     DECIMAL(18, 6)  NULL COMMENT '当日预热数据量（TB）',
    `evict_volume_tb`       DECIMAL(18, 6)  NULL COMMENT '当日淘汰数据量（TB）',
    `tier_down_volume_tb`   DECIMAL(18, 6)  NULL COMMENT '当日降冷数据量（TB）',
    `delete_volume_tb`      DECIMAL(18, 6)  NULL COMMENT '当日删除数据量（TB）',
    `nas_peak_usage`        DECIMAL(6, 4)   NULL COMMENT 'NAS 峰值使用率，持续 > 0.80 告警',
    `preheat_hit_rate`      DECIMAL(6, 4)   NULL COMMENT '预热命中率 = 训练预热命中 / 总预热请求',
    `archive_restore_count` INT             NULL COMMENT '归档取回次数，反哺保留期与降冷阈值调优'
)
PRIMARY KEY (`stat_date`, `storage_media`, `lifecycle_stage`, `data_type`, `source_domain`)
DISTRIBUTED BY HASH(`stat_date`) BUCKETS 2
PROPERTIES ("replication_num" = "1");

-- 治理审计流水（第三道安全闸：所有流转 / 淘汰 / 删除操作全量登记，可追溯、可复盘）。
-- ⚠️ 原文未明确，本项目设计：原文只要求「全量登记」，没给表名与字段。
CREATE TABLE IF NOT EXISTS `adas_ads`.`ads_storage_lifecycle_audit` (
    `audit_date`   DATE          NOT NULL COMMENT '审计日期',
    `run_id`       VARCHAR(64)   NOT NULL COMMENT '治理运行 ID（三级 ID 体系 run_ 前缀）',
    `data_id`      VARCHAR(128)  NOT NULL COMMENT '全局数据 ID',
    `file_path`    VARCHAR(1024) NOT NULL COMMENT '文件路径',
    `action`       VARCHAR(32)   NULL COMMENT 'preheat/evict/tier_down/restore/delete/blocked',
    `from_media`   VARCHAR(32)   NULL,
    `to_media`     VARCHAR(32)   NULL,
    `from_stage`   VARCHAR(32)   NULL,
    `to_stage`     VARCHAR(32)   NULL,
    `rule`         VARCHAR(64)   NULL COMMENT '命中的规则名，用于规则调优归因',
    `reason`       VARCHAR(1024) NULL,
    `volume_tb`    DECIMAL(18,6) NULL,
    `blocked_by`   VARCHAR(128)  NULL COMMENT '被哪道安全闸拦下',
    `audit_time`   DATETIME      NULL
)
PRIMARY KEY (`audit_date`, `run_id`, `data_id`, `file_path`)
DISTRIBUTED BY HASH(`audit_date`) BUCKETS 4
PROPERTIES ("replication_num" = "1");

-- ===========================================================================
-- §2 扫描决策（原文第五章①：StarRocks 定时任务 T+1，
--     扫描生命周期状态表，按规则逐条计算，产出降冷 / 淘汰 / 删除候选清单）
--
--   保留期表（原文第三章，天数逐字照抄）：
--     原始数据       标准 30 天  | 低频 30–90 天  | 归档 90–365 天 | 365 天后且血缘零引用
--     中间过程产物   标准 90 天  | 低频 90–180 天 | 归档 180–365 天| 365 天后且血缘零引用
--     数据集文件     标准 180 天 | 低频 180–365 天| 归档 365 天+   | 永久保留
--     模型文件       标准 90 天  | 低频 90–180 天 | 归档 180 天+   | 永久保留
--     临时文件       标准 7 天   | —             | —             | 7 天后自动删除
--
--   血缘保护：被数据集版本或训练任务引用的数据自动提升一档保留
--   （lineage_ref_count > 0 时给 30 天提档宽限期，⚠️ 宽限期天数为本项目从案例反推）。
-- ===========================================================================

-- 标准存储保留天数（按数据类型）
CREATE VIEW IF NOT EXISTS `adas_ads`.`v_storage_retention_rule` AS
SELECT 'raw'          AS `data_type`,  30 AS `standard_days`,   90 AS `ia_until_days`,  365 AS `archive_until_days`,  365 AS `delete_after_days`
UNION ALL SELECT 'intermediate',       90,                     180,                    365,                          365
UNION ALL SELECT 'dataset',           180,                     365,                   NULL,                         NULL
UNION ALL SELECT 'model',              90,                     180,                   NULL,                         NULL
UNION ALL SELECT 'temp',                7,                    NULL,                   NULL,                            7;

-- 明细表 + 规则，算出每条数据「规则期望的分层」。
-- 提档宽限期 30 天：lineage_ref_count > 0 时，标准存储期顺延 30 天
-- （案例：原始数据标准 30 天 + 提档 30 天 = 在标准存储停留 2 个月，03-01 → 04-30）。
CREATE VIEW IF NOT EXISTS `adas_ads`.`v_storage_lifecycle_target` AS
SELECT
    l.`data_id`,
    l.`file_path`,
    l.`storage_media`,
    l.`lifecycle_stage`,
    l.`data_type`,
    l.`source_domain`,
    l.`file_size_bytes`,
    l.`checksum_md5`,
    l.`lineage_ref_count`,
    l.`whitelist_flag`,
    l.`access_count_30d`,
    l.`expire_policy`,
    l.`evict_status`,
    DATEDIFF(CURRENT_DATE(), CAST(l.`create_time` AS DATE))                       AS `age_days`,
    DATEDIFF(CURRENT_DATE(), CAST(COALESCE(l.`last_access_time`, l.`create_time`) AS DATE)) AS `no_access_days`,
    r.`standard_days` + IF(l.`lineage_ref_count` > 0, 30, 0)                      AS `effective_standard_days`,
    r.`ia_until_days`,
    r.`archive_until_days`,
    r.`delete_after_days`,
    CASE
        WHEN r.`ia_until_days` IS NULL AND r.`archive_until_days` IS NULL
             AND DATEDIFF(CURRENT_DATE(), CAST(l.`create_time` AS DATE)) >= r.`standard_days`
            THEN 'pending_delete'                            -- 临时文件：7 天后自动删除
        WHEN DATEDIFF(CURRENT_DATE(), CAST(l.`create_time` AS DATE))
             < r.`standard_days` + IF(l.`lineage_ref_count` > 0, 30, 0)
            THEN 'warm'                                      -- 温 H2：创建 30 天内或 30 天内有访问
        WHEN r.`ia_until_days` IS NOT NULL
             AND DATEDIFF(CURRENT_DATE(), CAST(l.`create_time` AS DATE)) < r.`ia_until_days`
            THEN 'cold'                                      -- 冷 C1：OSS 低频
        WHEN r.`archive_until_days` IS NULL
             OR DATEDIFF(CURRENT_DATE(), CAST(l.`create_time` AS DATE)) < r.`archive_until_days`
            THEN 'archive'                                   -- 归档 C2：OSS 归档 / 深度归档
        ELSE 'pending_delete'                                -- 删除 D：过保留期
    END                                                                           AS `target_stage`
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` AS l
JOIN `adas_ads`.`v_storage_retention_rule` AS r
  ON l.`data_type` = r.`data_type`
WHERE l.`lifecycle_stage` <> 'deleted'
  AND l.`whitelist_flag` = FALSE;   -- 白名单豁免：跳过分层流转，不参与自动淘汰

-- ---- 候选清单一：降冷 ----
CREATE VIEW IF NOT EXISTS `adas_ads`.`v_lifecycle_candidate_tier_down` AS
SELECT
    t.*,
    CASE t.`target_stage` WHEN 'cold' THEN 'oss_ia' ELSE 'oss_archive' END AS `target_media`,
    t.`file_size_bytes` / 1099511627776.0                                  AS `volume_tb`
FROM `adas_ads`.`v_storage_lifecycle_target` AS t
WHERE t.`storage_media` <> 'nas'
  AND (
        (t.`lifecycle_stage` = 'warm' AND t.`target_stage` IN ('cold', 'archive'))
        -- 冷 → 归档：连续 90 天无访问（案例口径；第二章通用阈值写的是 180 天，
        -- 两处口径不一致，本项目按案例落地，详见 lifecycle/policy.py 的 ⚠️）
     OR (t.`lifecycle_stage` = 'cold' AND t.`target_stage` = 'archive' AND t.`no_access_days` >= 90)
      );

-- ---- 候选清单二：NAS 淘汰（四条场景中可由 SQL 直接判定的三条）----
-- 场景一 训练任务完成后：任务已结束且副本未被下一任务引用，7 天缓冲期后淘汰
-- 场景二 Checkpoint 产物：NAS 仅保留最新 N 个版本（默认 3），历史版本转存 OSS 归档
-- 场景三 容量水位：NAS 使用率 > 80% 触发水位淘汰，按 LRU 优先淘汰近 30 天无访问数据
-- 场景四 白名单豁免：已在 v_storage_lifecycle_target 里过滤掉
--
-- 注意：checksum 一致才允许真正释放副本（第二道安全闸），本视图只出候选，
--       校验由存储执行服务执行。
CREATE VIEW IF NOT EXISTS `adas_ads`.`v_lifecycle_candidate_evict` AS
SELECT
    t.`data_id`, t.`file_path`, t.`data_type`, t.`source_domain`,
    t.`file_size_bytes`, t.`checksum_md5`, t.`access_count_30d`,
    t.`file_size_bytes` / 1099511627776.0 AS `volume_tb`,
    'training_done'                       AS `evict_scenario`,
    'oss_standard'                        AS `target_media`
FROM `adas_ads`.`v_storage_lifecycle_target` AS t
JOIN `paimon_catalog`.`adas_lakehouse`.`ods_training_task` AS tr
  ON t.`data_id` = tr.`data_id`
WHERE t.`storage_media` = 'nas'
  AND tr.`task_status` = 'finished'
  AND DATEDIFF(CURRENT_DATE(), CAST(tr.`end_time` AS DATE)) >= 7   -- 7 天缓冲期
  AND NOT EXISTS (
        SELECT 1 FROM `paimon_catalog`.`adas_lakehouse`.`ods_training_task` AS nx
        WHERE nx.`data_id` = t.`data_id` AND nx.`task_status` IN ('pending', 'running')
      )
UNION ALL
SELECT
    t.`data_id`, t.`file_path`, t.`data_type`, t.`source_domain`,
    t.`file_size_bytes`, t.`checksum_md5`, t.`access_count_30d`,
    t.`file_size_bytes` / 1099511627776.0 AS `volume_tb`,
    'checkpoint'                          AS `evict_scenario`,
    'oss_archive'                         AS `target_media`   -- 历史版本转存 OSS 归档
FROM `adas_ads`.`v_storage_lifecycle_target` AS t
JOIN (
    -- ⚠️ 原文未明确，本项目设计：原文说「NAS 仅保留最新 N 个版本」，
    -- 但没说版本按什么分组。这里按 data_id 分组、按落盘时间倒序排名——
    -- 同一训练任务的 Checkpoint 共享同一个 data_id，rn=1 即最新版本。
    -- 装配阶段若训练域提供了 model_version 维度，改 PARTITION BY 即可。
    SELECT `data_id`, `file_path`,
           ROW_NUMBER() OVER (PARTITION BY `data_id` ORDER BY `create_time` DESC) AS `rn`
    FROM `paimon_catalog`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
    WHERE `data_type` = 'model' AND `storage_media` = 'nas'
) AS ck ON t.`data_id` = ck.`data_id` AND t.`file_path` = ck.`file_path`
WHERE ck.`rn` > 3                                                -- 仅保留最新 N 个版本，默认 3
UNION ALL
SELECT
    t.`data_id`, t.`file_path`, t.`data_type`, t.`source_domain`,
    t.`file_size_bytes`, t.`checksum_md5`, t.`access_count_30d`,
    t.`file_size_bytes` / 1099511627776.0 AS `volume_tb`,
    'watermark'                           AS `evict_scenario`,
    'oss_standard'                        AS `target_media`
FROM `adas_ads`.`v_storage_lifecycle_target` AS t
WHERE t.`storage_media` = 'nas'
  AND t.`access_count_30d` = 0                                    -- LRU：近 30 天无访问
  -- 水位条件 nas_peak_usage > 0.80 由调度侧判定后再拉本清单
;

-- ---- 候选清单三：删除（三重确认全部写进 WHERE，SQL 层就拦住）----
-- 原文第五章第一道闸：过保留期 + 血缘零引用 + 白名单校验，三者同时满足才允许删除。
CREATE VIEW IF NOT EXISTS `adas_ads`.`v_lifecycle_candidate_delete` AS
SELECT
    t.*,
    t.`file_size_bytes` / 1099511627776.0 AS `volume_tb`
FROM `adas_ads`.`v_storage_lifecycle_target` AS t
WHERE t.`target_stage` = 'pending_delete'
  AND t.`delete_after_days` IS NOT NULL      -- 数据集/模型「永久保留」，没有删除出口
  AND t.`age_days` >= t.`delete_after_days`  -- 三重确认之一：过保留期
  AND t.`lineage_ref_count` = 0              -- 三重确认之二：血缘零引用
  AND t.`whitelist_flag` = FALSE;            -- 三重确认之三：白名单校验

-- ---- 被拦下的删除：必须可见，不能悄悄吞掉（原文案例第 7 个快照点）----
CREATE VIEW IF NOT EXISTS `adas_ads`.`v_lifecycle_delete_blocked` AS
SELECT
    t.`data_id`, t.`file_path`, t.`data_type`, t.`age_days`,
    t.`delete_after_days`, t.`lineage_ref_count`, t.`whitelist_flag`,
    CONCAT_WS(',',
        IF(t.`delete_after_days` IS NULL OR t.`age_days` < t.`delete_after_days`, 'past_retention', NULL),
        IF(t.`lineage_ref_count` <> 0, 'zero_lineage_ref', NULL),
        IF(t.`whitelist_flag`, 'not_whitelisted', NULL)
    ) AS `failed_conditions`
FROM `adas_ads`.`v_storage_lifecycle_target` AS t
WHERE t.`target_stage` = 'pending_delete'
  AND NOT (
        t.`delete_after_days` IS NOT NULL
    AND t.`age_days` >= t.`delete_after_days`
    AND t.`lineage_ref_count` = 0
    AND t.`whitelist_flag` = FALSE
      );

-- ===========================================================================
-- §3 成本日表 T+1 聚合任务
--
-- 单价用原文第四章案例的示例单价（NAS 1.0 / OSS 标准 0.12 / 低频 0.06 /
-- 归档 0.018 元·GB⁻¹·月⁻¹；深度归档 0.006 为按第二章 0.05x 相对量级折算的
-- ⚠️ 本项目推断值），月费按 30 天折算到天。
-- 生产请把 v_storage_unit_price 换成云厂商实际计价表——原文亦注明
-- 「示意值，以云厂商实际计价为准」「按云厂商计价折算」。
-- ===========================================================================

CREATE VIEW IF NOT EXISTS `adas_ads`.`v_storage_unit_price` AS
SELECT 'nas'              AS `storage_media`, 1.0   AS `yuan_per_gb_month`, 8.0  AS `relative_price`
UNION ALL SELECT 'oss_standard',               0.12,                        1.0
UNION ALL SELECT 'oss_ia',                     0.06,                        0.5
UNION ALL SELECT 'oss_archive',                0.018,                       0.15
UNION ALL SELECT 'oss_deep_archive',           0.006,                       0.05;

SUBMIT TASK IF NOT EXISTS `task_storage_cost_daily`
SCHEDULE START('2026-09-01 02:00:00') EVERY (INTERVAL 1 DAY)
AS
INSERT INTO `adas_ads`.`dws_closed_loop_storage_cost_daily`
SELECT
    DATE_SUB(CURRENT_DATE(), 1)                                      AS `stat_date`,
    l.`storage_media`,
    l.`lifecycle_stage`,
    l.`data_type`,
    l.`source_domain`,
    SUM(l.`file_size_bytes`) / 1099511627776.0                       AS `total_capacity_tb`,
    SUM(l.`file_size_bytes`) / 1073741824.0 * MAX(p.`yuan_per_gb_month`) / 30.0
                                                                     AS `daily_cost_yuan`,
    0, 0, 0, 0,                                                       -- 动作量由回写步更新
    0, 0, 0
FROM `paimon_catalog`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` AS l
JOIN `adas_ads`.`v_storage_unit_price` AS p ON l.`storage_media` = p.`storage_media`
WHERE l.`lifecycle_stage` <> 'deleted'
GROUP BY l.`storage_media`, l.`lifecycle_stage`, l.`data_type`, l.`source_domain`;

-- ===========================================================================
-- §4 成本看板 v_storage_cost_dashboard（原文第六章）
--    ⚠️ 视图名刻意不叫 ads_storage_cost_dashboard：那个名字属于 catalog 登记的 ADS
--    Paimon 表，ads/schema.py 会把它物化成 `adas_ads` 库里的同名**内表**，
--    同库重名会直接撞车。本视图是 StarRocks 侧的即时汇总口径，两者并存。
--
-- 指标口径（原文表格）：
--   总存储容量 / 月存储成本 —— 各介质容量加总；容量 × 介质单价折算
--   分层占比               —— NAS 热 / OSS 标准 / 低频 / 归档四档容量分布
--   治理动作量             —— 日预热 / 降冷 / 淘汰 / 删除数据量
--   成本节省额             —— 治理释放成本 = 无治理基线成本 − 实际成本
--   NAS 峰值使用率 / 预热命中率 —— 容量水位监控；训练预热命中 / 总预热请求
--   归档取回次数           —— 冷数据取回频次
--
-- ⚠️ 原文未明确「无治理基线」的介质假设，本项目取「全部按 OSS 标准存储计价」：
--   第四章案例的基线含 NAS 双份常驻（¥2,880 + ¥346 = ¥3,226），
--   那个口径只适用于上过 NAS 的训练数据，整湖套用会把节省额吹大。
-- ===========================================================================

CREATE VIEW IF NOT EXISTS `adas_ads`.`v_storage_cost_dashboard` AS
SELECT
    `stat_date`,
    SUM(`total_capacity_tb`)                                         AS `total_capacity_tb`,
    SUM(`total_capacity_tb`) / 1024.0                                AS `total_capacity_pb`,
    SUM(`daily_cost_yuan`)                                           AS `daily_cost_yuan`,
    SUM(`daily_cost_yuan`) * 30                                      AS `monthly_cost_yuan`,
    -- 分层占比：热 / 标准 / 低频 / 归档（原文月末复盘样例 6% / 24% / 38% / 32%）
    SUM(IF(`storage_media` = 'nas',          `total_capacity_tb`, 0)) / SUM(`total_capacity_tb`) AS `hot_ratio`,
    SUM(IF(`storage_media` = 'oss_standard', `total_capacity_tb`, 0)) / SUM(`total_capacity_tb`) AS `standard_ratio`,
    SUM(IF(`storage_media` = 'oss_ia',       `total_capacity_tb`, 0)) / SUM(`total_capacity_tb`) AS `ia_ratio`,
    SUM(IF(`storage_media` IN ('oss_archive', 'oss_deep_archive'), `total_capacity_tb`, 0))
        / SUM(`total_capacity_tb`)                                   AS `archive_ratio`,
    -- 治理动作量
    SUM(`preheat_volume_tb`)                                         AS `preheat_volume_tb`,
    SUM(`tier_down_volume_tb`)                                       AS `tier_down_volume_tb`,
    SUM(`evict_volume_tb`)                                           AS `evict_volume_tb`,
    SUM(`delete_volume_tb`)                                          AS `delete_volume_tb`,
    -- 成本节省额 = 无治理基线成本（全量 OSS 标准 0.12 元/GB·月）− 实际成本
    SUM(`total_capacity_tb`) * 1024 * 1024 * 0.12 / 30 - SUM(`daily_cost_yuan`)
                                                                     AS `daily_saving_yuan`,
    MAX(`nas_peak_usage`)                                            AS `nas_peak_usage`,
    MAX(`preheat_hit_rate`)                                          AS `preheat_hit_rate`,
    MAX(`archive_restore_count`)                                     AS `archive_restore_count`
FROM `adas_ads`.`dws_closed_loop_storage_cost_daily`
GROUP BY `stat_date`;

-- ===========================================================================
-- §5 预算告警线（原文第六章）
--   · 存储成本环比增长 > 10%      自动告警
--   · NAS 使用率持续 > 80%        自动告警
-- 健康区间参照（原文月末复盘样例）：成本环比 +1.8%（数据量增速 35%，
-- 即成本增速约为数据增速的 1/20）、预热命中率 91%、归档取回 23 次/日。
-- ===========================================================================

CREATE VIEW IF NOT EXISTS `adas_ads`.`v_storage_budget_alert` AS
SELECT
    d.`stat_date`,
    d.`monthly_cost_yuan`,
    m.`monthly_cost_yuan`                                            AS `prev_monthly_cost_yuan`,
    (d.`monthly_cost_yuan` - m.`monthly_cost_yuan`) / m.`monthly_cost_yuan`
                                                                     AS `cost_mom_growth`,
    d.`nas_peak_usage`,
    d.`preheat_hit_rate`,
    d.`archive_restore_count`,
    CONCAT_WS(',',
        IF((d.`monthly_cost_yuan` - m.`monthly_cost_yuan`) / m.`monthly_cost_yuan` > 0.10,
           'cost_mom_growth', NULL),                                 -- 告警线一：> 10%
        IF(d.`nas_peak_usage` > 0.80, 'nas_usage', NULL)             -- 告警线二：持续 > 80%
    )                                                                AS `alerts`
FROM `adas_ads`.`v_storage_cost_dashboard` AS d
LEFT JOIN `adas_ads`.`v_storage_cost_dashboard` AS m
       ON m.`stat_date` = DATE_SUB(d.`stat_date`, 30);
