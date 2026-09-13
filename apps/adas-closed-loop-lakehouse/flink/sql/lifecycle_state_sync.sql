-- lifecycle_state_sync.sql —— 元信息驱动：把散在各域的元信息汇成生命周期状态快照
--
-- 来源：a14.md 第一章流转链路「文件落 OSS（唯一事实源）→ 元信息实时入湖 →
--      训练任务创建触发预热上 NAS → 训练高吞吐读写 → 任务结束触发淘汰回 OSS
--      → OSS 按规则分层降冷」，以及第二章第一条原则「元信息驱动：一切决策由湖仓表
--      计算得出，不依赖人工判断」。
--
-- 本作业不做任何决策，只负责把决策所需的事实喂进 dwd_closed_loop_storage_lifecycle：
--   · 容量/校验和/文件类型  ← ods_data_file_meta（采集域文件元信息）
--   · 落湖时间/来源域        ← dwd_collect_clip_detail
--   · 血缘引用数            ← 血缘关系汇总（原文：「自 2.4 血缘关系汇总复用」）
--   · 近 30 天访问次数/最后访问时间 ← 访问记录
--   · 预热任务 ID           ← 训练域预热任务登记
--
-- ⚠️ 原文未明确，本项目设计：原文只说这些信息「由湖仓元信息计算得出」，
--    没有给出具体来源表与 JOIN 关系。下面的来源表按 11 数据域的既有表名推断，
--    装配阶段如与闭环域/训练域的实际表名不符，改这里的 FROM 即可，
--    目标表的字段契约由 adas_lakehouse.lifecycle.tables 保证不变。

SET 'execution.runtime-mode' = 'batch';
SET 'pipeline.name' = 'lifecycle_state_sync';

-- ---------------------------------------------------------------------------
-- ① 新增文件登记：OSS 落盘即建快照，初始状态一律 oss_standard / warm
--    （原文五级分层：温 H2 = 创建 30 天内或 30 天内有访问）
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    f.`data_id`,
    f.`object_key`                                    AS `file_path`,
    CAST(NULL AS STRING)                              AS `artifact_id`,
    'oss_standard'                                    AS `storage_media`,
    'warm'                                            AS `lifecycle_stage`,
    -- ⚠️ 本项目设计：文件类型 → 保留期表的五种数据类型的映射
    CASE
        WHEN f.`file_type` IN ('video', 'pointcloud', 'radar', 'imu', 'gps', 'can') THEN 'raw'
        WHEN f.`file_type` = 'checkpoint'                                            THEN 'model'
        WHEN f.`file_type` = 'dataset'                                               THEN 'dataset'
        WHEN f.`file_type` = 'tmp'                                                   THEN 'temp'
        ELSE 'intermediate'
    END                                               AS `data_type`,
    'collect'                                         AS `source_domain`,
    f.`file_size_bytes`,
    f.`checksum_md5`,
    c.`collect_start_time`                            AS `create_time`,
    c.`collect_start_time`                            AS `stage_entered_at`,
    CAST(NULL AS TIMESTAMP(3))                        AS `last_access_time`,
    0                                                 AS `access_count_30d`,
    0                                                 AS `lineage_ref_count`,
    FALSE                                             AS `whitelist_flag`,
    -- expire_policy 取值对齐原文：raw_365d / dataset_forever / model_top_n 等
    CASE
        WHEN f.`file_type` IN ('video', 'pointcloud', 'radar', 'imu', 'gps', 'can') THEN 'raw_365d'
        WHEN f.`file_type` = 'checkpoint'                                            THEN 'model_top_n'
        WHEN f.`file_type` = 'dataset'                                               THEN 'dataset_forever'
        WHEN f.`file_type` = 'tmp'                                                   THEN 'temp_7d'
        ELSE 'intermediate_365d'
    END                                               AS `expire_policy`,
    ''                                                AS `preheat_task_id`,
    'none'                                            AS `evict_status`,
    CURRENT_TIMESTAMP                                 AS `_ingest_time`,
    CURRENT_TIMESTAMP                                 AS `update_time`
FROM `paimon`.`adas_lakehouse`.`ods_data_file_meta` AS f
LEFT JOIN `paimon`.`adas_lakehouse`.`dwd_collect_clip_detail` AS c
       ON f.`data_id` = c.`data_id`
