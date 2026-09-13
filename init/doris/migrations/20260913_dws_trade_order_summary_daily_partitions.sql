-- =====================================================================
-- 迁移脚本: dw_store.dws_trade_order_summary_daily 分区过期修复
-- 日期: 2026-09-13
-- 关联: init/doris/dws_trade_order_summary_daily.sql
--
-- 为什么需要这个文件：
--   建表脚本是 `CREATE TABLE IF NOT EXISTS`，对**已经建好的线上表**是空操作。
--   线上表的分区末档仍停在 "2026-08-01"，
--   batch_dws_trade_order_summary_daily_job（每天 02:00）从 2026-08-01 起
--   已经连续失败。改建表语句救不了线上表，必须跑下面的 ALTER。
--
-- 执行方式（DORIS-DW 连接，需要 ALTER 权限）：
--   mysql -h<fe_host> -P<query_port> -u<user> -p<pass> < 本文件
--
-- 幂等性：
--   ADD PARTITION 带 IF NOT EXISTS，可重复执行。
--   SET (...) 属性重复设置同值也是幂等的。
-- =====================================================================

-- ── ① 补齐历史缺口分区（2026-08、2026-09）────────────────────────────
-- 静态分区只能人工补；这两个月的分区缺失就是跑批失败的直接原因。
ALTER TABLE dw_store.dws_trade_order_summary_daily
    ADD PARTITION IF NOT EXISTS p_202608 VALUES LESS THAN ("2026-09-01");

ALTER TABLE dw_store.dws_trade_order_summary_daily
    ADD PARTITION IF NOT EXISTS p_202609 VALUES LESS THAN ("2026-10-01");

-- ── ② 打开动态分区，从此按月自动滚动 ────────────────────────────────
-- ⚠️ 刻意不设置 "dynamic_partition.start"：不设置 = Doris 默认
--    Integer.MIN_VALUE = 永不自动删除历史分区。
--    **不要**补成 "-3" 之类的负数，那是每天自动 DROP 历史分区（不可逆删数据）。
ALTER TABLE dw_store.dws_trade_order_summary_daily SET (
    "dynamic_partition.enable" = "true",
    "dynamic_partition.time_unit" = "MONTH",
    "dynamic_partition.prefix" = "p_",
    "dynamic_partition.end" = "3",
    "dynamic_partition.create_history_partition" = "false",
    "dynamic_partition.buckets" = "8",
    "dynamic_partition.replication_allocation" = "tag.location.default: 1"
);

-- ── ③ 验证 ──────────────────────────────────────────────────────────
-- SHOW PARTITIONS FROM dw_store.dws_trade_order_summary_daily;
--   期望：p_202605 … p_202609 存在，且调度器补出 p_202610/p_202611/p_202612。
-- SHOW DYNAMIC PARTITION TABLES;
--   期望：本表 Enable=true，LastUpdateTime 有值，Msg 为空。

-- ── ④ 补数（分区补齐之后才可能成功）──────────────────────────────────
-- 2026-08-01 ~ 2026-09-12 的跑批全部失败，数据是空的，需要按天重跑
-- etl/doris/etl_dws_trade_order_summary_daily.sql，逐日传入 run_date：
--
--   for d in $(seq 0 42); do
--     RUN_DATE=$(date -d "2026-08-01 +${d} day" +%F)     # macOS: date -v+${d}d
--     sed "s/\${run_date}/${RUN_DATE}/g" \
--       etl/doris/etl_dws_trade_order_summary_daily.sql | mysql -h... -P... -u... -p...
--   done
--
-- 该 ETL 是 INSERT INTO（非 INSERT OVERWRITE），目标表是 UNIQUE KEY
-- (dt, region_id, category_name)，同键重跑走 upsert 覆盖，不会翻倍。
-- 补数前请先确认上游 dwd_trade_order_detail / dwd_trade_refund_detail
-- 这两张表在 2026-08 ~ 2026-09 区间有数据，否则补出来是空的。
