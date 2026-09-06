ALTER TABLE `portal_saved_report_subscriptions`
  ADD COLUMN `alert_condition` JSON NULL COMMENT '版本化告警条件' AFTER `external_channels`,
  ADD COLUMN `alert_state` JSON NULL COMMENT '最近基线与连续命中状态' AFTER `alert_condition`;

ALTER TABLE `portal_saved_report_digest_deliveries`
  ADD COLUMN `trigger_evidence` JSON NULL COMMENT '本次告警判断与触发证据' AFTER `digest_payload`;
