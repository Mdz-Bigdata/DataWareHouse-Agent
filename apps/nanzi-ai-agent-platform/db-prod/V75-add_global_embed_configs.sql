-- 需求：系统设置新增全局 Embedding 参数配置，分类为 agent
-- 创建人：Antigravity
-- 创建时间：2026-06-13

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('embed_api_url', 'https://ds-api.yovole.com/v1', '全局 Embedding 模型 API 地址，本地 HNSW 元数据和案例相似度匹配场景下将用于向量计算', 'agent', 0),
('embed_api_key', '', '全局 Embedding 模型 API Key', 'agent', 1),
('embed_model_name', 'bge-m3', '全局 Embedding 模型名称', 'agent', 0),
('embed_dimensions', '1024', '全局 Embedding 向量维度，必须与 Redis 创建索引设定的维度相匹配', 'agent', 0);
