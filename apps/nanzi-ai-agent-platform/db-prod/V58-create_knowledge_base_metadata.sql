-- V58: Create Knowledge Base Metadata
-- Description: Local metadata for RAGFlow knowledge bases and permissions.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS knowledge_base_metadata (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    ragflow_dataset_id VARCHAR(64) NOT NULL COMMENT 'RAGFlow Dataset ID',
    name VARCHAR(255) NOT NULL COMMENT '知识库名称快照',
    description TEXT NULL COMMENT '知识库描述快照',
    owner VARCHAR(100) NULL COMMENT '业务归属/负责人',
    visibility VARCHAR(32) DEFAULT 'private' COMMENT '可见性: private/team/public',
    tags JSON NULL COMMENT '平台侧标签',
    notes TEXT NULL COMMENT '平台侧备注',
    extra_config JSON NULL COMMENT '扩展配置',
    status VARCHAR(32) DEFAULT 'active' COMMENT '状态: active/deleted/missing',
    created_by VARCHAR(64) NULL COMMENT '创建人',
    updated_by VARCHAR(64) NULL COMMENT '更新人',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uix_kb_ragflow_dataset_id (ragflow_dataset_id),
    KEY idx_kb_status (status),
    KEY idx_kb_owner (owner),
    KEY idx_kb_created_by (created_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('menu', 'menu:knowledge_management', 1, NOW(), NOW()),
('menu', 'menu:knowledge_retrieval_test', 1, NOW(), NOW()),
('element', 'element:knowledge:create', 1, NOW(), NOW()),
('element', 'element:knowledge:edit', 1, NOW(), NOW()),
('element', 'element:knowledge:delete', 1, NOW(), NOW()),
('element', 'element:knowledge:upload_document', 1, NOW(), NOW()),
('element', 'element:knowledge:delete_document', 1, NOW(), NOW()),
('element', 'element:knowledge:parse_document', 1, NOW(), NOW()),
('element', 'element:knowledge:test_retrieval', 1, NOW(), NOW());
