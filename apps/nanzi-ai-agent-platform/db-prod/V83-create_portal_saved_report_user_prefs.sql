CREATE TABLE IF NOT EXISTS portal_saved_report_user_prefs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    report_id VARCHAR(64) NOT NULL COMMENT '已保存报表的ID',
    user_id BIGINT NOT NULL COMMENT '用户ID',
    is_favorite TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否收藏（0-否，1-是）',
    pinned_at DATETIME NULL COMMENT '置顶时间',
    last_viewed_at DATETIME NULL COMMENT '最近浏览时间',
    run_count INT NOT NULL DEFAULT 0 COMMENT '运行总次数',
    last_run_at DATETIME NULL COMMENT '最近一次运行时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    CONSTRAINT fk_saved_report_user_pref_report
        FOREIGN KEY (report_id) REFERENCES portal_saved_reports(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_saved_report_user_pref_user
        FOREIGN KEY (user_id) REFERENCES ai_agent_users(id)
        ON DELETE CASCADE,
    UNIQUE KEY ux_portal_saved_report_user_pref (report_id, user_id),
    KEY idx_portal_saved_report_user_pref_user (user_id, pinned_at, last_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据门户已保存报表的用户偏好设置表（收藏、置顶、阅读及运行历史）';
