-- lifecycle_tables.sql —— 存储生命周期治理的两张表（Flink SQL / Paimon）
--
-- 来源：a14.md 第四章「元信息驱动：两张表撑起全部治理决策」。
-- 本文件由 adas_lakehouse.lifecycle.tables.render_ddl() 生成，改动请改 Python 侧的
-- TableSpec，不要手工编辑本文件，否则 reconcile_with_registry() 对不上账。
--
-- 物理策略（共享契约的硬性规则）：
--   · 两张表都不分区——主键 Upsert 且无明确分区维度（分区决策规则三）；
--     全湖仅 6 张分区表，这两张不在其中。
--   · Bucket：明细表 16（超大/高并发），成本日表 2（DWS 汇总），均取自五档。
--   · changelog-producer：DWD → lookup，DWS → full-compaction，无偏离。
--
-- 用法：
--   SET 'execution.runtime-mode' = 'batch';
--   然后整文件提交给 Flink SQL Gateway / SQL Client。

-- dwd_closed_loop_storage_lifecycle  [闭环域 / DWD]  存储生命周期状态明细表：全闭环每个数据单元的当前存储状态快照
-- 备注: 分区：不分区。主键 Upsert 且无明确分区维度（分区决策规则三）——治理扫描是全表按规则逐条算，不是按 dt 取范围，按 dt 分区反而每天全表重扫所有分区；全湖仅 6 张分区表，本表不在其中。Bucket=16：超大表 / 高并发写入——粒度到「每个数据单元的每个文件」，PB 级数据下行数远超任何业务明细表，且预热/淘汰/降冷每天批量 Upsert。changelog-producer=lookup：DWD 层默认，本表是典型 Upsert 明细，下游成本日表要拿到 -U/+U 才能算准当日治理动作量。命名：第四段 lifecycle 非 10 种标准粒度后缀之一，表名取自原文，已在 ACCEPTED_NAMING_WARNINGS 登记。
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` (
  `data_id` STRING NOT NULL COMMENT '全局数据 ID，关联 dwd_closed_loop_trace（主键一）',
  `file_path` STRING NOT NULL COMMENT '文件路径（主键二）',
  `storage_media` STRING COMMENT '当前介质：oss_standard / oss_ia / oss_archive / oss_deep_archive / nas',
  `lifecycle_stage` STRING COMMENT '分层状态：hot / warm / cold / archive / pending_delete / deleted',
  `last_access_time` TIMESTAMP(3) COMMENT '最后访问时间（降冷驱动）',
  `access_count_30d` INT COMMENT '近 30 天访问次数（LRU 淘汰依据）',
  `lineage_ref_count` INT COMMENT '下游血缘引用数——删除保护依据，自 2.4 血缘关系汇总复用',
  `whitelist_flag` BOOLEAN COMMENT '白名单豁免标志',
  `expire_policy` STRING COMMENT '保留策略：raw_365d / dataset_forever / model_top_n 等',
  `preheat_task_id` STRING COMMENT '最近预热任务 ID（预热归因）',
  `evict_status` STRING COMMENT '淘汰状态：none / pending / done / skipped',
  `artifact_id` STRING COMMENT '⚠️ 本项目补充：二级 ID，处理产物落盘时的归属',
  `data_type` STRING COMMENT '⚠️ 本项目补充：raw/intermediate/dataset/model/temp——保留期表按数据类型配置',
  `source_domain` STRING COMMENT '⚠️ 本项目补充：来源域，成本日表按此聚合',
  `file_size_bytes` BIGINT COMMENT '⚠️ 本项目补充：文件大小，容量与成本的计算基数',
  `checksum_md5` STRING COMMENT '⚠️ 本项目补充：淘汰校验闸比对 NAS 副本与 OSS 对象',
  `create_time` TIMESTAMP(3) COMMENT '⚠️ 本项目补充：落湖时间，温层「创建 30 天内」的基准',
  `stage_entered_at` TIMESTAMP(3) COMMENT '⚠️ 本项目补充：进入当前分层的时间，用于冷→归档计时',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `file_path`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dws_closed_loop_storage_cost_daily  [闭环域 / DWS]  存储成本日指标表：按日期 × 介质 × 分层 × 数据类型 × 来源域聚合容量与成本
-- 备注: 分区：不分区。五维聚合后行数很小（介质 5 × 分层 6 × 数据类型 5 × 来源域 ~11，单日上限千行量级），按 dt 分区只会产生大量小文件；全湖仅 6 张分区表，本表不在其中。Bucket=2：DWS 汇总表档位。changelog-producer=full-compaction：DWS 层默认，批量聚合后整体刷新。原文称本表由 StarRocks 离线聚合——Paimon 侧同名表作为落湖副本，StarRocks 内表 DDL 见 ddl/starrocks_lifecycle.sql。
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_closed_loop_storage_cost_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期（主键一）',
  `storage_media` STRING NOT NULL COMMENT '介质（主键二）',
  `lifecycle_stage` STRING NOT NULL COMMENT '分层（主键三）',
  `data_type` STRING NOT NULL COMMENT '数据类型（主键四）',
  `source_domain` STRING NOT NULL COMMENT '来源域（主键五）',
  `total_capacity_tb` DECIMAL(18,6) COMMENT '容量合计（TB）',
  `daily_cost_yuan` DECIMAL(18,4) COMMENT '当日折算成本（元，按云厂商计价折算）',
  `preheat_volume_tb` DECIMAL(18,6) COMMENT '当日预热数据量（TB）',
  `evict_volume_tb` DECIMAL(18,6) COMMENT '当日淘汰数据量（TB）',
  `tier_down_volume_tb` DECIMAL(18,6) COMMENT '当日降冷数据量（TB）',
  `delete_volume_tb` DECIMAL(18,6) COMMENT '当日删除数据量（TB）',
  `nas_peak_usage` DECIMAL(6,4) COMMENT 'NAS 峰值使用率（0~1，> 0.80 告警）',
  `preheat_hit_rate` DECIMAL(6,4) COMMENT '预热命中率 = 训练预热命中 / 总预热请求',
  `archive_restore_count` INT COMMENT '归档取回次数，反哺保留期与降冷阈值调优',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `storage_media`, `lifecycle_stage`, `data_type`, `source_domain`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);
