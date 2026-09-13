-- =============================================================================
-- embedding_version 灰度切换与一键回滚
-- 来源：a7 第二章设计决策三：模型换代新旧向量并存，切换与回滚都不需要重写数据
-- 本文件由 `python -m adas_lakehouse.vector.render_sql` 生成，请勿手改；
-- 参数改动请改 src/adas_lakehouse/vector/params.py。
-- =============================================================================

-- 顺序很重要：先激活新版本，再退役旧版本，否则会出现「一瞬间没有 active 版本」的检索空窗。

-- 1) 影子写入：新版本以 vector_status='deprecated' 先行入湖，不影响线上检索
--    （由 Embedding 流水线带 embedding_version='clip_v2' 跑一遍历史分区）

-- 2) 灰度切换
-- 激活 embedding_version=clip_v2：只改 vector_status 一列，向量本体不动
UPDATE `dwd_mining_image_vector_detail` SET `vector_status` = 'active'
WHERE `embedding_version` = 'clip_v2';
-- 退役 embedding_version=clip_v1：数据保留，检索不再命中
UPDATE `dwd_mining_image_vector_detail` SET `vector_status` = 'deprecated'
WHERE `embedding_version` = 'clip_v1';

-- 3) 一键回滚（新旧向量并存，无需重算、无需重建索引）
-- 一键回滚 clip_v2 -> clip_v1（新旧向量并存，无需重算、无需重建索引）
-- 退役 embedding_version=clip_v2：数据保留，检索不再命中
UPDATE `dwd_mining_image_vector_detail` SET `vector_status` = 'deprecated'
WHERE `embedding_version` = 'clip_v2';
-- 激活 embedding_version=clip_v1：只改 vector_status 一列，向量本体不动
UPDATE `dwd_mining_image_vector_detail` SET `vector_status` = 'active'
WHERE `embedding_version` = 'clip_v1';
