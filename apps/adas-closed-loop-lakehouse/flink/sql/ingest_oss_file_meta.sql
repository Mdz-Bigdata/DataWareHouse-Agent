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
-- 字段清单由 ods_data_file_meta 的业务列派生（见 file_meta_message_schema），
-- 保证下面 SELECT 的每一列在源表里都存在；源表列一律可空，脏消息交给门禁拦
CREATE TEMPORARY TABLE `kafka_src_collect_file_meta` (
  `file_id` STRING COMMENT '文件 ID',
  `file_type` STRING COMMENT '文件类型：video/pointcloud/radar/imu/gps/can',
  `data_id` STRING COMMENT '所属 clip 的 data_id',
  `object_key` STRING COMMENT '对象存储 key',
  `file_size_bytes` BIGINT COMMENT '文件大小',
  `checksum_md5` STRING COMMENT '文件校验和',
  `sensor_id` STRING COMMENT '产出传感器',
  `duration_sec` DOUBLE COMMENT '时长（秒）',
  `frame_group_modalities` STRING COMMENT '本帧组已到齐的模态清单（逗号分隔，字典 camera/lidar/radar/imu/gnss）。对应 QG-OSS-002 多模态完整性：同一帧组内相机/激光雷达/毫米波/IMU/GNSS 文件齐全，缺帧 P0 拒绝入湖（L1）',
  `continuous_frame_loss_rate` DOUBLE COMMENT '连续丢帧率（0~1）。对应 QG-OSS-003：连续丢帧率 > 1% 升级告警（阈值见 thresholds.CONTINUOUS_FRAME_LOSS_RATE_ESCALATE_THRESHOLD）',
  `vehicle_type` STRING COMMENT '车辆类型：collect 采集车 / production 量产车。QG-OSS-004、QG-OSS-005 的 when 前置条件字段——两档同步容差按车型分流，该列缺失会让 ±10ms / ±50ms 两条规则一起被跳过',
  `time_sync_error_ms` INT COMMENT '多传感器时间同步误差（毫秒，带符号，取绝对值判定）。对应 QG-OSS-004 / QG-OSS-005 时间同步：采集车硬件同步 ≤ ±10ms、量产车软同步 ≤ ±50ms，超限告警放行（L1）',
  `near_duplicate_similarity` DOUBLE COMMENT '与已入湖数据的最高相似度（0~1，由上游算好，门禁只做阈值判定）。对应 QG-OSS-011 近重复：超 0.98 标记抑制（L2 近重复抑制 / L5 近重复）。⚠️ 原文未明确，本项目设计：原文只说「近重复抑制」，既没说用什么度量、也没给判定口径；本项目落成一个 0~1 的相似度标量列，由上游算好后门禁只比阈值',
  `batch_missing_ratio` DOUBLE COMMENT '所属批次的缺片比例（0~1，批级统计量）。对应 QG-OSS-012 批次到达完整性：六维之及时性，批次轻微缺片 P3 观察告警。⚠️ 原文未明确，本项目设计：原文只说「批次到达完整性」，未给统计量形式；本项目落成批级缺片比例。注意它是「应到 vs 实到」的差，光看收到的记录推不出来，必须由上游随批次喂进 check_batch(batch_stats=...)',
  `vehicle_desensitized_flag` BOOLEAN COMMENT '车端脱敏标记：人脸/车牌等敏感信息在车端已脱敏。QG-OSS-001 脱敏标记合规的双标记之一，缺失或未置位即 P0 合规风险拒绝入湖',
  `cloud_compliance_decrypted_flag` BOOLEAN COMMENT '合规云脱密标记：数据已在合规云完成脱密处理。QG-OSS-001 脱敏标记合规的双标记之二，与车端脱敏标记必须同时为真',
  `decodable_flag` BOOLEAN COMMENT '文件可解码标记（上游解码探针的结论，随元信息落表）。QG-OSS-006 文件损坏检查在没有注入 file_probe 探针、也没有 checksum 比对时退化为读这一列；该列缺失会让「压缩损伤 / 传输截断」这条 P1 规则失去兜底判据',
  `file_path` STRING COMMENT '文件本体完整路径，按需读取',
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

-- 门禁判定：四项专属检查各出一个布尔列，两条分支共用。
-- 条件表达式只在这里写一次——写两遍迟早会改漏一处，让「通过」与「被拦」的口径打架。
CREATE TEMPORARY VIEW `gated_collect_file_meta` AS
SELECT
  *,
  (`redaction_vehicle_applied` IS TRUE AND `redaction_cloud_applied` IS TRUE AND `redaction_vehicle_operator` IS NOT NULL AND `redaction_vehicle_operator` <> '' AND `redaction_cloud_operator` IS NOT NULL AND `redaction_cloud_operator` <> '' AND `vehicle_desensitized_flag` IS TRUE AND `cloud_compliance_decrypted_flag` IS TRUE) AS `chk_redaction_marks_complete`,
  (`data_id` IS NOT NULL AND REGEXP(`data_id`, '^COLLECT_[A-Z0-9]+_[0-9]{14}_[0-9a-f]{4,}$')) AS `chk_data_id_format_valid`,
  (`file_size_bytes` IS NOT NULL AND `file_size_bytes` > 0 AND (`decodable_flag` IS NULL OR `decodable_flag` IS TRUE)) AS `chk_file_body_decodable`,
  (`file_path` IS NOT NULL AND `file_path` LIKE 's3://adas-raw/%' AND `checksum_md5` IS NOT NULL AND REGEXP(`checksum_md5`, '^[0-9a-fA-F]{32}$')) AS `chk_meta_oss_path_consistent`
FROM `kafka_src_collect_file_meta`;

-- 合规最后一道闸：本通道有 4 项专属检查，其中 2 项是 P0 级
--   [P0] 脱敏标记完整性：文件需携带「车端脱敏 + 合规云脱密」双合规标记，缺失即合规风险 → P0 拒绝入湖；落表的两个 flag 列必须与审计字段一致，否则湖里会留下一个「说自己脱过敏」的假标记
--   [P0] data_id 格式合法：全局数据 ID 格式与来源前缀合法性（血缘追溯起点）→ P0 拒绝入湖
--   [P1] 文件本体可解码：图像 / 点云文件完整性与可解码性校验 → P1 拒绝入湖；SQL 侧查完整性与上游探针回填的 decodable_flag（探针未跑过时该列为 NULL，按「查不了 ≠ 查出问题」放行），魔数级探针在 Python 侧 ingest.oss.probe_decodable
--   [P1] 元信息与 OSS 路径一致：file_path 合法且指向智驾云 OSS（bucket=adas-raw），checksum 可校验 → P1 拒绝入湖
-- 命中拒绝规则的数据进入五步异常闭环（拦截 → 隔离 → 告警 → 分流处置 → 复验）
-- 隔离分支的列清单由 catalog.registry 的 ods_quality_issue 派生，并显式写出列名：
--   契约里两套同义列名并存（source_record_key≈record_key、rule_id≈rule_ids、
--   issue_detail≈detail、isolate_time≈detected_at …），两套都填，读哪套都不取 NULL；
--   不显式写列名的 INSERT 一旦契约加列就按位错位，作业提交即失败。
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
    `frame_group_modalities`,
    `continuous_frame_loss_rate`,
    `vehicle_type`,
    `time_sync_error_ms`,
    `near_duplicate_similarity`,
    `batch_missing_ratio`,
    `vehicle_desensitized_flag`,
    `cloud_compliance_decrypted_flag`,
    `decodable_flag`,
    CURRENT_TIMESTAMP AS `_ingest_time`,
    '文件管理系统' AS `_source_system`
  FROM `gated_collect_file_meta`
  WHERE `chk_redaction_marks_complete`
    AND `chk_data_id_format_valid`
    AND `chk_file_body_decodable`
    AND `chk_meta_oss_path_consistent`;

  -- 分支二 · 命中拒绝规则 → 进隔离表，等待分流处置与复验
  -- raw_payload 存整条报文的 JSON：原始数据不丢失才谈得上复验重放
  INSERT INTO `paimon`.`adas_lakehouse`.`ods_quality_issue` (`issue_id`, `dt`, `data_id`, `project_code`, `vehicle_code`, `source_channel`, `target_table`, `source_table`, `source_system`, `source_record_key`, `record_key`, `rule_id`, `rule_ids`, `rule_dimension`, `dimension`, `severity`, `issue_level`, `issue_detail`, `detail`, `message`, `raw_payload`, `replayable`, `isolate_time`, `detected_at`, `handle_status`, `issue_status`, `recheck_round`, `recheck_count`, `escalated`, `_ingest_time`, `_source_system`)
  SELECT
    CONCAT('oss_file_', `file_id`, '_', DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyyMMddHHmmss')) AS `issue_id`,
    DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyy-MM-dd') AS `dt`,
    `data_id` AS `data_id`,
    `project_code` AS `project_code`,
    `vehicle_code` AS `vehicle_code`,
    'oss_file' AS `source_channel`,
    'ods_data_file_meta' AS `target_table`,
    'ods_data_file_meta' AS `source_table`,
    '文件管理系统' AS `source_system`,
    CAST(`file_id` AS STRING) AS `source_record_key`,
    CAST(`file_id` AS STRING) AS `record_key`,
    CONCAT_WS(',',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN 'redaction_marks_complete' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN 'data_id_format_valid' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN 'file_body_decodable' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN 'meta_oss_path_consistent' ELSE NULL END
    ) AS `rule_id`,
    CONCAT_WS(',',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN 'redaction_marks_complete' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN 'data_id_format_valid' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN 'file_body_decodable' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN 'meta_oss_path_consistent' ELSE NULL END
    ) AS `rule_ids`,
    CONCAT_WS(',',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN 'completeness' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN 'validity' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN 'accuracy' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN 'consistency' ELSE NULL END
    ) AS `rule_dimension`,
    CONCAT_WS(',',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN 'completeness' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN 'validity' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN 'accuracy' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN 'consistency' ELSE NULL END
    ) AS `dimension`,
    'ERROR' AS `severity`,
    CASE
      WHEN NOT `chk_redaction_marks_complete` THEN 'P0'
      WHEN NOT `chk_data_id_format_valid` THEN 'P0'
      WHEN NOT `chk_file_body_decodable` THEN 'P1'
      WHEN NOT `chk_meta_oss_path_consistent` THEN 'P1'
      ELSE 'P1'
    END AS `issue_level`,
    CONCAT_WS(' | ',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN '[P0] 脱敏标记完整性' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN '[P0] data_id 格式合法' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN '[P1] 文件本体可解码' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN '[P1] 元信息与 OSS 路径一致' ELSE NULL END
    ) AS `issue_detail`,
    CONCAT_WS(' | ',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN '[P0] 脱敏标记完整性' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN '[P0] data_id 格式合法' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN '[P1] 文件本体可解码' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN '[P1] 元信息与 OSS 路径一致' ELSE NULL END
    ) AS `detail`,
    CONCAT_WS('；',
      CASE WHEN NOT `chk_redaction_marks_complete` THEN '脱敏标记完整性' ELSE NULL END,
      CASE WHEN NOT `chk_data_id_format_valid` THEN 'data_id 格式合法' ELSE NULL END,
      CASE WHEN NOT `chk_file_body_decodable` THEN '文件本体可解码' ELSE NULL END,
      CASE WHEN NOT `chk_meta_oss_path_consistent` THEN '元信息与 OSS 路径一致' ELSE NULL END
    ) AS `message`,
    JSON_OBJECT(
      KEY 'file_id' VALUE `file_id`,
      KEY 'file_type' VALUE `file_type`,
      KEY 'data_id' VALUE `data_id`,
      KEY 'object_key' VALUE `object_key`,
      KEY 'file_size_bytes' VALUE `file_size_bytes`,
      KEY 'checksum_md5' VALUE `checksum_md5`,
      KEY 'sensor_id' VALUE `sensor_id`,
      KEY 'duration_sec' VALUE `duration_sec`,
      KEY 'frame_group_modalities' VALUE `frame_group_modalities`,
      KEY 'continuous_frame_loss_rate' VALUE `continuous_frame_loss_rate`,
      KEY 'vehicle_type' VALUE `vehicle_type`,
      KEY 'time_sync_error_ms' VALUE `time_sync_error_ms`,
      KEY 'near_duplicate_similarity' VALUE `near_duplicate_similarity`,
      KEY 'batch_missing_ratio' VALUE `batch_missing_ratio`,
      KEY 'vehicle_desensitized_flag' VALUE `vehicle_desensitized_flag`,
      KEY 'cloud_compliance_decrypted_flag' VALUE `cloud_compliance_decrypted_flag`,
      KEY 'decodable_flag' VALUE `decodable_flag`,
      KEY 'file_path' VALUE `file_path`,
      KEY 'storage_class' VALUE `storage_class`,
      KEY 'project_code' VALUE `project_code`,
      KEY 'vehicle_code' VALUE `vehicle_code`,
      KEY 'distributed_at' VALUE `distributed_at`,
      KEY 'redaction_vehicle_applied' VALUE `redaction_vehicle_applied`,
      KEY 'redaction_vehicle_operator' VALUE `redaction_vehicle_operator`,
      KEY 'redaction_vehicle_rules' VALUE `redaction_vehicle_rules`,
      KEY 'redaction_vehicle_time' VALUE `redaction_vehicle_time`,
      KEY 'redaction_cloud_applied' VALUE `redaction_cloud_applied`,
      KEY 'redaction_cloud_operator' VALUE `redaction_cloud_operator`,
      KEY 'redaction_cloud_rules' VALUE `redaction_cloud_rules`,
      KEY 'redaction_cloud_time' VALUE `redaction_cloud_time`
    ) AS `raw_payload`,
    TRUE AS `replayable`,
    CURRENT_TIMESTAMP AS `isolate_time`,
    CURRENT_TIMESTAMP AS `detected_at`,
    'isolated' AS `handle_status`,
    'isolated' AS `issue_status`,
    0 AS `recheck_round`,
    0 AS `recheck_count`,
    FALSE AS `escalated`,
    CURRENT_TIMESTAMP AS `_ingest_time`,
    '文件管理系统' AS `_source_system`
  FROM `gated_collect_file_meta`
  WHERE NOT (`chk_redaction_marks_complete`
    AND `chk_data_id_format_valid`
    AND `chk_file_body_decodable`
    AND `chk_meta_oss_path_consistent`);

END;

-- 元信息写入即可用：下游 DWD 加工与查询立即可以引用，文件本体通过 file_path 按需读取
-- SELECT `data_id`, `file_id`, `object_key`, `file_size_bytes`, `checksum_md5`
-- FROM `paimon`.`adas_lakehouse`.`ods_data_file_meta`
-- WHERE `file_type` = 'pointcloud' AND `data_id` = 'COLLECT_BP_20260301123045_b7e2';
