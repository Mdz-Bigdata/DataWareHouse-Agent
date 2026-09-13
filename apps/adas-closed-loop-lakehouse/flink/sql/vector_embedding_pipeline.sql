-- =============================================================================
-- Embedding T+1 向量化流水线（增量识别 → 幂等写回 → 分区级索引刷新）
-- 来源：a7 第三章《Embedding 五步流水线：向量是怎么算出来的》
-- 本文件由 `python -m adas_lakehouse.vector.render_sql` 生成，请勿手改；
-- 参数改动请改 src/adas_lakehouse/vector/params.py。
-- =============================================================================

-- 节拍：T+1，每日凌晨 6 点前完成增量处理，新数据当日可检索。
-- 五步：
--   1. 增量识别：按 create_time / update_time 水位，仅处理新增与标签变更图片
--   2. 成本分级：高价值数据全量处理，普通数据按比例抽样，GPU 空闲时段分批——GPU 是稀缺资源，成本必须分级
--   3. 双路编码：图片与 caption 经同一 CLIP 模型双塔编码，保证向量同空间
--   4. 幂等写回：按 (image_id, embedding_version) Upsert，重跑无副作用；标签变图片不变不重算
--   5. 索引刷新：写入完成后通知 StarRocks 增量刷新当日分区索引，新数据当日可检索
-- 第 ② ③ 步（成本分级 / GPU 双路编码）在 Spark / Ray + GPU 算子里完成，不在 Flink SQL 内；
-- 本文件覆盖第 ① 步的增量水位筛选、第 ④ 步的幂等写回、第 ⑤ 步的索引刷新通知。

-- ① 增量识别：按 create_time / update_time 水位，仅取新增与标签变更图片
--    :create_wm / :update_wm 由调度注入（见 embedding.Watermark）
CREATE TEMPORARY VIEW `image_increment` AS
SELECT *
FROM `dwd_mining_image_frame_detail`
WHERE `create_time` > CAST(:create_wm AS TIMESTAMP(3))
   OR `update_time` > CAST(:update_wm AS TIMESTAMP(3));

-- ④ 幂等写回：向量由 GPU 算子算好后落在 `tmp_image_vector_staging`
-- 幂等写回：按 (image_id, embedding_version) Upsert，重跑无副作用（原文第三章第 ④ 步）
INSERT INTO `dwd_mining_image_vector_detail`
SELECT * FROM `tmp_image_vector_staging`
WHERE `embedding_version` = 'clip_v1' AND `dt` = '2026-09-06';

-- ④' 标签变图片不变：不重算向量，只更新 vector_meta 的对应路径
--     Python 侧走 pypaimon variant_set（a9 基准：比 JSON 解析-修改-回写快 22.23×~36.98×）
--     SQL 侧可读回热路径校验：
SELECT `image_id`, variant_get(vector_meta, '$.perception.weather', 'string') AS weather_raw
FROM `dwd_mining_image_vector_detail` WHERE `dt` = '2026-09-06' LIMIT 10;

-- ⑤ 索引刷新：写入完成后通知 StarRocks 增量刷新当日分区索引（在 StarRocks 侧执行）
--   REFRESH EXTERNAL TABLE paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail PARTITION ('2026-09-06');
--   ALTER TABLE paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail BUILD INDEX idx_image_embedding_hnsw PARTITION (`dt` = '2026-09-06');
--   ALTER TABLE paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail BUILD INDEX idx_text_embedding_hnsw PARTITION (`dt` = '2026-09-06');
