-- 将 SQL 查数超时从 30s 调整为 60s（与代码默认值一致）
UPDATE `system_configs`
SET `value` = '60.0',
    `description` = '数据查数接口 SQL 执行超时时间 (秒)'
WHERE `key` = 'data_api_timeout_seconds'
  AND `value` IN ('30', '30.0');
