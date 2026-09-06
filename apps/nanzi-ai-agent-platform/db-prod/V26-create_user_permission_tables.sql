-- V26 Create User Permission Tables
-- Date: 2026-01-13
-- Purpose: Implement resource-level permissions (Agent, Dataset, API) and reserve support for RBAC.

SET NAMES utf8mb4;

-- ----------------------------
-- 1. Table: ai_agent_roles (Reserved for future RBAC)
-- ----------------------------
CREATE TABLE IF NOT EXISTS ai_agent_roles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(50) NOT NULL UNIQUE COMMENT 'Role code (e.g., admin, editor)',
    name VARCHAR(50) NOT NULL COMMENT 'Display name',
    description VARCHAR(255) COMMENT 'Role description',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------
-- 2. Table: ai_agent_resource_permissions
-- ----------------------------
CREATE TABLE IF NOT EXISTS ai_agent_resource_permissions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT DEFAULT NULL COMMENT 'Link to ai_agent_users.id',
    role_id BIGINT DEFAULT NULL COMMENT 'Link to ai_agent_roles.id (Reserved)',
    resource_type VARCHAR(20) NOT NULL COMMENT 'Enum: agent, dataset, api',
    resource_id VARCHAR(100) NOT NULL COMMENT 'External ID (UUID) or String ID of the resource',
    enabled TINYINT(1) DEFAULT 1 COMMENT '1=Enabled, 0=Disabled',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    INDEX idx_user_res (user_id, resource_type),
    INDEX idx_role_res (role_id, resource_type),
    INDEX idx_resource (resource_type, resource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
