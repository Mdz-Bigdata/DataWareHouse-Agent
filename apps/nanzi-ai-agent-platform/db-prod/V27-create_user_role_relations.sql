-- V27 Create User-Role Relation Table
-- Date: 2026-01-13
-- Purpose: Link Users to Business Roles (Many-to-Many).

SET NAMES utf8mb4;

-- ----------------------------
-- 1. Table: ai_agent_user_role_relations
-- ----------------------------
CREATE TABLE IF NOT EXISTS ai_agent_user_role_relations (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL COMMENT 'Link to ai_agent_users.id',
    role_id BIGINT NOT NULL COMMENT 'Link to ai_agent_roles.id',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE KEY uk_user_role (user_id, role_id),
    INDEX idx_user (user_id),
    INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
