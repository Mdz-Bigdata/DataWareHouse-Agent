# 数据模型文档 - dw_store.dws_trade_order_summary_daily

## 1. 概览
- **所属数据库**: dw_store
- **表名称**: dws_trade_order_summary_daily
- **数仓层级**: DWS
- **物理数据源**: DORIS
- **加工引擎**: DORIS

## 2. ETL 说明
- **调度频率**: 天级
- **写入方式**: 增量写入 (INSERT INTO)

## 3. 字段定义
| 编号 | 字段名 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| 1 | dt | DATE | NULL | 分区日期 (YYYY-MM-DD) |
| 2 | region_id | INT | NULL | 区域 ID |
| 3 | region_name | VARCHAR(50) | NULL | 区域名称 |
| 4 | category_name | VARCHAR(100) | NULL | 品类名称 |
| 5 | gmv | DOUBLE | 0.0 | 总交易额 (GMV) |
| 6 | order_count | INT | 0 | 订单总量 |
| 7 | refund_amount | DOUBLE | 0.0 | 总退款金额 |
| 8 | refund_count | INT | 0 | 总退款量 |

## 4. 建表 DDL
```sql
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

```
