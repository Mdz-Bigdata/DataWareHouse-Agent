-- V90: Create user notification configurations table for personal notification settings
CREATE TABLE IF NOT EXISTS user_notification_configs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    channel_type VARCHAR(50) NOT NULL COMMENT '通道类型: dingtalk, wechat_work, email',
    config_json TEXT NOT NULL COMMENT '配置参数，JSON格式保存',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    CONSTRAINT fk_user_notification_config_user
        FOREIGN KEY (user_id) REFERENCES ai_agent_users(id)
        ON DELETE CASCADE,
    UNIQUE KEY ux_user_notification_channel (user_id, channel_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户消息通知配置表';
