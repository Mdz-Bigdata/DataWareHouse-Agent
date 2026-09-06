-- V28: 注册 Jira 集成工具与 DevOps 运维智能体
-- Date: 2026-01-16
-- Purpose: 
-- 1. 在 sys_api_tools 中注册 jira_search 和 jira_create_issue 工具，使其在前端可见。
-- 2. 创建预置的 'devops-expert' 智能体，并绑定相关工具权限。

-- ==========================================
-- 1. 注册 Jira 工具 (Tools Registration)
-- ==========================================

INSERT INTO `sys_api_tools` (
    `id`, `name`, `description`, `url_template`, `method`, `is_active`, `created_at`
) VALUES 
(
    UUID(),
    'jira_search', 
    'Jira 搜索 (Jira Search): 使用 JQL 语法查询 Jira 历史工单。', 
    'internal://jira_search', -- 占位符，标识为内部工具
    'POST',
    1, 
    NOW()
),
(
    UUID(),
    'jira_create_issue', 
    '创建 Jira 工单 (Create Issue): 在 Jira 中创建新的任务或工单。', 
    'internal://jira_create_issue',
    'POST',
    1, 
    NOW()
)
ON DUPLICATE KEY UPDATE 
    `description` = VALUES(`description`),
    `is_active` = 1;

-- ==========================================
-- 2. 创建 DevOps 智能体 (Agent Creation)
-- ==========================================
-- 运维专家智能体基础数据
INSERT INTO `ai_agents` (
    `id`, 
    `name`, 
    `display_name`, 
    `description`, 
    `capabilities`, 
    `is_system`, 
    `is_enabled`,
    `engine_type`,
    `created_at`,
    `updated_at`
) VALUES (
    'devops-expert-001', 
    'devops-expert', 
    '运维专家 (DevOps Expert)', 
    '专门处理运维工单与故障排查的智能体，支持查询 Jira 历史经验 (CS/OPS) 并自动创建运维操作工单。', 
    '["运维经验检索", "工单自动化创建", "故障排查"]', 
    0, 
    1,
    'LOCAL',
    NOW(), 
    NOW()
) ON DUPLICATE KEY UPDATE 
    `display_name` = VALUES(`display_name`),
    `description` = VALUES(`description`),
    `updated_at` = NOW();
