CREATE TABLE IF NOT EXISTS `system_configs` (
    `key` VARCHAR(255) NOT NULL COMMENT '配置键名 (e.g., llm_model_name)',
    `value` TEXT NULL COMMENT '配置值',
    `description` VARCHAR(255) NULL COMMENT '配置描述',
    `category` VARCHAR(50) NOT NULL DEFAULT 'general' COMMENT '分类 (e.g., llm, data_api, general)',
    `is_secret` BOOLEAN NOT NULL DEFAULT 0 COMMENT '是否为敏感信息 (前端需脱敏)',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`key`),
    INDEX `idx_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统动态配置表';

-- 插入初始数据
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('llm_model_name', 'DeepSeek-V3.2', '系统默认模型', 'llm', 0),
('llm_temperature', '0.0', 'LLM 温度参数', 'llm', 0),
('external_sql_api_url', 'https://yunshu-api.yovole.net/api/v1/sql/execute', '外部 SQL 执行 API 地址', 'data_api', 0),
('external_sql_api_key', 'n0Tj4sgQNIxbUFSACm9ADKnNASb3LbohaQmkkfQAysQ', '外部 SQL API 鉴权 Key', 'data_api', 1),
('agent_max_iterations', '20', '智能体最大思考/调用工具轮数 (防止死循环)', 'agent', 0),
('data_api_timeout_seconds', '60.0', '数据查数接口 SQL 执行超时时间 (秒)', 'data_api', 0),
('schema_api_timeout_seconds', '10.0', '元数据获取接口超时时间 (秒)', 'data_api', 0),
('metadata_provider', 'local', '元数据提供者类型 (local: 本地元数据管理, ragflow: RAGFlow 语义检索)', 'metadata', 0),
('ragflow_api_url', 'http://your-ragflow-url:8001', 'RAGFlow 服务地址', 'metadata', 0),
('ragflow_api_key', '', 'RAGFlow API Key', 'metadata', 1);
