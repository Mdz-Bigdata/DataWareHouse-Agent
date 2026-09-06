-- V91: Register WeChat Work Tool into Database for UI selection
INSERT IGNORE INTO sys_api_tools (id, name, description, method, url_template, is_active, created_at, updated_at) VALUES
('tool-wechat-work-msg', 'send_wechat_work_message', '发送企业微信机器人消息。需要在个人中心消息通知里配置 Webhook 地址。', 'POST', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send', 1, NOW(), NOW());
