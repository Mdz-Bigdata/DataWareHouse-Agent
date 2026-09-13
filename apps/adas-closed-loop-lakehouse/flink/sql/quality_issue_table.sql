-- ============================================================================
-- quality_issue_table.sql  质量门禁隔离表 ods_quality_issue
--
-- 来源：系列二 · 湖仓实战 第 6 篇《数据质量门禁设计：智驾数据入湖的五步校验链路》
--       第五章「隔离表是这套闭环的物理基础，核心字段全部围绕『可重放、可追责』设计」
--
-- 本文件由 python -c "from adas_lakehouse.quality.tables import render_ddl; print(render_ddl())"
-- 渲染，字段清单以 src/adas_lakehouse/quality/tables.py 为准，勿手工改字段。
--
-- 物理策略（共享契约 catalog/spec.py）：
--   · 分区 dt      —— 全湖仅 6 张分区表之一，规则一「大体量 + 时间范围查询」
--   · 主键 (issue_id, dt) —— 原则三：分区表主键必须包含分区字段
--   · bucket 4     —— 中等体量 ODS
--   · changelog-producer input —— ODS 层默认
-- ============================================================================

SET 'execution.runtime-mode' = 'batch';

-- ods_quality_issue  [分析域 / ODS]  质量门禁异常隔离表（拦截 → 隔离 → 告警 → 分流处置 → 复验 五步闭环的物理基础）
-- 来源系统: 质量门禁（quality-gate）
-- 备注: 分区规则一：大体量 + 时间范围查询 → 按 dt 分区；主键原则三：分区表主键必须包含分区字段；issue_id 幂等派生，重放不产生重复行
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_quality_issue` (
  `issue_id` STRING NOT NULL COMMENT '异常 ID，由 表+记录键+报文哈希+命中规则 派生，重放幂等',
  `dt` STRING NOT NULL COMMENT '异常发现日期（分区字段，yyyy-MM-dd）',
  `detected_at` TIMESTAMP(3) NOT NULL COMMENT '门禁拦截时刻',
  `source_table` STRING NOT NULL COMMENT '被拦截数据的目标表',
  `source_channel` STRING COMMENT '入湖通道：mysql_cdc/kafka/oss_file/common',
  `source_system` STRING COMMENT '来源系统标识（业务列，区别于系统字段 _source_system）',
  `record_key` STRING COMMENT '被拦截记录的业务键',
  `data_id` STRING COMMENT '一级 ID：clip 级终身锚点',
  `artifact_id` STRING COMMENT '二级 ID：处理产物',
  `run_id` STRING COMMENT '三级 ID：处理运行',
  `parent_artifact_id` STRING COMMENT '血缘父产物（图库对账兜底）',
  `project_code` STRING COMMENT '项目码',
  `vehicle_code` STRING COMMENT '车辆编码',
  `rule_ids` STRING NOT NULL COMMENT '命中的规则 ID 列表（逗号分隔）',
  `severity` STRING NOT NULL COMMENT '严重程度：ERROR（拒绝入湖）/ WARNING（带标记放行）',
  `issue_level` STRING NOT NULL COMMENT '异常等级：P0 合规 / P1 严重 / P2 一般 / P3 观察',
  `dimension` STRING COMMENT '六维之一：completeness/accuracy/consistency/uniqueness/validity/timeliness',
  `quality_layer` STRING COMMENT '五层质量问题之一：L1~L5',
  `message` STRING COMMENT '命中说明',
  `detail` STRING COMMENT '检查器给出的具体偏差',
  `hits_json` STRING COMMENT '全部命中明细（JSON 数组）',
  `raw_payload` STRING NOT NULL COMMENT '原始报文（JSON）。原始数据不丢失是可重放、可审计的根基',
  `payload_hash` STRING NOT NULL COMMENT '原始报文哈希',
  `payload_object_key` STRING COMMENT '超大报文的对象存储 key（内联超限时使用）',
  `replayable` BOOLEAN COMMENT '是否可重放',
  `issue_status` STRING COMMENT '状态：isolated/alerted/dispatched/repaired/rechecking/reingested/discarded',
  `repair_action` STRING COMMENT '分流处置：A 自动修复 / B 人工修复 / C 弃置归档',
  `recheck_count` INT COMMENT '复验轮次，超 3 轮升级 P0',
  `escalated` BOOLEAN COMMENT '是否已升级',
  `owner` STRING COMMENT '数据 owner（告警对象）',
  `on_duty` STRING COMMENT '平台值班（告警对象）',
  `response_due_at` TIMESTAMP(3) COMMENT '响应截止时间（P0 30 分钟 / P1 2 小时）',
  `closure_due_at` TIMESTAMP(3) COMMENT '闭环截止时间（P1 当日 / P2 3 个工作日）',
  `responded_at` TIMESTAMP(3) COMMENT '实际响应时间',
  `resolved_at` TIMESTAMP(3) COMMENT '闭环时间',
  `sla_met` BOOLEAN COMMENT '是否达成 SLA（异常处理 SLA 达成率的分子）',
  `reingested_at` TIMESTAMP(3) COMMENT '复验通过重入湖时间',
  `discard_reason` STRING COMMENT '弃置归档原因（C 分支必填，保留审计）',
  `gate_version` STRING COMMENT '拦下它的门禁版本',
  `history` STRING COMMENT '处理轨迹（JSON 数组，可追责）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`issue_id`, `dt`) NOT ENFORCED
) PARTITIONED BY (`dt`)
WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

