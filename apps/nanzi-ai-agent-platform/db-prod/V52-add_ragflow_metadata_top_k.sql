INSERT INTO system_configs (`key`, `value`, `description`, `category`, `is_secret`)
VALUES
('ragflow_metadata_top_k', '8', 'RAGFlow 元数据检索时返回的 Top K 数量，控制 AI 可感知的表结构上限', 'metadata', 0)
ON DUPLICATE KEY UPDATE
`description` = VALUES(`description`),
`category` = VALUES(`category`);
