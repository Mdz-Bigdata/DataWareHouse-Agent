-- ----------------------------
-- V71: 对审计日志与执行 Trace 表进行 MySQL Range 分区改造
-- ----------------------------

-- 1. 改造 ai_agent_access_logs 表
-- 1.1 先将 created_at 变更为 DATETIME 类型，以支持 Range Columns 分区
ALTER TABLE ai_agent_access_logs MODIFY COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP;

-- 1.2 修改自增列去掉 AUTO_INCREMENT 属性，以便安全删除旧主键
ALTER TABLE ai_agent_access_logs MODIFY COLUMN id BIGINT NOT NULL;
ALTER TABLE ai_agent_access_logs DROP PRIMARY KEY;

-- 1.3 重新添加联合主键并加回 AUTO_INCREMENT
ALTER TABLE ai_agent_access_logs ADD PRIMARY KEY (id, created_at);
ALTER TABLE ai_agent_access_logs MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT;

-- 1.4 挂载 Range 按月分区
ALTER TABLE ai_agent_access_logs PARTITION BY RANGE COLUMNS(created_at) (
    PARTITION p202605 VALUES LESS THAN ('2026-06-01 00:00:00'),
    PARTITION p202606 VALUES LESS THAN ('2026-07-01 00:00:00'),
    PARTITION p202607 VALUES LESS THAN ('2026-08-01 00:00:00'),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);


-- 2. 改造 ai_agent_execution_traces 表
-- 2.1 先将 created_at 变更为 DATETIME 类型
ALTER TABLE ai_agent_execution_traces MODIFY COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP;

-- 2.2 修改自增列去掉 AUTO_INCREMENT 属性
ALTER TABLE ai_agent_execution_traces MODIFY COLUMN id BIGINT NOT NULL;
ALTER TABLE ai_agent_execution_traces DROP PRIMARY KEY;

-- 2.3 重新添加联合主键并加回 AUTO_INCREMENT
ALTER TABLE ai_agent_execution_traces ADD PRIMARY KEY (id, created_at);
ALTER TABLE ai_agent_execution_traces MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT;

-- 2.4 挂载 Range 按月分区
ALTER TABLE ai_agent_execution_traces PARTITION BY RANGE COLUMNS(created_at) (
    PARTITION p202605 VALUES LESS THAN ('2026-06-01 00:00:00'),
    PARTITION p202606 VALUES LESS THAN ('2026-07-01 00:00:00'),
    PARTITION p202607 VALUES LESS THAN ('2026-08-01 00:00:00'),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);

-- 3. 注册系统配置参数：日志保留天数
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('audit_log_retention_days', '90', '系统日志和智能体步骤 Trace 保留期限天数', 'other', 0);
