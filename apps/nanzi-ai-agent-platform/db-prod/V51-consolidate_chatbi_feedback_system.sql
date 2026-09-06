-- V51-consolidate_chatbi_feedback_system.sql

-- 1. 注册系统配置：ChatBI 经验库检索参数
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`)
VALUES 
(
    'chatbi_sample_similarity_threshold', 
    '0.4', 
    'ChatBI 经验库检索相似度阈值 (0-1)，建议 0.4。只有高于此分值的案例才会被注入 Prompt。', 
    'agent', 
    0
),
(
    'chatbi_sample_vector_similarity_weight', 
    '0.85', 
    'ChatBI 经验库检索向量权重 (0-1)，建议 0.85。剩余权重归于全文检索。', 
    'agent', 
    0
);

-- 2. 扩展 ai_chatbi_examples 表字段：支持意图增强与背景记录
ALTER TABLE `ai_chatbi_examples` 
ADD COLUMN `refined_query` TEXT NULL COMMENT 'LLM 改写后的完整独立问题（用于 RAG 检索）' AFTER `user_query`,
ADD COLUMN `context_summary` TEXT NULL COMMENT '对话上下文背景摘要' AFTER `refined_query`,
ADD COLUMN `sql_metadata` JSON NULL COMMENT 'SQL 技术特征（涉及表、查询类型等）' AFTER `sql_text`,
ADD COLUMN `enhance_status` VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT '增强状态: pending=正在处理, success=成功, failed=失败' AFTER `sql_metadata`;
