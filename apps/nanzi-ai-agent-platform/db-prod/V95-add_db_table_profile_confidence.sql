-- 原因：为数据库表画像增加置信度评分、是否为临时表标记及分析忽略标记
-- 需求背景：防止无业务价值的临时表/中间表干扰 AI 生成 SQL
-- 创建人：Antigravity
-- 创建时间：2026-07-11

ALTER TABLE `db_table_profiles`
  ADD COLUMN `confidence_score` SMALLINT NOT NULL DEFAULT 100 COMMENT '置信度/业务相关度评分(0-100)',
  ADD COLUMN `is_temporary` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为临时/备份表',
  ADD COLUMN `is_ignored` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '分析中是否忽略该表',
  ADD COLUMN `confidence_reason` TEXT DEFAULT NULL COMMENT '置信度判定与扣分原因';
