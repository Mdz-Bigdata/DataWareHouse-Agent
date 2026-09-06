CREATE TABLE IF NOT EXISTS `chatbi_briefs` (
  `id` VARCHAR(64) NOT NULL COMMENT '业务简报唯一标识',
  `owner_user_id` BIGINT NOT NULL COMMENT '简报所属用户ID',
  `conversation_id` VARCHAR(128) NOT NULL COMMENT '来源ChatBI会话ID',
  `result_id` VARCHAR(64) NULL COMMENT '来源ChatBI结构化结果ID',
  `title` VARCHAR(200) NOT NULL COMMENT '业务简报标题',
  `brief_payload` JSON NOT NULL COMMENT '简报结构化内容及证据信息',
  `markdown_content` TEXT NOT NULL COMMENT '简报Markdown正文',
  `artifact_payload` JSON NULL COMMENT 'Word等生成文件的下载信息',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '简报创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_chatbi_briefs_owner_created` (`owner_user_id`, `created_at`),
  KEY `idx_chatbi_briefs_conversation` (`conversation_id`),
  KEY `idx_chatbi_briefs_result` (`result_id`),
  CONSTRAINT `fk_chatbi_briefs_owner` FOREIGN KEY (`owner_user_id`) REFERENCES `ai_agent_users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='ChatBI证据化业务简报';
