-- =====================================================================
-- 表名称: dw_store.dws_trade_order_summary_daily
-- 描述: 电商交易日汇总表 (Doris/StarRocks Model)
-- 物理数据源: DORIS
-- Modeler: @data-warehouse-modeler
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
    PARTITION p_202607 VALUES LESS THAN ("2026-08-01")
)
DISTRIBUTED BY HASH(region_id) BUCKETS 8
PROPERTIES (
    "replication_allocation" = "tag.location.default: 1",
    "compression" = "zstd"
);
