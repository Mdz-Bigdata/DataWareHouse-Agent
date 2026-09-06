-- 插入 Yovole SSO 的胶囊开关配置
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('yovole_sso_enabled', 'false', '控制是否启用 Yovole SSO 统一登录。关闭后，登录页面的 SSO 登录将隐藏，且用户管理中的 SSO 同步按钮也将隐藏。', 'other', 0);
