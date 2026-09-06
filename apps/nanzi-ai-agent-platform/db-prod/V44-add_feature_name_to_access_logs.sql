-- V44: 增加功能点字段到访问日志表
ALTER TABLE ai_agent_access_logs ADD COLUMN feature_name VARCHAR(100) DEFAULT NULL COMMENT '功能点/菜单名称' AFTER user_name;

-- 索引优化以便查询
CREATE INDEX idx_access_feature ON ai_agent_access_logs(feature_name);
