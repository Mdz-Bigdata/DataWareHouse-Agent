-- =====================================================================
-- 模块名称: ETL - dws_trade_order_summary_daily
-- 执行引擎: DORIS
-- 上游数据源: dwd_trade_order_detail
-- 变量说明: 使用 ${run_date} 变量传入运行日期
-- Data Engineer: @data-warehouse-data-engineer
-- =====================================================================

WITH daily_orders AS (
    SELECT 
        DATE(created_at) AS dt,
        region_id,
        category_name,
        gmv,
        order_id
    FROM dw_store.dwd_trade_order_detail
    WHERE created_at >= '${run_date} 00:00:00'
      AND created_at <= '${run_date} 23:59:59'
),
daily_refunds AS (
    SELECT
        DATE(refund_time) AS dt,
        region_id,
        category_name,
        refund_amount,
        refund_id
    FROM dw_store.dwd_trade_refund_detail
    WHERE refund_time >= '${run_date} 00:00:00'
      AND refund_time <= '${run_date} 23:59:59'
)
INSERT INTO dw_store.dws_trade_order_summary_daily
SELECT 
    COALESCE(o.dt, r.dt) AS dt,
    COALESCE(o.region_id, r.region_id) AS region_id,
    dim.region_name,
    COALESCE(o.category_name, r.category_name) AS category_name,
    SUM(COALESCE(o.gmv, 0)) AS gmv,
    COUNT(DISTINCT o.order_id) AS order_count,
    SUM(COALESCE(r.refund_amount, 0)) AS refund_amount,
    COUNT(DISTINCT r.refund_id) AS refund_count
FROM daily_orders o
FULL JOIN daily_refunds r 
    ON o.region_id = r.region_id 
   AND o.category_name = r.category_name 
   AND o.dt = r.dt
LEFT JOIN dw_store.dim_region dim 
    ON COALESCE(o.region_id, r.region_id) = dim.region_id
GROUP BY 
    dt,
    region_id,
    region_name,
    category_name;
