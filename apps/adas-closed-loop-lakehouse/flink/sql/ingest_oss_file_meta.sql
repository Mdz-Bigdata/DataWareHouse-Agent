-- ========================================================================
-- 通道三 · OSS 合规上传：采集文件元信息 → ODS
-- 由 adas_lakehouse.ingest.sql 生成，请勿手工编辑。
-- 文件本体存 OSS（大文件外置），湖仓只存元信息与解析后的关键信息。
-- 合规靠架构边界：合规云与智驾云同一 VPC 内网流转、不对外暴露。
-- ========================================================================


-- 通道三 · OSS 合规上传：采集大文件（图像 / 点云 / 传感器数据）
-- 入湖方式：文件本体存 OSS · 元信息经 Kafka 入湖（大文件外置，湖仓只存元信息）
-- 五步合规链路：① 车端脱敏 → ② 合规室上传 → ③ 合规脱密 → ④ 合规数据分发 → ⑤ 实时入湖
-- 本脚本落地第 ⑤ 步；第 ①~④ 步在车端 / 合规室 / 合规云 / 智驾云完成（见 ingest.compliance）
-- 消息结构比 ods_data_file_meta 宽：双合规标记与 storage_class 是门禁判据，
-- 共享契约表未设对应列，故只作为过滤条件参与，不写入湖表
CREATE TEMPORARY TABLE `kafka_src_collect_file_meta` (
  `file_id` STRING NOT NULL COMMENT '文件 ID',
  `file_type` STRING NOT NULL COMMENT '文件类型：video/pointcloud/radar/imu/gps/can（分区字段）',
  `data_id` STRING COMMENT '所属 clip 的 data_id：全局三级 ID 的起点',
  `object_key` STRING COMMENT '对象存储 key',
  `file_path` STRING COMMENT '文件本体完整路径，按需读取',
  `file_size_bytes` BIGINT COMMENT '文件大小',
  `checksum_md5` STRING COMMENT '文件校验和，完整性随时可验证',
  `sensor_id` STRING COMMENT '产出传感器',
  `duration_sec` DOUBLE COMMENT '时长（秒）',
  `storage_class` STRING COMMENT '存储级别：standard/infrequent/archive，对接生命周期管理',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '车辆编码',
  `distributed_at` TIMESTAMP(3) COMMENT '合规数据副本分发至智驾云 OSS 的时间',
  `redaction_vehicle_applied` BOOLEAN COMMENT '车端脱敏标记：人脸/车牌个性脱敏 + 地信脱敏',
  `redaction_vehicle_operator` STRING COMMENT '车端脱敏执行方',
  `redaction_vehicle_rules` STRING COMMENT '车端脱敏生效规则',
  `redaction_vehicle_time` TIMESTAMP(3) COMMENT '车端脱敏时间',
  `redaction_cloud_applied` BOOLEAN COMMENT '合规云脱密标记：删除敏感 POI + 模糊桥梁限高',
  `redaction_cloud_operator` STRING COMMENT '合规云脱密执行方（具备资质的合规公司）',
  `redaction_cloud_rules` STRING COMMENT '合规云脱密生效规则',
  `redaction_cloud_time` TIMESTAMP(3) COMMENT '合规云脱密时间'
) WITH (
  'connector' = 'kafka',
  'topic' = 'collect.file.meta',
  'properties.bootstrap.servers' = 'localhost:18692',
  'properties.group.id' = 'adas-lakehouse-ods_data_file_meta',
  'scan.startup.mode' = 'group-offsets',
  'properties.auto.offset.reset' = 'earliest',
  'format' = 'json',
  'json.ignore-parse-errors' = 'false',
  'json.fail-on-missing-field' = 'false'
);