WHERE f.`object_key` IS NOT NULL
  AND NOT EXISTS (
        SELECT 1
        FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` AS l
        WHERE l.`data_id` = f.`data_id`
          AND l.`file_path` = f.`object_key`
      );

-- ---------------------------------------------------------------------------
-- ② 刷新血缘引用数 —— 删除保护的唯一依据（原文：「血缘定生死」）
--    lineage_ref_count > 0 → 删除三重确认第二条不通过，同时触发「提升一档保留」
--
-- ⚠️ 本项目设计：原文说这个值「自 2.4 血缘关系汇总复用」，本仓库里对应的是
--    闭环域的血缘关系表。下面按 dwd_closed_loop_lineage_relation 写，
--    实际表名以血缘子系统装配后的为准。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    l.`data_id`, l.`file_path`, l.`artifact_id`, l.`storage_media`, l.`lifecycle_stage`,
    l.`data_type`, l.`source_domain`, l.`file_size_bytes`, l.`checksum_md5`,
    l.`create_time`, l.`stage_entered_at`, l.`last_access_time`, l.`access_count_30d`,
    COALESCE(r.`ref_count`, 0)                        AS `lineage_ref_count`,
    l.`whitelist_flag`, l.`expire_policy`, l.`preheat_task_id`, l.`evict_status`,
    l.`_ingest_time`,
    CURRENT_TIMESTAMP                                 AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` AS l
LEFT JOIN (
    SELECT `data_id`, COUNT(1) AS `ref_count`
    FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_lineage_relation`
    WHERE `relation_status` = 'active'
    GROUP BY `data_id`
) AS r ON l.`data_id` = r.`data_id`
WHERE l.`lifecycle_stage` <> 'deleted'
  AND COALESCE(r.`ref_count`, 0) <> l.`lineage_ref_count`;

-- ---------------------------------------------------------------------------
-- ③ 刷新访问热度 —— 降冷驱动（last_access_time）与 LRU 淘汰依据（access_count_30d）
--    近 30 天的窗口宽度取自原文：「NAS 使用率 > 80% 触发水位淘汰，
--    按 LRU 优先淘汰近 30 天无访问数据」，也与表字段 access_count_30d 同名同义。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    l.`data_id`, l.`file_path`, l.`artifact_id`, l.`storage_media`, l.`lifecycle_stage`,
    l.`data_type`, l.`source_domain`, l.`file_size_bytes`, l.`checksum_md5`,
    l.`create_time`, l.`stage_entered_at`,
    COALESCE(a.`last_access_time`, l.`last_access_time`) AS `last_access_time`,
    COALESCE(a.`access_count_30d`, 0)                    AS `access_count_30d`,
    l.`lineage_ref_count`, l.`whitelist_flag`, l.`expire_policy`,
    l.`preheat_task_id`, l.`evict_status`,
    l.`_ingest_time`,
    CURRENT_TIMESTAMP                                    AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` AS l
LEFT JOIN (
    SELECT
        `data_id`,
        `file_path`,
        MAX(`access_time`) AS `last_access_time`,
        -- 窗口宽度 30 天：原文 LRU 口径
        SUM(CASE WHEN `access_time` >= TIMESTAMPADD(DAY, -30, CURRENT_TIMESTAMP)
                 THEN 1 ELSE 0 END) AS `access_count_30d`
    FROM `paimon`.`adas_lakehouse`.`ods_storage_access_log`
    GROUP BY `data_id`, `file_path`
) AS a
  ON l.`data_id` = a.`data_id` AND l.`file_path` = a.`file_path`
WHERE l.`lifecycle_stage` <> 'deleted';

-- ---------------------------------------------------------------------------
-- ④ 预热登记 —— 训练任务创建触发预热上 NAS，进热层 H1
--    原文五级分层 H1 的进入条件：「数据集关联活跃训练任务并预热」。
--    preheat_task_id 落表用于预热归因，也是预热命中率的分子来源。
-- ---------------------------------------------------------------------------
INSERT INTO `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle`
SELECT
    l.`data_id`, l.`file_path`, l.`artifact_id`,
    'nas'                                             AS `storage_media`,
    'hot'                                             AS `lifecycle_stage`,
    l.`data_type`, l.`source_domain`, l.`file_size_bytes`, l.`checksum_md5`,
    l.`create_time`,
    CURRENT_TIMESTAMP                                 AS `stage_entered_at`,
    l.`last_access_time`, l.`access_count_30d`, l.`lineage_ref_count`,
    l.`whitelist_flag`, l.`expire_policy`,
    p.`preheat_task_id`,
    'none'                                            AS `evict_status`,
    l.`_ingest_time`,
    CURRENT_TIMESTAMP                                 AS `update_time`
FROM `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` AS l
JOIN (
    SELECT `data_id`, MAX(`preheat_task_id`) AS `preheat_task_id`
    FROM `paimon`.`adas_lakehouse`.`ods_training_preheat_task`
    WHERE `task_status` = 'active'
    GROUP BY `data_id`
) AS p ON l.`data_id` = p.`data_id`
WHERE l.`storage_media` <> 'nas'
  AND l.`lifecycle_stage` <> 'deleted';
