-- V87: 知识库功能总开关
-- Date: 2026-07-02
-- Purpose: 允许管理员关闭知识库能力；关闭后管理页、检索测试与 search_knowledge_base 工具均不可用。

INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('knowledge_base_enabled', 'true', '是否启用知识库功能。关闭后，知识库管理、检索测试及智能体知识库检索工具将不可用，下方连接参数仅保留只读。', 'knowledge', 0)
ON DUPLICATE KEY UPDATE
`description` = VALUES(`description`),
`category` = VALUES(`category`);
