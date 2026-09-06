-- V30: Register initial UI resources (Menus and Elements)
-- Description: Adds system menus and functional elements to the permission system for granular control.

-- 1. Register Menus
INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
('menu', 'menu:dashboard', 1, NOW(), NOW()),
('menu', 'menu:ai_chat', 1, NOW(), NOW()),
('menu', 'menu:metadata', 1, NOW(), NOW()),
('menu', 'menu:agent_management', 1, NOW(), NOW()),
('menu', 'menu:agent_debug', 1, NOW(), NOW()),
('menu', 'menu:playground', 1, NOW(), NOW()),
('menu', 'menu:chat_logs', 1, NOW(), NOW()),
('menu', 'menu:system:users', 1, NOW(), NOW()),
('menu', 'menu:system:roles', 1, NOW(), NOW()),
('menu', 'menu:system:config', 1, NOW(), NOW()),
('menu', 'menu:system:audit', 1, NOW(), NOW()),
('menu', 'menu:prompts', 1, NOW(), NOW()),
('menu', 'menu:widget_debug', 1, NOW(), NOW());

-- 2. Register key elements (buttons)
INSERT IGNORE INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at) VALUES
-- 元数据管理相关
('element', 'element:metadata:sync', 1, NOW(), NOW()),
('element', 'element:metadata:import', 1, NOW(), NOW()),
('element', 'element:metadata:edit', 1, NOW(), NOW()),
('element', 'element:metadata:delete', 1, NOW(), NOW()),
('element', 'element:metadata:view_yaml', 1, NOW(), NOW()),
('element', 'element:metadata:edit_table', 1, NOW(), NOW()),
('element', 'element:metadata:delete_table', 1, NOW(), NOW()),
-- 智能体管理相关
('element', 'element:agent:create', 1, NOW(), NOW()),
('element', 'element:agent:edit', 1, NOW(), NOW()),
('element', 'element:agent:delete', 1, NOW(), NOW()),
-- 聊天日志相关
('element', 'element:chat_logs:export', 1, NOW(), NOW()),
-- 提示词工作室相关
('element', 'element:prompts:optimize', 1, NOW(), NOW()),
-- 用户与角色管理高级操作
('element', 'element:user:view_key', 1, NOW(), NOW()),
('element', 'element:user:reset_key', 1, NOW(), NOW()),
('element', 'element:user:edit', 1, NOW(), NOW()),
('element', 'element:user:delete', 1, NOW(), NOW()),
('element', 'element:role:edit', 1, NOW(), NOW()),
('element', 'element:role:delete', 1, NOW(), NOW()),
('element', 'element:system:config_save', 1, NOW(), NOW());

-- 3. Pre-assign all UI permissions to the 'admin' role if needed
-- Note: The code already has 'Admin bypass' logic, so this is primarily for visibility in the UI.
-- (Optional step based on preference)
