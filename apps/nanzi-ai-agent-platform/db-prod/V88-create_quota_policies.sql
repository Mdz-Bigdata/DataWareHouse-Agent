-- 用户/角色/系统级 Token 额度策略（按月计量）
CREATE TABLE IF NOT EXISTS `ai_agent_quota_policies` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `scope_type` VARCHAR(20) NOT NULL COMMENT 'user|role|system',
  `scope_id` BIGINT NULL COMMENT 'user_id 或 role_id；system 时为 NULL',
  `period` VARCHAR(20) NOT NULL DEFAULT 'monthly' COMMENT '计费周期：monthly',
  `limit_tokens` BIGINT NULL COMMENT 'NULL 表示不限额',
  `enabled` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用该策略',
  `action_on_exceed` VARCHAR(20) NOT NULL DEFAULT 'block' COMMENT '超额动作：block',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_quota_scope_period` (`scope_type`, `scope_id`, `period`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Token 额度策略';