-- 合规最后一道闸：本通道有 4 项专属检查，其中 2 项是 P0 级
--   [P0] 脱敏标记完整性：文件需携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险 → P0 拒绝入湖
--   [P0] data_id 格式合法：全局数据 ID 格式与来源前缀合法性（血缘追溯起点）→ P0 拒绝入湖
--   [P1] 文件本体可解码：图像 / 点云文件完整性与可解码性校验 → P1 拒绝入湖；SQL 侧只能查完整性，魔数级可解码探针在 Python 侧 ingest.oss.probe_decodable
--   [P1] 元信息与 OSS 路径一致：file_path 合法且指向智驾云 OSS（bucket=adas-raw），checksum 可校验 → P1 拒绝入湖
-- 命中拒绝规则的数据进入五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）
-- ⚠️ 隔离表 ods_quality_issue 的列以 quality 子系统的定义为准；
--    若有出入，只需改下面 SELECT 的列别名，条件表达式不用动
EXECUTE STATEMENT SET
BEGIN

  -- 分支一 · 通过门禁 → 元信息写入即可用，下游 DWD 立即可引用，本体按 file_path 按需读取
  INSERT INTO `paimon`.`adas_lakehouse`.`ods_data_file_meta`
  SELECT
    `file_id`,
    `file_type`,
    `data_id`,
    `object_key`,
    `file_size_bytes`,
    `checksum_md5`,
    `sensor_id`,
    `duration_sec`,
    CURRENT_TIMESTAMP AS `_ingest_time`,
    '文件管理系统' AS `_source_system`
  FROM `kafka_src_collect_file_meta`
  WHERE (`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '')
    AND (`data_id` IS NOT NULL AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$'))
    AND (`file_size_bytes` IS NOT NULL AND `file_size_bytes` > 0)
    AND (`file_path` IS NOT NULL AND `file_path` LIKE 's3://adas-raw/%' AND `checksum_md5` IS NOT NULL AND REGEXP(`checksum_md5`, '^[0-9a-fA-F]{32}$'));

  -- 分支二 · 命中拒绝规则 → 进隔离表，等待分流处置与复验
  INSERT INTO `paimon`.`adas_lakehouse`.`ods_quality_issue`
  SELECT
    CONCAT('oss_', `file_id`, '_', DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyyMMddHHmmss')) AS `issue_id`,
    DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyy-MM-dd') AS `dt`,
    `file_id` AS `subject_id`,
    `data_id`,
    'OSS 合规上传' AS `source_channel`,
    'ods_data_file_meta' AS `target_table`,
    CASE
      WHEN NOT (`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '') THEN 'P0'
      WHEN NOT (`data_id` IS NOT NULL AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$')) THEN 'P0'
      ELSE 'P1'
    END AS `severity`,
    NOT (`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '') AS `is_compliance_issue`,
    CONCAT_WS(',',
      CASE WHEN NOT (`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '') THEN 'redaction_marks_complete' ELSE NULL END,
      CASE WHEN NOT (`data_id` IS NOT NULL AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$')) THEN 'data_id_format_valid' ELSE NULL END,
      CASE WHEN NOT (`file_size_bytes` IS NOT NULL AND `file_size_bytes` > 0) THEN 'file_body_decodable' ELSE NULL END,
      CASE WHEN NOT (`file_path` IS NOT NULL AND `file_path` LIKE 's3://adas-raw/%' AND `checksum_md5` IS NOT NULL AND REGEXP(`checksum_md5`, '^[0-9a-fA-F]{32}$')) THEN 'meta_oss_path_consistent' ELSE NULL END
    ) AS `check_codes`,
    CONCAT_WS(' | ',
      CASE WHEN NOT (`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '') THEN '[P0] 脱敏标记完整性' ELSE NULL END,
      CASE WHEN NOT (`data_id` IS NOT NULL AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$')) THEN '[P0] data_id 格式合法' ELSE NULL END,
      CASE WHEN NOT (`file_size_bytes` IS NOT NULL AND `file_size_bytes` > 0) THEN '[P1] 文件本体可解码' ELSE NULL END,
      CASE WHEN NOT (`file_path` IS NOT NULL AND `file_path` LIKE 's3://adas-raw/%' AND `checksum_md5` IS NOT NULL AND REGEXP(`checksum_md5`, '^[0-9a-fA-F]{32}$')) THEN '[P1] 元信息与 OSS 路径一致' ELSE NULL END
    ) AS `issue_reason`,
    '拦截' AS `closed_loop_step`,
    CAST(`file_id` AS STRING) AS `raw_payload`,
    CURRENT_TIMESTAMP AS `intercepted_at`,
    CURRENT_TIMESTAMP AS `updated_at`,
    CURRENT_TIMESTAMP AS `_ingest_time`,
    '文件管理系统' AS `_source_system`
  FROM `kafka_src_collect_file_meta`
  WHERE NOT ((`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '')
    AND (`data_id` IS NOT NULL AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$'))
    AND (`file_size_bytes` IS NOT NULL AND `file_size_bytes` > 0)
    AND (`file_path` IS NOT NULL AND `file_path` LIKE 's3://adas-raw/%' AND `checksum_md5` IS NOT NULL AND REGEXP(`checksum_md5`, '^[0-9a-fA-F]{32}$')));

END;

-- 元信息写入即可用：下游 DWD 加工与查询立即可以引用，文件本体通过 file_path 按需读取
-- SELECT `data_id`, `file_id`, `object_key`, `file_size_bytes`, `checksum_md5`
-- FROM `paimon`.`adas_lakehouse`.`ods_data_file_meta`
-- WHERE `file_type` = 'pointcloud' AND `data_id` = 'COLLECT_BP_20260301123045_b7e2';
