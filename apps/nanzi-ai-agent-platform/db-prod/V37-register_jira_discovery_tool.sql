-- V37: 注册 Jira 项目探索工具并关联至 DevOps 智能体
-- Date: 2026-01-28
-- Purpose: 
-- 1. 注册 jira_get_projects 工具，支持智能体在搜索前发现 Jira 项目。
-- 2. 将该工具自动关联至 devops-expert 智能体的最新发布版本。

-- ==========================================
-- 1. 注册 Jira 探索工具 (Jira Discovery Tool)
-- ==========================================

INSERT INTO `sys_api_tools` (
    `id`, `name`, `description`, `url_template`, `method`, `is_active`, `created_at`
) VALUES 
(
    UUID(),
    'jira_get_projects', 
    '获取 Jira 项目列表 (Get Projects): 查询当前 Jira 系统中可用的项目 Key 和名称。', 
    'internal://jira_get_projects',
    'GET',
    1, 
    NOW()
)
ON DUPLICATE KEY UPDATE 
    `description` = VALUES(`description`),
    `is_active` = 1;


