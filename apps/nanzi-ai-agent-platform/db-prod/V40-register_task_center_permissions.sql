-- V40: Register Task Center Permissions
-- Description: Registers the Task Center menu and management element permissions.

-- 1. Register Menu
INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('menu', 'menu:task_center', 1, NOW(), NOW());

-- 2. Register Elements (Operations)
INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('element', 'element:task:manage', 1, NOW(), NOW());
