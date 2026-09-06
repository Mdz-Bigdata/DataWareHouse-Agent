-- V42: Register Email Tool into Database for UI selection
INSERT IGNORE INTO sys_api_tools (id, name, description, method, url_template, is_active, created_at, updated_at) VALUES
('tool-email-sender', 'send_email', '发送电子邮件 (SMTP)。需要在智能体工具配置中设置 SMTP 服务器信息。', 'POST', 'smtp://send', 1, NOW(), NOW());
