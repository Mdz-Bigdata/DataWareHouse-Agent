-- V57: Register Skills Management Permissions
-- Description: Registers the Skills Management menu permission.

INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('menu', 'menu:skills_management', 1, NOW(), NOW());
