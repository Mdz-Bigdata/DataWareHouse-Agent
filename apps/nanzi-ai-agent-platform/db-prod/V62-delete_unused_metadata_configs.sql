-- V62-delete_unused_metadata_configs.sql
-- 1. 清理废弃的系统配置：metadata_api_url 和 metadata_api_key
DELETE FROM `system_configs` WHERE `key` IN ('metadata_api_url', 'metadata_api_key');

-- 2. 修正相似度阈值和向量检索权重值
UPDATE `system_configs` SET `value` = '0.4' WHERE `key` IN ('ragflow_similarity_threshold', 'chatbi_sample_similarity_threshold');
UPDATE `system_configs` SET `value` = '0.85' WHERE `key` IN ('ragflow_vector_weight', 'chatbi_sample_vector_similarity_weight');

-- 3. 修正智能体思考最大迭代轮数
UPDATE `system_configs` SET `value` = '20' WHERE `key` = 'agent_max_iterations';

-- 4. 修正 RAGFlow 元数据检索返回 Top K 数量
UPDATE `system_configs` SET `value` = '8' WHERE `key` = 'ragflow_metadata_top_k';
