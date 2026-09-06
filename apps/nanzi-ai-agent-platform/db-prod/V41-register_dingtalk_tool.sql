-- V41: Register DingTalk Tool into Database for UI selection
INSERT IGNORE INTO sys_api_tools (id, name, description, method, url_template, is_active, created_at, updated_at) VALUES
('tool-dingtalk-msg', 'send_dingtalk_message', '发送钉钉机器人消息。需要在智能体工具配置中设置 webhook_url 和 secret。', 'POST', 'https://oapi.dingtalk.com/robot/send', 1, NOW(), NOW());
