-- lifecycle_writeback.sql —— 五步闭环第 ④ 步「回写」的 Flink SQL 形态
--
-- 来源：a14.md 第五章「④ 回写 | 存储执行服务 | 结果回写生命周期状态表，更新成本日表」。
--
-- 生产上回写通常由存储执行服务经 Flink SQL Gateway 提交（见
-- adas_lakehouse.lifecycle.repository.PaimonRepository），本文件给的是同一批语句的
-- 手工/批处理版本，用于补数与回溯。
--
-- 铁律提醒（原文第三章）：淘汰 ≠ 删除。淘汰只把 storage_media 从 nas 改回 OSS，
-- 绝不会把 lifecycle_stage 改成 deleted——OSS 始终是事实源。

SET 'execution.runtime-mode' = 'batch';
SET 'pipeline.name' = 'lifecycle_writeback';

-- ---------------------------------------------------------------------------
-- ① 降冷回写：标准 → 低频 → 归档
--    温度只降不升（升温只能由「归档取回」触发，见 ③）。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    `data_id`, `file_path`, `artifact_id`,
    CASE `lifecycle_stage` WHEN 'cold' THEN 'oss_ia' ELSE 'oss_archive' END AS `storage_media`,
    `lifecycle_stage`,
    `data_type`, `source_domain`, `file_size_bytes`, `checksum_md5`,
    `create_time`,
    CURRENT_TIMESTAMP AS `stage_entered_at`,
    `last_access_time`, `access_count_30d`, `lineage_ref_count`,
    `whitelist_flag`, `expire_policy`, `preheat_task_id`, `evict_status`,
    `_ingest_time`,
    CURRENT_TIMESTAMP AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
-- 占位谓词：实际由执行服务按当轮成功的候选清单替换为 (data_id, file_path) IN (...)
WHERE 1 = 0;

-- ---------------------------------------------------------------------------
-- ② 淘汰回写：清 NAS 副本，介质回 OSS 标准，evict_status 置 done
--    前置条件已由执行服务的 checksum 闸把关，不一致的不会进入这条语句。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    `data_id`, `file_path`, `artifact_id`,
    'oss_standard' AS `storage_media`,
    'warm'         AS `lifecycle_stage`,
    `data_type`, `source_domain`, `file_size_bytes`, `checksum_md5`,
    `create_time`,
    CURRENT_TIMESTAMP AS `stage_entered_at`,
    `last_access_time`, `access_count_30d`, `lineage_ref_count`,
    `whitelist_flag`, `expire_policy`, `preheat_task_id`,
    'done' AS `evict_status`,
    `_ingest_time`,
    CURRENT_TIMESTAMP AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
WHERE 1 = 0;

-- ---------------------------------------------------------------------------
-- ③ 归档取回回写：回升温层 + 重置访问计时
--    原文第五章第四道安全闸：「归档数据标准恢复 ≤ 4 小时，
--    取回后自动回升温层并重置访问计时」。
--    「重置访问计时」= last_access_time 置当前、access_count_30d 归零，
--    这样取回后的 30 天里它不会又被当成冷数据降回去。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    `data_id`, `file_path`, `artifact_id`,
    'oss_standard' AS `storage_media`,
    'warm'         AS `lifecycle_stage`,
    `data_type`, `source_domain`, `file_size_bytes`, `checksum_md5`,
    `create_time`,
    CURRENT_TIMESTAMP AS `stage_entered_at`,
    CURRENT_TIMESTAMP AS `last_access_time`,
    0 AS `access_count_30d`,
    `lineage_ref_count`, `whitelist_flag`, `expire_policy`, `preheat_task_id`,
    `evict_status`,
    `_ingest_time`,
    CURRENT_TIMESTAMP AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
WHERE 1 = 0;

