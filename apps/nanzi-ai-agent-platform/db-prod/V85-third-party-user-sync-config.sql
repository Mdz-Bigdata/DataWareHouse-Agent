-- 第三方用户同步配置项
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
  'third_party_user_sync_config',
  '{"enabled":false,"connection_config_id":null,"table_name":null,"field_map":{"id":"","user_name":"","real_name":null,"remark":null},"schedule":"off"}',
  '第三方用户同步配置（数据源、表、字段映射、定时周期）',
  'other',
  0
);
