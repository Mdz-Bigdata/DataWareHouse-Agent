-- =====================================================================
-- 表名称: dw_store.dws_trade_order_summary_daily
-- 描述: 电商交易日汇总表 (Doris/StarRocks Model)
-- 物理数据源: DORIS
-- Modeler: @data-warehouse-modeler
--
-- 分区策略（2026-09-13 修订，修复「分区已过期导致每日跑批失败」）：
--   原 DDL 只有 p_202605 / p_202606 / p_202607 三档静态分区，末档上界
--   "2026-08-01"。job/batch/pipeline/batch_dws_trade_order_summary_daily_job.json
--   每天 02:00 跑 ETL，2026-08-01 起写入的数据没有任何分区可落，
--   INSERT 直接报 "Insert has filtered data / no partition for this key"。
--
--   修订内容：
--     ① 补齐缺口分区 p_202608 / p_202609（静态分区只能人工补）
--     ② 打开 dynamic_partition：此后按月自动向前滚动创建，不再需要人工维护
--
--   ⚠️ 刻意不设置 "dynamic_partition.start"：不设置即 Doris 默认
--      Integer.MIN_VALUE，语义是「永不自动删除历史分区」。
--      **不要**改成 "-3" 之类的负数 —— 那会让 Doris 每天自动 DROP 掉
--      超出窗口的历史分区，是不可逆的数据删除。历史分区的生命周期
--      请走人工归档流程，不要交给动态分区调度器。
-- =====================================================================

CREATE TABLE IF NOT EXISTS dw_store.dws_trade_order_summary_daily (
    dt DATE COMMENT "分区日期 (YYYY-MM-DD)",
    region_id INT COMMENT "区域 ID",
    region_name VARCHAR(50) COMMENT "区域名称",
    category_name VARCHAR(100) COMMENT "品类名称",
    gmv DOUBLE COMMENT "总交易额 (GMV)",
    order_count INT COMMENT "订单总量",
    refund_amount DOUBLE COMMENT "总退款金额",
    refund_count INT COMMENT "总退款量"
)
UNIQUE KEY(dt, region_id, category_name)
PARTITION BY RANGE(dt) (
    PARTITION p_202605 VALUES LESS THAN ("2026-06-01"),
    PARTITION p_202606 VALUES LESS THAN ("2026-07-01"),
    PARTITION p_202607 VALUES LESS THAN ("2026-08-01"),
    PARTITION p_202608 VALUES LESS THAN ("2026-09-01"),
    PARTITION p_202609 VALUES LESS THAN ("2026-10-01")
)
DISTRIBUTED BY HASH(region_id) BUCKETS 8
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1",
    "compression" = "zstd",
    -- 按月自动滚动分区：分区名 = prefix + yyyyMM（与既有 p_YYYYMM 命名一致）
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "MONTH",
    "dynamic_partition.prefix" = "p_",
    -- 预创建未来 3 个月，跑批永远有分区可落（月末跨月也不会踩空）
    "dynamic_partition.end" = "3",
    -- 不回补历史分区：历史缺口由 init/doris/migrations/ 下的 ALTER 脚本人工补
    "dynamic_partition.create_history_partition" = "false",
    "dynamic_partition.buckets" = "8",
    "dynamic_partition.replication_allocation" = "tag.location.default: 1"
);
