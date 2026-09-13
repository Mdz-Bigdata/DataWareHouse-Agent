-- ============================================================================
-- 控制面本地库：规则配置 / 任务配置与执行状态 / 审核流状态 / 网关审计。
--
-- 库名取自 controlplane/store.py 的 CONTROL_PLANE_MYSQL_DATABASE（默认
-- adas_mining_platform）；下面的建表语句**原样**来自同一文件里的
-- CONTROL_PLANE_MYSQL_DDL 常量，本文件不是第二份定义，只是它的落盘副本。
--
-- 重新生成（改了 store.py 之后必须重跑，否则两边会漂）：
--   PYTHONPATH=src python3 -c "from adas_lakehouse.controlplane.store import \
--     CONTROL_PLANE_MYSQL_DDL as D; print(D)" > /tmp/cp.sql
--   然后把 /tmp/cp.sql 的内容替换掉本文件 BEGIN/END 标记之间的部分。
--
-- 控制面状态「可随时重建」（原文第四章健康判据）——这个库清空不影响任何业务数据。
-- ============================================================================

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS `adas_mining_platform`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `adas_mining_platform`;

-- >>> BEGIN generated from controlplane.store.CONTROL_PLANE_MYSQL_DDL >>>
-- 控制面本地库：清空重建不影响任何业务数据（原文第四章健康判据）
CREATE TABLE IF NOT EXISTS `cp_rule_config` (
  `rule_id`        VARCHAR(64)  NOT NULL COMMENT '规则 ID',
  `rule_version`   INT          NOT NULL COMMENT '规则版本，每次下发 +1',
  `rule_name`      VARCHAR(200) NOT NULL COMMENT '规则名称',
  `scene_expression` TEXT       NOT NULL COMMENT '场景表达式（SQL 谓词）',
  `tag_code`       VARCHAR(128)          COMMENT '命中后打的标签编码',
  `engine`         VARCHAR(32)  NOT NULL COMMENT 'spark_batch / flink_stream',
  `enabled`        TINYINT(1)   NOT NULL DEFAULT 1,
  `priority`       INT          NOT NULL DEFAULT 5,
  `owner`          VARCHAR(64),
  `created_at`     DATETIME(3)  NOT NULL,
  `updated_at`     DATETIME(3)  NOT NULL,
  PRIMARY KEY (`rule_id`, `rule_version`),
  KEY `idx_rule_updated` (`updated_at`)
) ENGINE=InnoDB COMMENT='规则配置；经 Flink CDC 同步入湖 ods_mining_rule_config';

CREATE TABLE IF NOT EXISTS `cp_task` (
  `task_id`        VARCHAR(96)  NOT NULL,
  `run_id`         VARCHAR(64)  NOT NULL COMMENT '三级 ID：处理运行级',
  `task_kind`      VARCHAR(32)  NOT NULL,
  `subsystem`      VARCHAR(32)  NOT NULL,
  `task_state`     VARCHAR(24)  NOT NULL,
  `rule_id`        VARCHAR(64),
  `rule_version`   INT,
  `priority`       INT          NOT NULL DEFAULT 5,
  `requested_by`   VARCHAR(64)  NOT NULL,
  `idempotency_key` VARCHAR(128)         COMMENT '幂等键防重复提交（原文第六章）',
  `external_handle` VARCHAR(200)         COMMENT '数据面作业句柄（Spark/Flink/K8s/Ray）',
  `attempt`        INT          NOT NULL DEFAULT 0,
  `envelope_json`  TEXT         NOT NULL COMMENT 'TaskEnvelope 快照',
  `artifacts_json` TEXT                  COMMENT '产物指针列表（只有 ID，没有数据本体）',
  `rows_written`   BIGINT       NOT NULL DEFAULT 0,
  `review_decision` VARCHAR(16),
  `reviewer`       VARCHAR(64),
  `message`        VARCHAR(500),
  `written_back`   TINYINT(1)   NOT NULL DEFAULT 0,
  `created_at`     DATETIME(3)  NOT NULL,
  `updated_at`     DATETIME(3)  NOT NULL,
  PRIMARY KEY (`task_id`),
  UNIQUE KEY `uk_idempotency` (`idempotency_key`),
  KEY `idx_state_priority` (`task_state`, `priority`, `created_at`),
  KEY `idx_writeback` (`written_back`, `updated_at`)
) ENGINE=InnoDB COMMENT='任务配置与执行状态 + 审核流状态';

CREATE TABLE IF NOT EXISTS `cp_task_event` (
  `task_id`    VARCHAR(96) NOT NULL,
  `event_seq`  INT         NOT NULL,
  `from_state` VARCHAR(24),
  `to_state`   VARCHAR(24) NOT NULL,
  `event_time` DATETIME(3) NOT NULL,
  `actor`      VARCHAR(64) NOT NULL,
  `detail`     VARCHAR(500),
  PRIMARY KEY (`task_id`, `event_seq`)
) ENGINE=InnoDB COMMENT='状态迁移审计流；定期回写 dwd_mining_task_detail 进血缘';

CREATE TABLE IF NOT EXISTS `cp_audit_log` (
  `audit_id`   BIGINT       NOT NULL AUTO_INCREMENT,
  `api_path`   VARCHAR(200) NOT NULL,
  `http_method` VARCHAR(8)  NOT NULL,
  `caller`     VARCHAR(64)  NOT NULL,
  `at`         DATETIME(3)  NOT NULL,
  `outcome`    VARCHAR(24)  NOT NULL,
  `detail`     VARCHAR(500),
  PRIMARY KEY (`audit_id`),
  KEY `idx_audit_at` (`at`)
) ENGINE=InnoDB COMMENT='OpenAPI 网关审计（原文第三章接入层：认证/限流/审计）';
-- <<< END generated from controlplane.store.CONTROL_PLANE_MYSQL_DDL <<<

-- 控制面账号 = CONTROL_PLANE_MYSQL_USER（默认 adas），用户本体由 compose 创建。
GRANT ALL PRIVILEGES ON `adas_mining_platform`.* TO 'adas'@'%';
FLUSH PRIVILEGES;
