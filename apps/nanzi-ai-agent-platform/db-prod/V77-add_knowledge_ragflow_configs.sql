-- V77: 新增知识库问答检索专属 RAGFlow 配置
-- Date: 2026-06-13
-- Purpose:
-- 独立出一组专用于知识库问答检索的 RAGFlow 连接与检索参数配置（category = 'knowledge'）。

INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('knowledge_ragflow_api_url', '', 'RAGFlow 知识库服务 API 接口网关地址，知识库问答检索将始终使用本节参数', 'knowledge', 0),
('knowledge_ragflow_api_key', '', '用于调用 RAGFlow 知识库服务的 API Key', 'knowledge', 1),
('knowledge_ragflow_dataset_ids', '', '默认绑定的知识库 ID（可多选），用于常规智能体问答召回背景知识文档', 'knowledge', 0),
('knowledge_ragflow_metadata_top_k', '5', '常规知识库问答检索时，最大召回的候选文档片段数量。值越大参考条数越多，但会增加 Token 消耗。', 'knowledge', 0),
('knowledge_ragflow_similarity_threshold', '0.2', '常规知识库检索时的相似度阈值（0.0 至 1.0）。低于此设定值的检索结果将被过滤，以防混入无关文档，推荐配置为 0.20。', 'knowledge', 0),
('knowledge_ragflow_vector_weight', '0.3', '常规知识库检索时向量相似度权重的占比（0.0 至 1.0），其余权重为全文关键词匹配。推荐配置为 0.30。', 'knowledge', 0)
ON DUPLICATE KEY UPDATE
`description` = VALUES(`description`),
`category` = VALUES(`category`);

-- 初次安装迁移时，若旧值存在且新值为空，则自动从元数据配置项复制，确保无缝升级
UPDATE `system_configs` t_new, `system_configs` t_old
SET t_new.`value` = t_old.`value`
WHERE t_new.`key` = 'knowledge_ragflow_api_url'
  AND t_old.`key` = 'ragflow_api_url'
  AND (t_new.`value` IS NULL OR t_new.`value` = '');

UPDATE `system_configs` t_new, `system_configs` t_old
SET t_new.`value` = t_old.`value`
WHERE t_new.`key` = 'knowledge_ragflow_api_key'
  AND t_old.`key` = 'ragflow_api_key'
  AND (t_new.`value` IS NULL OR t_new.`value` = '');

UPDATE `system_configs` t_new, `system_configs` t_old
SET t_new.`value` = t_old.`value`
WHERE t_new.`key` = 'knowledge_ragflow_dataset_ids'
  AND t_old.`key` = 'ragflow_dataset_ids'
  AND (t_new.`value` IS NULL OR t_new.`value` = '');

-- 迁移完成后，彻底删除已废弃且极易混淆的旧版全局配置项 ragflow_dataset_ids
DELETE FROM `system_configs` WHERE `key` = 'ragflow_dataset_ids';
