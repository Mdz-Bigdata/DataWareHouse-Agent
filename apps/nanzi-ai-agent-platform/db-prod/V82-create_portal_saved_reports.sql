-- V82: Persist ChatBI golden reports and share targets
-- Created at: 2026-06-27

CREATE TABLE IF NOT EXISTS `portal_saved_reports` (
  `id` varchar(64) NOT NULL,
  `title` varchar(100) NOT NULL,
  `description` varchar(500) DEFAULT NULL,
  `sql_content` text NOT NULL,
  `dataset_id` bigint DEFAULT NULL,
  `data_source` varchar(100) NOT NULL,
  `original_query` text DEFAULT NULL,
  `mode` varchar(32) NOT NULL DEFAULT 'static_sql',
  `sql_template` text DEFAULT NULL,
  `params_schema` json DEFAULT NULL,
  `default_params` json DEFAULT NULL,
  `analysis_mode` varchar(32) NOT NULL DEFAULT 'manual',
  `tags` json DEFAULT NULL,
  `owner_user_id` bigint NOT NULL,
  `owner_name` varchar(100) DEFAULT NULL,
  `visibility` varchar(32) NOT NULL DEFAULT 'private',
  `status` varchar(32) NOT NULL DEFAULT 'active',
  `last_run_at` datetime DEFAULT NULL,
  `last_success_at` datetime DEFAULT NULL,
  `last_error` text DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_portal_saved_reports_owner_updated` (`owner_user_id`, `updated_at`),
  KEY `idx_portal_saved_reports_visibility` (`visibility`),
  CONSTRAINT `fk_portal_saved_reports_owner`
    FOREIGN KEY (`owner_user_id`) REFERENCES `ai_agent_users` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `portal_saved_report_shares` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `report_id` varchar(64) NOT NULL,
  `target_type` varchar(16) NOT NULL,
  `target_id` bigint NOT NULL,
  `permission` varchar(16) NOT NULL DEFAULT 'run',
  `created_by` bigint NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_portal_saved_report_share_target` (`report_id`, `target_type`, `target_id`),
  KEY `idx_portal_saved_report_shares_target` (`target_type`, `target_id`),
  KEY `idx_portal_saved_report_shares_report` (`report_id`),
  KEY `idx_portal_saved_report_shares_created_by` (`created_by`),
  CONSTRAINT `fk_portal_saved_report_shares_report`
    FOREIGN KEY (`report_id`) REFERENCES `portal_saved_reports` (`id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_portal_saved_report_shares_created_by`
    FOREIGN KEY (`created_by`) REFERENCES `ai_agent_users` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
