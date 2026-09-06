-- V50-init_chatbi_feedback_system.sql

-- 1. 创建经验库主表
CREATE TABLE IF NOT EXISTS `ai_chatbi_examples` (
    `id`             BIGINT       AUTO_INCREMENT PRIMARY KEY,
    `trace_id`       VARCHAR(64)  NOT NULL COMMENT '关联 ai_agent_execution_history.trace_id',
    `agent_id`       VARCHAR(36)  NOT NULL COMMENT '智能体 ID',
    `dataset_id`     INT          NOT NULL COMMENT '数据集 ID（metadata_datasets.id）',
    
    `user_query`     TEXT         NOT NULL COMMENT '用户自然语言问题',
    `sql_text`       TEXT         NOT NULL COMMENT '执行成功的 SQL',
    `ai_answer`      TEXT         NULL      COMMENT 'AI 回答摘要',
    
    `feedback_type`  VARCHAR(10)  NOT NULL DEFAULT 'up' COMMENT '反馈: up=点赞, down=点踩',
    `status`         VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT '状态: pending=待审核, approved=已通过, rejected=已驳回, deprecated=已废弃',
    `error_message`  TEXT         NULL      COMMENT '如果是负向样本，记录报错信息',
    `use_count`      INT          NOT NULL DEFAULT 0  COMMENT '引用次数',
    
    `rag_doc_id`     VARCHAR(64)  NULL      COMMENT 'RAGFlow 中的文档 ID',
    `rag_sync_status` VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT '同步状态: pending, synced, failed, removed',
    `rag_sync_error` TEXT         NULL      COMMENT '同步失败错误信息',
    `rag_synced_at`  DATETIME     NULL      COMMENT '最后同步时间',
    
    `user_id`        VARCHAR(64)  NULL      COMMENT '反馈用户 ID',
    `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY `idx_trace` (`trace_id`),
    INDEX `idx_agent_dataset` (`agent_id`, `dataset_id`),
    INDEX `idx_status` (`status`),
    INDEX `idx_rag_sync` (`rag_sync_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ChatBI 经验案例库';

-- 2. 创建引用记录流水表
CREATE TABLE IF NOT EXISTS `ai_chatbi_example_usages` (
    `id`            BIGINT      AUTO_INCREMENT PRIMARY KEY,
    `example_id`    BIGINT      NOT NULL COMMENT '关联 ai_chatbi_examples.id',
    `trace_id`      VARCHAR(64) NOT NULL COMMENT '当前对话的 trace_id',
    `similarity`    FLOAT       NULL     COMMENT '检索时的相似度',
    `created_at`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_example` (`example_id`),
    INDEX `idx_trace` (`trace_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ChatBI 经验引用流水表';

-- 3. 修改执行历史表，增加反馈字段
ALTER TABLE `ai_agent_execution_history` 
ADD COLUMN `feedback` VARCHAR(10) NULL DEFAULT NULL COMMENT '用户反馈: up/down' AFTER `agent_version`;

-- 4. 注册系统配置：经验库专属 RAGFlow 数据集 ID
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`)
VALUES (
    'chatbi_sample_knowledge_base', 
    '', 
    '用于存放用户点赞沉淀的优质 SQL 案例的 RAGFlow 数据集 ID', 
    'agent', 
    0
);

-- 5. 注册权限资源 (遵循 ai_agent_resource_permissions 规范)
INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('menu', 'menu:chatbi_examples', 1, NOW(), NOW()),
('element', 'element:chatbi_example:audit', 1, NOW(), NOW()),
('element', 'element:chatbi_example:sync', 1, NOW(), NOW()),
('element', 'element:chatbi_example:delete', 1, NOW(), NOW());
