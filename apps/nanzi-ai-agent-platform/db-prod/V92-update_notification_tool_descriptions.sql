-- V92: Clarify notification tools use the current user's Personal Center config
-- Description: Prevents agents/operators from thinking webhook credentials must be supplied in-chat or per tool config.

UPDATE sys_api_tools
SET description = '发送钉钉群机器人 Markdown 消息。自动读取当前用户在个人中心 -> 消息通知里的钉钉 Webhook/加签配置，无需在本轮对话或工具配置中提供 webhook、access_token 或群聊目标。'
WHERE name = 'send_dingtalk_message';

UPDATE sys_api_tools
SET description = '发送企业微信群机器人 Markdown 消息。自动读取当前用户在个人中心 -> 消息通知里的企微 Webhook 配置，无需在本轮对话或工具配置中提供 webhook 或群聊目标。'
WHERE name = 'send_wechat_work_message';

UPDATE sys_api_tools
SET description = '发送邮件通知。自动读取当前用户在个人中心 -> 消息通知里的 SMTP 配置，无需在本轮对话或工具配置中提供 SMTP 服务器或密码。'
WHERE name = 'send_email';