-- ---------------------------------------------------------------------------
-- ④ 删除回写：只标 deleted，不物理删除生命周期记录本身
--    记录留着才有「审计留痕」（第三道安全闸）。
--    这里再加一次删除三重确认的 WHERE 兜底——纵深防御，
--    即便执行服务的候选清单算错了，这条 SQL 也不会把有引用的数据标成已删。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    `data_id`, `file_path`, `artifact_id`, `storage_media`,
    'deleted' AS `lifecycle_stage`,
    `data_type`, `source_domain`, `file_size_bytes`, `checksum_md5`,
    `create_time`,
    CURRENT_TIMESTAMP AS `stage_entered_at`,
    `last_access_time`, `access_count_30d`, `lineage_ref_count`,
    `whitelist_flag`, `expire_policy`, `preheat_task_id`, `evict_status`,
    `_ingest_time`,
    CURRENT_TIMESTAMP AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
WHERE `lineage_ref_count` = 0        -- 三重确认之二：血缘零引用
  AND `whitelist_flag` = FALSE       -- 三重确认之三：白名单校验
  AND (                              -- 三重确认之一：过保留期（按数据类型的保留期表）
        (`data_type` = 'raw'          AND `create_time` < TIMESTAMPADD(DAY, -365, CURRENT_TIMESTAMP))
     OR (`data_type` = 'intermediate' AND `create_time` < TIMESTAMPADD(DAY, -365, CURRENT_TIMESTAMP))
     OR (`data_type` = 'temp'         AND `create_time` < TIMESTAMPADD(DAY, -7,   CURRENT_TIMESTAMP))
     -- dataset / model 原文写「永久保留」，这里不给删除出口，是有意为之
      )
  AND 1 = 0;   -- 占位：执行服务按当轮候选清单替换为 (data_id, file_path) IN (...)

-- ---------------------------------------------------------------------------
-- ⑤ 成本日表落湖副本：从明细表直接聚合出 dws_closed_loop_storage_cost_daily
--    维度：日期 × 介质 × 分层 × 数据类型 × 来源域（原文第四章②）。
--    单价按原文案例的示例单价（NAS 1.0 / 标准 0.12 / 低频 0.06 / 归档 0.018 元/GB·月，
--    深度归档 0.006 为按 0.05x 相对量级折算的 ⚠️ 本项目推断值），
--    月费按 30 天折算到天。生产请换成云厂商实际计价。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dws_closed_loop_storage_cost_daily`
SELECT
    CAST(CURRENT_DATE AS DATE)                        AS `stat_date`,
    `storage_media`,
    `lifecycle_stage`,
    `data_type`,
    `source_domain`,
    CAST(SUM(`file_size_bytes`) / 1099511627776.0 AS DECIMAL(18, 6))  AS `total_capacity_tb`,
    CAST(
        SUM(`file_size_bytes`) / 1073741824.0
        * CASE `storage_media`
              WHEN 'nas'              THEN 1.0
              WHEN 'oss_standard'     THEN 0.12
              WHEN 'oss_ia'           THEN 0.06
              WHEN 'oss_archive'      THEN 0.018
              WHEN 'oss_deep_archive' THEN 0.006
              ELSE 0.0
          END / 30.0
        AS DECIMAL(18, 4)
    )                                                 AS `daily_cost_yuan`,
    CAST(0 AS DECIMAL(18, 6))                         AS `preheat_volume_tb`,
    CAST(0 AS DECIMAL(18, 6))                         AS `evict_volume_tb`,
    CAST(0 AS DECIMAL(18, 6))                         AS `tier_down_volume_tb`,
    CAST(0 AS DECIMAL(18, 6))                         AS `delete_volume_tb`,
    CAST(0 AS DECIMAL(6, 4))                          AS `nas_peak_usage`,
    CAST(0 AS DECIMAL(6, 4))                          AS `preheat_hit_rate`,
    0                                                 AS `archive_restore_count`,
    CURRENT_TIMESTAMP                                 AS `_ingest_time`,
    CURRENT_TIMESTAMP                                 AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
WHERE `lifecycle_stage` <> 'deleted'
GROUP BY `storage_media`, `lifecycle_stage`, `data_type`, `source_domain`;
