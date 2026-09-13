-- ========================================================================
-- 湖仓建表 · ODS 层
-- 由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：python3 scripts/export_ddl.py
-- 层级定位：原始同步层：源系统什么样就存什么样，不加业务逻辑，只做字段映射与系统字段补充
-- 系统字段：_ingest_time + _source_system
-- 本层共 33 张表：
--   · 采集域: 4 张
--   · 生产域: 8 张
--   · 数据资产域: 4 张
--   · 训练域: 3 张
--   · 评测域: 3 张
--   · 仿真域: 2 张
--   · 回传域: 3 张
--   · 部署域: 2 张
--   · 分析域: 1 张
--   · 挖掘域: 2 张
--   · [伪域]quality: 1 张
-- 物理策略（分区 / bucket / changelog-producer）由 catalog/spec.py 硬校验后渲染。
-- ========================================================================

-- ods_collect_task  [采集域 / ODS]  采集任务
-- 来源系统: 采集管理系统
-- 备注: [a12] 第四章 Bucket 五档表点名本表作 4 档「中等体量 ODS/DWD」的代表表（与 dwd_training_task_detail 并列）；[a10] 第五章①以本表举「业务主键 + NOT ENFORCED」——PK 取 collect_task_id，Paimon 不在写入时强制校验唯一性（由上游保证），但按主键做 Upsert 合并
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_collect_task` (
  `collect_task_id` STRING NOT NULL COMMENT '采集任务 ID',
  `task_name` STRING COMMENT '任务名称',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '执行车辆编码',
  `target_scene` STRING COMMENT '目标采集场景',
  `planned_duration_min` INT COMMENT '计划采集时长（分钟）',
  `actual_clip_count` INT COMMENT '实际产出 clip 数',
  `task_status` STRING COMMENT '任务状态',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`collect_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_data_file_meta  [采集域 / ODS]  采集文件元信息
-- 来源系统: 文件管理系统
-- 备注: 分区规则二：有明确业务分类过滤 → 按业务字段分区；文件类型体量差异大
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_data_file_meta` (
  `file_id` STRING NOT NULL COMMENT '文件 ID',
  `file_type` STRING NOT NULL COMMENT '文件类型：video/pointcloud/radar/imu/gps/can',
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
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`file_id`, `file_type`) NOT ENFORCED
) PARTITIONED BY (`file_type`)
WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_sensor_config  [采集域 / ODS]  传感器配置
-- 来源系统: 配置管理系统
-- 备注: 复合主键表达完整粒度：同一车辆挂载多个传感器
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_sensor_config` (
  `vehicle_code` STRING NOT NULL COMMENT '车辆编码',
  `sensor_id` STRING NOT NULL COMMENT '传感器 ID',
  `sensor_type` STRING COMMENT '类型：camera/lidar/radar/imu/gps',
  `sensor_model` STRING COMMENT '型号',
  `mount_position` STRING COMMENT '安装位置',
  `intrinsic_json` STRING COMMENT '内参（JSON）',
  `extrinsic_json` STRING COMMENT '外参（JSON）',
  `sample_rate_hz` DOUBLE COMMENT '采样率',
  `effective_from` TIMESTAMP(3) COMMENT '生效时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`vehicle_code`, `sensor_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_vehicle_info  [采集域 / ODS]  车辆基础信息
-- 来源系统: 车辆管理系统
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_vehicle_info` (
  `vehicle_code` STRING NOT NULL COMMENT '车辆编码，data_id 第二段来源',
  `vin` STRING COMMENT '车架号',
  `vehicle_model` STRING COMMENT '车型',
  `fleet_name` STRING COMMENT '所属车队',
  `autonomy_level` STRING COMMENT '智驾等级',
  `register_date` DATE COMMENT '入队日期',
  `status` STRING COMMENT '车辆状态',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`vehicle_code`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_annotation_result  [生产域 / ODS]  标注结果（结果文件外置 OSS，湖仓只存元信息）
-- 来源系统: 标注平台
-- 备注: 大文件外置：标注结果 JSON 存 OSS，此表只存 key 与统计量
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_annotation_result` (
  `annotation_result_id` STRING NOT NULL COMMENT '标注结果 ID',
  `annotation_task_id` STRING COMMENT '所属标注任务 ID',
  `data_id` STRING COMMENT '数据单元 data_id',
  `artifact_id` STRING COMMENT '标注产物 ID（二级 ID）',
  `frame_index` INT COMMENT '帧序号',
  `object_count` INT COMMENT '标注目标数',
  `label_object_key` STRING COMMENT '标注结果文件对象存储 key',
  `annotation_version` STRING COMMENT '标注结果版本',
  `annotator` STRING COMMENT '标注员账号',
  `annotate_duration_sec` DOUBLE COMMENT '标注耗时（秒）',
  `submit_time` TIMESTAMP(3) COMMENT '提交时间',
  `self_check_score` DOUBLE COMMENT '标注员自检得分',
  `is_rework` BOOLEAN COMMENT '是否返工产出',
  `rework_reason` STRING COMMENT '返工原因',
  `qc_result_id` STRING COMMENT '关联的质检结果 ID（冗余自 ods_qc_result.qc_result_id，跨源关联存在性判据）。对应 QG-CDC-001 标注质量准入：标注结果必须关联质检结论，质检缺失拒绝入湖',
  `qc_conclusion` STRING COMMENT '冗余落列的质检结论（取值随 ods_qc_result.qc_conclusion）。对应 QG-CDC-002 标注质量准入：结论不为通过则拒绝入湖（L3 门禁动作「未过质检不入湖」）',
  `annotation_source` STRING COMMENT '标注来源：manual/auto_label/pretrain_model/llm_prelabel。QG-CDC-003 的 when 前置条件字段——只有预标注来源才要求人工复核，该列缺失会让整条自动标注准入规则被跳过。⚠️ 原文未明确，本项目设计：原文只说「预标注结果必须人工审核」，没有给出区分来源的字段，更没有给取值字典；这四个取值由本项目枚举',
  `human_review_status` STRING COMMENT '预标注结果的人工复核状态：pending/reviewed/approved/rejected。对应 QG-CDC-003 自动标注准入：大模型/离线模型预标注未经人工审核不得入训练数据集。⚠️ 原文未明确，本项目设计：原文只要求「经人工审核」这一动作，没说审核状态怎么留痕；本项目落成四态状态列，门禁按 reviewed/approved 放行',
  `task_status` STRING COMMENT '标注任务状态：created/annotating/qc_pending/qc_passed/qc_rejected/delivered/cancelled。对应 QG-CDC-006 状态流转合法性校验（六维之有效性「状态流转合法」，P2 告警标记放行）',
  `prev_task_status` STRING COMMENT '上一状态，QG-CDC-006 的 params.from_field——状态机要比对「旧 → 新」才判得了非法流转，只有 task_status 而没有它，这条规则同样永远判不出命中。⚠️ 原文未明确，本项目设计：原文只说「状态流转合法」，既没给状态机也没说前态如何取得；本项目选择在行上冗余一列前态，而不是让门禁回查上一版本——门禁热路径不挂重活',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`annotation_result_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_annotation_task  [生产域 / ODS]  标注任务
-- 来源系统: 标注平台
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_annotation_task` (
  `annotation_task_id` STRING NOT NULL COMMENT '标注任务 ID',
  `batch_id` STRING COMMENT '所属批次 ID',
  `project_code` STRING COMMENT '所属项目',
  `data_id` STRING COMMENT '待标注数据单元 data_id',
  `artifact_id` STRING COMMENT '输入产物 ID（前处理产物）',
  `annotation_type` STRING COMMENT '标注类型：2d_box/3d_box/lane/semantic_seg/tracking',
  `vendor_name` STRING COMMENT '标注供应商',
  `annotator` STRING COMMENT '标注员账号',
  `planned_object_count` INT COMMENT '计划标注目标数',
  `task_status` STRING COMMENT '任务状态：assigned/annotating/submitted/rework/done',
  `assign_time` TIMESTAMP(3) COMMENT '派单时间',
  `start_time` TIMESTAMP(3) COMMENT '开始标注时间',
  `finish_time` TIMESTAMP(3) COMMENT '完成时间',
  `deadline` TIMESTAMP(3) COMMENT '交付截止时间',
  `price_per_unit` DECIMAL(12,4) COMMENT '标注单价',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`annotation_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_argo_workflow  [生产域 / ODS]  Argo 工作流运行元数据
-- 来源系统: Argo 元数据库
-- 备注: 产线各环节由 Argo Workflow 编排，此表是 run_id 与 K8s 实际执行的对照
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_argo_workflow` (
  `workflow_uid` STRING NOT NULL COMMENT '工作流唯一 ID（Argo UID）',
  `workflow_name` STRING COMMENT '工作流名称',
  `namespace` STRING COMMENT 'K8s 命名空间',
  `template_name` STRING COMMENT '工作流模板名',
  `line_task_id` STRING COMMENT '关联产线任务 ID',
  `run_id` STRING COMMENT '三级 ID：对应的处理运行 ID',
  `phase` STRING COMMENT '运行阶段：Pending/Running/Succeeded/Failed/Error',
  `started_at` TIMESTAMP(3) COMMENT '工作流开始时间',
  `finished_at` TIMESTAMP(3) COMMENT '工作流结束时间',
  `duration_sec` DOUBLE COMMENT '运行时长（秒）',
  `node_count` INT COMMENT '工作流节点数',
  `pod_count` INT COMMENT '拉起的 Pod 数',
  `cpu_core` DOUBLE COMMENT '申请 CPU 核数',
  `memory_gb` DOUBLE COMMENT '申请内存（GB）',
  `parameters_json` STRING COMMENT '入参快照（JSON），保证可重放',
  `exit_message` STRING COMMENT '退出信息',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`workflow_uid`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_manual_operation_log  [生产域 / ODS]  人工操作日志（产线埋点事件流）
-- 来源系统: Kafka
-- 备注: Kafka 通道入湖，保留回放能力；表名不含数据域段，域归属以本注册表声明为准
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_manual_operation_log` (
  `operation_id` STRING NOT NULL COMMENT '操作流水 ID',
  `operator` STRING COMMENT '操作人账号',
  `operator_role` STRING COMMENT '操作人角色：标注员/质检员/产线运维/数据负责人',
  `operation_type` STRING COMMENT '操作类型：rerun/invalidate/repriority/manual_fix/force_pass',
  `target_type` STRING COMMENT '操作对象类型：task/data/artifact/run',
  `target_id` STRING COMMENT '操作对象 ID',
  `data_id` STRING COMMENT '关联数据单元 data_id',
  `run_id` STRING COMMENT '关联处理运行 run_id',
  `platform` STRING COMMENT '来源平台：产线平台/标注平台/质检平台',
  `operation_time` TIMESTAMP(3) COMMENT '操作时间',
  `before_value` STRING COMMENT '操作前取值',
  `after_value` STRING COMMENT '操作后取值',
  `reason` STRING COMMENT '操作原因',
  `client_ip` STRING COMMENT '来源 IP',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`operation_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_production_kafka_event  [生产域 / ODS]  产线埋点事件流
-- 来源系统: Kafka
-- 备注: 分区规则二：有明确业务分类过滤 → 按业务字段分区；事件类型间数据量差异极大。原则三：分区表主键必须包含分区字段 event_type
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_production_kafka_event` (
  `event_id` STRING NOT NULL COMMENT '事件 ID',
  `event_type` STRING NOT NULL COMMENT '事件类型：stage_start/stage_end/stage_fail/upload_done/deliver_done',
  `data_id` STRING COMMENT '关联数据单元 data_id',
  `artifact_id` STRING COMMENT '关联产物 artifact_id',
  `run_id` STRING COMMENT '关联处理运行 run_id',
  `line_task_id` STRING COMMENT '关联产线任务 ID',
  `stage_code` STRING COMMENT '产线环节编码',
  `event_status` STRING COMMENT '事件携带的状态值',
  `event_time` TIMESTAMP(3) COMMENT '事件发生时间',
  `producer_platform` STRING COMMENT '事件产生平台',
  `kafka_topic` STRING COMMENT '来源 Kafka topic',
  `kafka_partition` INT COMMENT 'Kafka 分区号，支持按位点回放',
  `kafka_offset` BIGINT COMMENT 'Kafka 位点',
  `trace_id` STRING COMMENT '链路追踪 ID',
  `payload_json` STRING COMMENT '原始事件体（JSON）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`event_id`, `event_type`) NOT ENFORCED
) PARTITIONED BY (`event_type`)
WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_production_line_task  [生产域 / ODS]  产线任务（14 环节调度的最小派发单元）
-- 来源系统: 产线平台 MySQL
-- 备注: Flink CDC 读 binlog 原样入湖，字段命名不规范之处留给 DWD 层修正
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_production_line_task` (
  `line_task_id` STRING NOT NULL COMMENT '产线任务 ID',
  `batch_id` STRING COMMENT '所属批次 ID',
  `project_code` STRING COMMENT '所属项目',
  `data_id` STRING COMMENT '处理的数据单元 data_id（一级 ID）',
  `stage_code` STRING COMMENT '产线环节编码：upload/tagging/preprocess/annotate/qc/postprocess/deliver',
  `pipeline_name` STRING COMMENT '流水线名称',
  `algo_version` STRING COMMENT '算法版本',
  `priority` INT COMMENT '优先级，数值越小越先调度',
  `task_status` STRING COMMENT '任务状态：pending/running/success/failed/canceled',
  `submit_time` TIMESTAMP(3) COMMENT '提交时间',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `operator` STRING COMMENT '提交人账号',
  `retry_count` INT COMMENT '重试次数',
  `error_message` STRING COMMENT '失败信息',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`line_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_qc_result  [生产域 / ODS]  质检结果
-- 来源系统: 质检平台
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_qc_result` (
  `qc_result_id` STRING NOT NULL COMMENT '质检结果 ID',
  `qc_task_id` STRING COMMENT '所属质检任务 ID',
  `annotation_result_id` STRING COMMENT '被质检的标注结果 ID',
  `data_id` STRING COMMENT '数据单元 data_id',
  `artifact_id` STRING COMMENT '被质检产物 ID',
  `qc_conclusion` STRING COMMENT '质检结论：pass/reject/rework',
  `defect_type` STRING COMMENT '缺陷类型：miss_label/wrong_label/box_inaccurate/attr_error',
  `defect_count` INT COMMENT '缺陷数',
  `checked_object_count` INT COMMENT '实际抽检目标数',
  `accuracy_rate` DOUBLE COMMENT '本次质检准确率',
  `inspector` STRING COMMENT '质检员账号',
  `check_time` TIMESTAMP(3) COMMENT '质检时间',
  `rework_round` INT COMMENT '返工轮次，0 表示首检',
  `remark` STRING COMMENT '质检备注',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`qc_result_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_qc_task  [生产域 / ODS]  质检任务
-- 来源系统: 质检平台
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_qc_task` (
  `qc_task_id` STRING NOT NULL COMMENT '质检任务 ID',
  `annotation_task_id` STRING COMMENT '被质检的标注任务 ID',
  `batch_id` STRING COMMENT '所属批次 ID',
  `project_code` STRING COMMENT '所属项目',
  `data_id` STRING COMMENT '被质检数据单元 data_id',
  `qc_type` STRING COMMENT '质检类型：sampling/full/machine',
  `sample_ratio` DOUBLE COMMENT '抽检比例',
  `planned_sample_count` INT COMMENT '计划抽检目标数',
  `inspector` STRING COMMENT '质检员账号',
  `qc_status` STRING COMMENT '质检状态：assigned/checking/finished/canceled',
  `priority` INT COMMENT '优先级',
  `assign_time` TIMESTAMP(3) COMMENT '派单时间',
  `start_time` TIMESTAMP(3) COMMENT '开始质检时间',
  `finish_time` TIMESTAMP(3) COMMENT '完成时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`qc_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_quality_issue  [生产域 / ODS]  入湖质量门禁异常隔离表（拦截数据原样留存，支撑复验重入湖）
-- 来源系统: 入湖质量门禁服务
-- 备注: 质量门禁伪域（quality_），不属于 11 数据域，见 domains.QUALITY_GATE_PSEUDO_DOMAIN；domain 暂挂生产域仅为满足注册表类型要求。分区规则一：异常量随上游批量变更突增，按 dt 分区支撑按天 TTL 清理；主键原则三：分区表主键必须包含分区字段，故 PK=(issue_id, dt)
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_quality_issue` (
  `issue_id` STRING NOT NULL COMMENT '异常记录 ID，门禁拦截瞬间生成的业务主键',
  `dt` STRING NOT NULL COMMENT '隔离日期分区（yyyy-MM-dd），按天 TTL 清理',
  `data_id` STRING COMMENT '一级 ID：关联的 clip 锚点；ID 格式非法被拦时可能为空',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '来源车辆编码',
  `source_channel` STRING COMMENT '入湖通道：cdc/kafka/oss',
  `target_table` STRING COMMENT '本应写入的目标 ODS 表名，复验通过后的重放目的地',
  `source_record_key` STRING COMMENT '原始记录定位键：CDC 主键 / Kafka event_id / OSS object_key',
  `rule_id` STRING COMMENT '命中的门禁规则 ID（规则中心 YAML 配置注册）',
  `rule_dimension` STRING COMMENT '六维检查维度：completeness/accuracy/consistency/uniqueness/validity/timeliness',
  `severity` STRING COMMENT '检查器严重度：ERROR→拒绝入湖 / WARNING→带标记放行',
  `issue_level` STRING COMMENT '异常等级：P0 合规（脱敏缺失/主键为空/整帧缺失，30 分钟响应）/P1 严重/P2 一般/P3 观察',
  `issue_detail` STRING COMMENT '规则命中详情：期望值 vs 实际值',
  `raw_payload` STRING COMMENT '被拦截的原始报文（JSON），原始数据不丢失是可重放的根基',
  `isolate_time` TIMESTAMP(3) COMMENT '隔离时间：写入本表的时刻，告警 SLA 计时起点',
  `handle_strategy` STRING COMMENT '分流处置：A 自动修复 / B 人工修复 / C 弃置归档',
  `handle_status` STRING COMMENT '处置状态：pending/handling/rechecking/resolved/archived',
  `handle_owner` STRING COMMENT '处置责任人：数据 owner 或平台值班',
  `recheck_round` INT COMMENT '复验轮次，复验不通过退回隔离，超 3 轮升级 P0',
  `resolved_time` TIMESTAMP(3) COMMENT '闭环时间：复验通过重入湖或归档的时刻',
  `detected_at` TIMESTAMP(3) COMMENT '门禁拦截时刻（原文第五章 ① 门禁拦截），告警 SLA 计时起点',
  `source_table` STRING COMMENT '被拦截数据本应写入的目标 ODS 表——复验通过后的重放目的地',
  `source_system` STRING COMMENT '来源系统标识（业务列，区别于 ODS 系统字段 _source_system）',
  `record_key` STRING COMMENT '被拦截记录的业务定位键：CDC 主键 / Kafka event_id / OSS object_key',
  `artifact_id` STRING COMMENT '二级 ID：处理产物 ID',
  `run_id` STRING COMMENT '三级 ID：处理运行 ID',
  `parent_artifact_id` STRING COMMENT '血缘父产物 ID，冗余落表以便图库对账兜底',
  `rule_ids` STRING COMMENT '命中的规则 ID 列表（逗号分隔）。原文第三章：被拒绝的数据连同命中规则一起落表，而不是打日志了事——一次检查可命中多条规则，故是列表不是单值',
  `dimension` STRING COMMENT '六维检查维度：completeness/accuracy/consistency/uniqueness/validity/timeliness',
  `quality_layer` STRING COMMENT '五层质量问题定位（原文第一章全景表）：L1 传感器 / L2 量产车回传 / L3 标注 / L4 分布与场景 / L5 数据工程与训练评测',
  `message` STRING COMMENT '命中说明（规则声明里的 message）',
  `detail` STRING COMMENT '检查器给出的具体偏差：期望值 vs 实际值',
  `hits_json` STRING COMMENT '全部命中明细（JSON 数组），逐条带 rule_id/severity/detail',
  `payload_hash` STRING COMMENT '原始报文哈希，参与 issue_id 派生，保证重放/重试幂等',
  `payload_object_key` STRING COMMENT '超大报文的对象存储 key：内联超过 thresholds.RAW_PAYLOAD_INLINE_MAX_BYTES 时退化为外置',
  `replayable` BOOLEAN COMMENT '是否具备重放条件（原始报文完整 + 目标表已知）',
  `issue_status` STRING COMMENT '五步闭环状态机：isolated/alerted/dispatched/repaired/rechecking/reingested/discarded',
  `repair_action` STRING COMMENT '④ 分流处置：A 自动修复（重传/幂等重放/断点续传）/ B 人工修复（源端补数，工单跟踪）/ C 弃置归档（无法修复，标记原因后归档保留审计）',
  `recheck_count` INT COMMENT '⑤ 复验轮次：复验不通过退回隔离，超 3 轮升级 P0（thresholds.MAX_RECHECK_ROUNDS）',
  `escalated` BOOLEAN COMMENT '是否已升级（超 3 轮复验升 P0；P3 连续两周超标升 P2）',
  `owner` STRING COMMENT '数据 owner——③ 分级告警的通知对象之一',
  `on_duty` STRING COMMENT '平台值班——③ 分级告警的通知对象之一',
  `response_due_at` TIMESTAMP(3) COMMENT '响应截止时间：P0 电话+钉钉 30 分钟内 / P1 钉钉+工单 2 小时内',
  `closure_due_at` TIMESTAMP(3) COMMENT '闭环截止时间：P1 当日修复 / P2 日报汇总 3 个工作日内闭环 / P3 周报汇总',
  `responded_at` TIMESTAMP(3) COMMENT '实际响应时间',
  `resolved_at` TIMESTAMP(3) COMMENT '实际闭环时间',
  `sla_met` BOOLEAN COMMENT '是否达成 SLA——闭环度量「异常处理 SLA 达成率」的分子',
  `reingested_at` TIMESTAMP(3) COMMENT '⑤ 复验通过重入湖的时刻——闭环度量「修复重入湖成功率」的依据',
  `discard_reason` STRING COMMENT 'C 弃置归档原因：无法修复，标记原因后归档保留审计',
  `gate_version` STRING COMMENT '拦下这条数据的门禁版本——规则按表灰度发布后，要能回答「这条异常是哪版门禁拦的」',
  `history` STRING COMMENT '处理轨迹（JSON 数组）：五步闭环每次状态推进追加一条，可追责',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`issue_id`, `dt`) NOT ENFORCED
) PARTITIONED BY (`dt`)
WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_dataset_data_list  [数据资产域 / ODS]  数据集数据清单（数据集包含哪些 clip）
-- 来源系统: 数据管理平台MySQL
-- 备注: 复合主键表达完整粒度：一条记录 = 某数据集某版本收录了某个 clip
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_dataset_data_list` (
  `dataset_id` STRING NOT NULL COMMENT '数据集 ID',
  `dataset_version` STRING NOT NULL COMMENT '数据集版本号',
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点',
  `artifact_id` STRING COMMENT '二级 ID：入集所用的处理产物（标注/质检产物）',
  `split_type` STRING COMMENT '数据划分：train/val/test',
  `add_type` STRING COMMENT '加入方式：manual/rule/mining',
  `add_reason` STRING COMMENT '加入原因（定向补采单号/挖掘任务号等）',
  `source_channel` STRING COMMENT '来源渠道：collect/trigger/mining/simulation',
  `scene_tag_id` STRING COMMENT '主场景标签 ID',
  `sample_weight` DOUBLE COMMENT '采样权重',
  `is_valid` BOOLEAN COMMENT '是否有效（false=已移出）',
  `operator` STRING COMMENT '操作人',
  `add_time` TIMESTAMP(3) COMMENT '加入时间',
  `remove_time` TIMESTAMP(3) COMMENT '移出时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`dataset_id`, `dataset_version`, `data_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_dataset_info  [数据资产域 / ODS]  数据集基础信息
-- 来源系统: 数据管理平台MySQL
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_dataset_info` (
  `dataset_id` STRING NOT NULL COMMENT '数据集 ID，跨域公共键',
  `dataset_name` STRING COMMENT '数据集名称',
  `dataset_type` STRING COMMENT '数据集类型：train/eval/test/regression',
  `project_code` STRING COMMENT '所属项目编码',
  `business_domain` STRING COMMENT '业务域：城区NOA/高速NOA/AVP',
  `task_type` STRING COMMENT '任务类型：detection/segmentation/prediction',
  `owner` STRING COMMENT '负责人',
  `owner_dept` STRING COMMENT '负责部门',
  `description` STRING COMMENT '数据集描述与用途',
  `latest_version` STRING COMMENT '当前最新版本号',
  `total_data_count` BIGINT COMMENT '累计数据量（clip 数）',
  `quality_score` DOUBLE COMMENT '质量评分（0-5，资产目录直接引用）',
  `dataset_status` STRING COMMENT '状态：draft/published/archived',
  `is_core_asset` BOOLEAN COMMENT '是否核心资产',
  `create_time` TIMESTAMP(3) COMMENT '创建时间',
  `publish_time` TIMESTAMP(3) COMMENT '首次发布时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`dataset_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_dataset_version  [数据资产域 / ODS]  数据集版本
-- 来源系统: 数据管理平台MySQL
-- 备注: 复合主键表达完整粒度：同一数据集可以有多个版本（原文点名的示例）
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_dataset_version` (
  `dataset_id` STRING NOT NULL COMMENT '数据集 ID',
  `dataset_version` STRING NOT NULL COMMENT '数据集版本号，跨域公共键',
  `version_name` STRING COMMENT '版本别名，如「城区NOA主数据集 v12」',
  `parent_version` STRING COMMENT '父版本号（增量版本溯源）',
  `version_status` STRING COMMENT '版本状态：draft/released/deprecated',
  `change_type` STRING COMMENT '变更方式：full/incremental',
  `change_note` STRING COMMENT '变更说明',
  `data_count` BIGINT COMMENT '数据量（clip 数）',
  `image_count` BIGINT COMMENT '图片数量',
  `annotation_count` BIGINT COMMENT '标注框数量',
  `scene_tag_count` INT COMMENT '覆盖场景标签数',
  `storage_path` STRING COMMENT '对象存储路径前缀',
  `storage_size_bytes` BIGINT COMMENT '版本占用存储',
  `creator` STRING COMMENT '创建人',
  `create_time` TIMESTAMP(3) COMMENT '创建时间',
  `release_time` TIMESTAMP(3) COMMENT '发布时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`dataset_id`, `dataset_version`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_scene_tag  [数据资产域 / ODS]  场景标签字典（场景库的标签定义）
-- 来源系统: 场景标签系统
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_scene_tag` (
  `scene_tag_id` STRING NOT NULL COMMENT '场景标签 ID',
  `tag_code` STRING COMMENT '标签编码，如 CONSTRUCTION_ZONE',
  `tag_name` STRING COMMENT '标签名称，如「施工区域」',
  `scene_type` STRING COMMENT '场景类型：道路/天气/光照/交通参与者/驾驶行为',
  `parent_tag_id` STRING COMMENT '父标签 ID（标签树）',
  `tag_level` INT COMMENT '标签层级（1=一级分类）',
  `tag_source` STRING COMMENT '标签来源三分类：collect/rule/model',
  `tag_definition` STRING COMMENT '标签判定口径与定义',
  `target_count` BIGINT COMMENT '达标线（该场景目标数据量）',
  `priority` STRING COMMENT '补采优先级：P0/P1/P2',
  `is_hard_case_related` BOOLEAN COMMENT '是否难例相关场景',
  `tag_status` STRING COMMENT '标签状态：enabled/disabled',
  `owner` STRING COMMENT '标签负责人',
  `create_time` TIMESTAMP(3) COMMENT '创建时间',
  `effective_from` TIMESTAMP(3) COMMENT '生效时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`scene_tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_model_version  [训练域 / ODS]  模型版本
-- 来源系统: 模型管理平台
-- 备注: 表名不含数据域段（源文即如此）。主键取全局唯一的 model_version，让评测域/部署域按跨域公共键 model_version 做一次主键查询即可拿到模型身份
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_model_version` (
  `model_version` STRING NOT NULL COMMENT '模型版本号（全局唯一版本码），跨域公共键',
  `model_name` STRING COMMENT '模型名称',
  `model_type` STRING COMMENT '模型类型：perception/prediction/planning',
  `model_arch` STRING COMMENT '网络结构：BEVFormer/PointPillars 等',
  `base_model_version` STRING COMMENT '父版本（迭代来源），用于版本树回溯',
  `training_task_id` STRING COMMENT '产出该版本的训练任务 ID',
  `dataset_id` STRING COMMENT '训练数据集 ID',
  `dataset_version` STRING COMMENT '训练数据集版本',
  `framework` STRING COMMENT '框架：pytorch/tensorflow',
  `quantization_type` STRING COMMENT '量化方式：fp32/fp16/int8',
  `model_file_path` STRING COMMENT '模型文件对象存储 key',
  `model_size_mb` DOUBLE COMMENT '模型文件大小（MB）',
  `model_md5` STRING COMMENT '模型文件校验和',
  `release_status` STRING COMMENT '版本状态：draft/released/deprecated',
  `eval_pass_flag` BOOLEAN COMMENT '是否通过评测准入',
  `publish_time` TIMESTAMP(3) COMMENT '发布时间',
  `owner` STRING COMMENT '版本负责人',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`model_version`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_training_metric  [训练域 / ODS]  训练指标（loss / mAP 等逐 step 上报）
-- 来源系统: 训练平台 MySQL
-- 备注: 复合主键表达完整粒度：一个训练任务 × 一个指标 × 一个 step 一条记录
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_training_metric` (
  `training_task_id` STRING NOT NULL COMMENT '所属训练任务 ID',
  `metric_name` STRING NOT NULL COMMENT '指标名称：loss/mAP/precision/recall/miss_rate',
  `step_no` BIGINT NOT NULL COMMENT '全局训练步数',
  `epoch_no` INT COMMENT '训练轮次',
  `metric_type` STRING COMMENT '指标口径：train/val/test',
  `metric_value` DOUBLE COMMENT '指标值',
  `metric_unit` STRING COMMENT '指标单位（比率/绝对值）',
  `learning_rate` DOUBLE COMMENT '该 step 的学习率',
  `gpu_util_pct` DOUBLE COMMENT 'GPU 利用率（%）',
  `gpu_mem_used_mb` DOUBLE COMMENT '显存占用（MB）',
  `throughput_sample_per_sec` DOUBLE COMMENT '吞吐（样本/秒）',
  `is_best` BOOLEAN COMMENT '是否当前最优检查点',
  `log_time` TIMESTAMP(3) COMMENT '指标上报时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`training_task_id`, `metric_name`, `step_no`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_training_task  [训练域 / ODS]  训练任务
-- 来源系统: 训练平台 MySQL
-- 备注: 业务主键优先：直接用训练平台的任务 ID，不引入自增代理键
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_training_task` (
  `training_task_id` STRING NOT NULL COMMENT '训练任务 ID（训练平台主键）',
  `task_name` STRING COMMENT '任务名称',
  `project_code` STRING COMMENT '所属项目',
  `dataset_id` STRING COMMENT '训练数据集 ID',
  `dataset_version` STRING COMMENT '训练数据集版本',
  `base_model_version` STRING COMMENT '基线模型版本（增量训练的起点）',
  `model_version` STRING COMMENT '产出模型版本',
  `model_type` STRING COMMENT '模型类型：perception/prediction/planning',
  `train_framework` STRING COMMENT '训练框架：pytorch/tensorflow',
  `gpu_type` STRING COMMENT 'GPU 型号',
  `gpu_card_num` INT COMMENT 'GPU 卡数',
  `epoch_num` INT COMMENT '训练轮数',
  `batch_size` INT COMMENT '批大小',
  `learning_rate` DOUBLE COMMENT '初始学习率',
  `hyper_param_json` STRING COMMENT '超参快照（JSON，原样落地）',
  `task_status` STRING COMMENT '任务状态：pending/running/success/failed/killed',
  `submit_time` TIMESTAMP(3) COMMENT '提交时间',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `submitter` STRING COMMENT '提交人',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`training_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_badcase_record  [评测域 / ODS]  Badcase 记录
-- 来源系统: 评测平台 MySQL
-- 备注: 表名域段为 badcase_（源文实战示例用法），语义上归评测域，见 naming.DOMAIN_ALIASES
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_badcase_record` (
  `badcase_id` STRING NOT NULL COMMENT 'Badcase 记录 ID',
  `evaluation_task_id` STRING COMMENT '来源评测任务 ID',
  `data_id` STRING COMMENT '关联的原始采集 clip',
  `artifact_id` STRING COMMENT '产出该 Badcase 的评测产物 ID',
  `badcase_type` STRING COMMENT '类型：miss_detection/false_alarm/track_break/planning_error',
  `severity` STRING COMMENT '严重等级：P0/P1/P2',
  `root_cause_category` STRING COMMENT '根因大类：感知漏检/定位漂移/规控决策/标注错误',
  `root_cause_sub_category` STRING COMMENT '根因子类：夜间行人/逆光车辆/施工区域',
  `scene_tag` STRING COMMENT '场景标签',
  `model_version` STRING COMMENT '被测模型版本',
  `frame_timestamp` TIMESTAMP(3) COMMENT '问题发生时刻（clip 内）',
  `description` STRING COMMENT '问题描述',
  `handle_status` STRING COMMENT '处理状态：open/analyzing/fixed/closed',
  `owner` STRING COMMENT '责任人',
  `issue_id` STRING COMMENT '关联问题单 ID（分析域）',
  `create_time` TIMESTAMP(3) COMMENT '创建时间',
  `close_time` TIMESTAMP(3) COMMENT '关闭时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`badcase_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_evaluation_result  [评测域 / ODS]  评测结果
-- 来源系统: 评测平台 MySQL
-- 备注: 复合主键表达完整粒度：一个评测任务下多条用例结果；ODS 层原样入湖，不做指标口径统一
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_evaluation_result` (
  `evaluation_task_id` STRING NOT NULL COMMENT '所属评测任务 ID',
  `result_id` STRING NOT NULL COMMENT '评测结果记录 ID',
  `case_id` STRING COMMENT '评测用例 ID',
  `data_id` STRING COMMENT '被评测 clip 的 data_id',
  `evaluation_type` STRING COMMENT '评测类型：offline/simulation/real_vehicle',
  `model_version` STRING COMMENT '被测模型版本',
  `metric_name` STRING COMMENT '指标名称：precision/recall/miss_rate/mAP',
  `metric_value` DOUBLE COMMENT '指标值',
  `score` DOUBLE COMMENT '综合得分',
  `pass_flag` BOOLEAN COMMENT '是否通过',
  `gt_object_count` INT COMMENT '真值目标数',
  `pred_object_count` INT COMMENT '预测目标数',
  `miss_count` INT COMMENT '漏检数',
  `false_alarm_count` INT COMMENT '误检数',
  `scene_tag` STRING COMMENT '场景标签',
  `result_detail_json` STRING COMMENT '结果明细（JSON，源系统原样）',
  `evaluate_time` TIMESTAMP(3) COMMENT '评测产出时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`evaluation_task_id`, `result_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_evaluation_task  [评测域 / ODS]  评测任务
-- 来源系统: 评测平台 MySQL
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_evaluation_task` (
  `evaluation_task_id` STRING NOT NULL COMMENT '评测任务 ID',
  `task_name` STRING COMMENT '评测任务名称',
  `project_code` STRING COMMENT '所属项目',
  `evaluation_type` STRING COMMENT '评测类型：offline/simulation/real_vehicle',
  `model_version` STRING COMMENT '被测模型版本',
  `baseline_model_version` STRING COMMENT '基线对比模型版本',
  `dataset_id` STRING COMMENT '评测数据集 ID',
  `dataset_version` STRING COMMENT '评测数据集版本',
  `metric_config_json` STRING COMMENT '评测指标配置（JSON）',
  `total_case_count` INT COMMENT '待评测用例总数',
  `task_status` STRING COMMENT '任务状态：pending/running/success/failed',
  `priority` STRING COMMENT '优先级：P0/P1/P2',
  `submit_user` STRING COMMENT '提交人',
  `submit_time` TIMESTAMP(3) COMMENT '提交时间',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`evaluation_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_simulation_result  [仿真域 / ODS]  仿真运行结果（一个场景一次执行一条）
-- 来源系统: 仿真平台 MySQL
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区。主键用源系统自己的 simulation_run_id（ODS 不改造、不丢失、可追溯，湖仓三级 run_id 的映射留到 DWD 层做）；metric_json 原样入湖，拆解同样留给 DWD
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_simulation_result` (
  `simulation_run_id` STRING NOT NULL COMMENT '仿真运行 ID（仿真平台主键）',
  `simulation_task_id` STRING COMMENT '仿真任务 ID：一次回归批次下发 N 个场景',
  `scenario_id` STRING COMMENT '被执行的仿真场景 ID',
  `project_code` STRING COMMENT '所属项目',
  `model_version` STRING COMMENT '被测模型版本',
  `sim_engine` STRING COMMENT '仿真引擎：carla/lgsvl/自研闭环仿真',
  `sim_mode` STRING COMMENT '仿真模式：open_loop 开环回灌 / closed_loop 闭环',
  `run_status` STRING COMMENT '运行状态：success/failed/timeout',
  `pass_flag` STRING COMMENT '准出判定：pass/fail',
  `collision_flag` BOOLEAN COMMENT '是否发生碰撞',
  `takeover_count` INT COMMENT '虚拟接管次数',
  `min_ttc_sec` DOUBLE COMMENT '最小碰撞时间 TTC（秒）',
  `max_lateral_deviation_m` DOUBLE COMMENT '最大横向偏差（米）',
  `score` DOUBLE COMMENT '综合评分',
  `start_time` TIMESTAMP(3) COMMENT '仿真开始时间',
  `end_time` TIMESTAMP(3) COMMENT '仿真结束时间',
  `duration_sec` DOUBLE COMMENT '运行耗时（秒）',
  `log_object_key` STRING COMMENT '仿真日志 / 回放包对象存储 key',
  `metric_json` STRING COMMENT '平台原始指标（JSON，ODS 层不拆解）',
  `executor` STRING COMMENT '提交人',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`simulation_run_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_simulation_scenario  [仿真域 / ODS]  仿真场景库（场景定义与来源）
-- 来源系统: 仿真平台 MySQL
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区。场景库是维表量级（万级），CDC 从仿真平台 MySQL 同步，靠 bucket + 主键 Upsert 管理。scenario_source=real_clip 的场景带 data_id，是「仿真失败 → 回溯原始 clip」的第一跳
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_simulation_scenario` (
  `scenario_id` STRING NOT NULL COMMENT '仿真场景 ID（仿真平台场景库主键）',
  `scenario_name` STRING COMMENT '场景名称',
  `scenario_type` STRING COMMENT '场景类型：cut_in/aeb/加塞/路口左转/施工区绕行',
  `scenario_source` STRING COMMENT '场景来源：real_clip 真实回灌 / manual 人工构造 / mining 挖掘生成',
  `data_id` STRING COMMENT '来源 clip 的 data_id；仅 real_clip 回灌场景有值，关联采集域',
  `project_code` STRING COMMENT '所属项目',
  `map_name` STRING COMMENT '高精地图 / 路网名称',
  `road_type` STRING COMMENT '道路类型：城市/高速/乡道/园区',
  `weather` STRING COMMENT '天气：晴/雨/雪/雾',
  `light_condition` STRING COMMENT '光照条件：白天/夜间/黄昏/隧道',
  `traffic_density` STRING COMMENT '交通流密度：low/medium/high',
  `npc_count` INT COMMENT 'NPC 交通参与者数量',
  `ego_init_speed_kmh` DOUBLE COMMENT '主车初始车速（km/h）',
  `duration_sec` DOUBLE COMMENT '场景时长（秒）',
  `difficulty_level` STRING COMMENT '难度等级：easy/normal/hard/corner',
  `scenario_version` STRING COMMENT '场景版本号（场景本身也会迭代）',
  `scenario_file_key` STRING COMMENT 'OpenSCENARIO / OpenDRIVE 文件对象存储 key',
  `scene_tag_list` STRING COMMENT '场景标签列表（逗号分隔，口径对齐数据资产域 ods_scene_tag）',
  `scenario_status` STRING COMMENT '场景状态：draft/online/offline',
  `create_time` TIMESTAMP(3) COMMENT '场景创建时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`scenario_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_shadow_mode_data  [回传域 / ODS]  影子模式数据（后台捕捉算法与人类驾驶的分歧）
-- 来源系统: 车端回传 Kafka
-- 备注: 影子模式不接管车辆，只在后台并行推理并与人类驾驶行为比对，分歧即长尾场景线索——这是回传数据自带长尾标签的来源。分区规则三：主键 Upsert 且无明确分区维度 → 不分区
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_shadow_mode_data` (
  `shadow_record_id` STRING NOT NULL COMMENT '影子模式记录 ID（一次分歧一条）',
  `data_id` STRING COMMENT '分歧片段对应 clip 的 data_id',
  `vehicle_code` STRING COMMENT '车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `software_version` STRING COMMENT '车端软件版本',
  `model_version` STRING COMMENT '后台影子运行的模型版本（训练域交接标识）',
  `divergence_type` STRING COMMENT '分歧类型：trajectory/speed/lane_change/braking',
  `divergence_score` DOUBLE COMMENT '分歧度打分，算法决策与人类驾驶的偏离程度',
  `algo_action` STRING COMMENT '算法拟执行动作',
  `human_action` STRING COMMENT '人类驾驶实际动作',
  `lateral_offset_m` DOUBLE COMMENT '横向轨迹偏差（米）',
  `speed_diff_kph` DOUBLE COMMENT '纵向速度差（km/h）',
  `occur_time` TIMESTAMP(3) COMMENT '分歧发生时刻',
  `gps_lat` DOUBLE COMMENT '分歧点纬度',
  `gps_lon` DOUBLE COMMENT '分歧点经度',
  `road_type` STRING COMMENT '道路类型',
  `upload_status` STRING COMMENT '回传上云状态',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`shadow_record_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_vehicle_function_test  [回传域 / ODS]  车端功能测试回传记录
-- 来源系统: 车端回传 Kafka
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区，靠 bucket 打散
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_vehicle_function_test` (
  `test_record_id` STRING NOT NULL COMMENT '功能测试记录 ID',
  `data_id` STRING COMMENT '测试片段对应 clip 的 data_id',
  `vehicle_code` STRING COMMENT '测试车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `test_plan_id` STRING COMMENT '测试计划 ID',
  `function_code` STRING COMMENT '被测功能编码：acc/lka/noa/apa/aeb',
  `function_name` STRING COMMENT '被测功能名称',
  `software_version` STRING COMMENT '被测车端软件版本',
  `model_version` STRING COMMENT '被测模型版本（训练域交接标识）',
  `test_scene` STRING COMMENT '测试场景描述',
  `test_result` STRING COMMENT '测试结论：pass/fail/blocked',
  `fail_reason` STRING COMMENT '失败原因',
  `takeover_count` INT COMMENT '测试过程中的接管次数',
  `test_mileage_km` DOUBLE COMMENT '测试里程（公里）',
  `test_start_time` TIMESTAMP(3) COMMENT '测试开始时间',
  `test_end_time` TIMESTAMP(3) COMMENT '测试结束时间',
  `tester` STRING COMMENT '测试员/安全员',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`test_record_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_vehicle_trigger_event  [回传域 / ODS]  车端触发事件（触发器命中即回传）
-- 来源系统: 车端回传 Kafka
-- 备注: 分区规则二：有明确业务分类过滤 → 按业务字段分区，各触发类型数据量差异大；原则三：分区表主键必须包含分区字段，故 PK=(event_id, trigger_type)
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_vehicle_trigger_event` (
  `event_id` STRING NOT NULL COMMENT '触发事件 ID（车端生成，Kafka 消息业务唯一键）',
  `trigger_type` STRING NOT NULL COMMENT '触发类型：takeover/aeb/hard_brake/rule_hit/corner_case（分区字段）',
  `data_id` STRING COMMENT '回传片段对应 clip 的 data_id，车端随文件生成，回传数据的终身锚点',
  `vehicle_code` STRING COMMENT '触发车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `software_version` STRING COMMENT '触发时车端软件版本（部署域交接标识）',
  `trigger_rule_id` STRING COMMENT '命中的云端下发触发规则 ID',
  `trigger_time` TIMESTAMP(3) COMMENT '车端触发时刻',
  `gps_lat` DOUBLE COMMENT '触发点纬度',
  `gps_lon` DOUBLE COMMENT '触发点经度',
  `vehicle_speed_kph` DOUBLE COMMENT '触发时车速（km/h）',
  `road_type` STRING COMMENT '道路类型',
  `weather` STRING COMMENT '天气',
  `light_condition` STRING COMMENT '光照条件',
  `clip_duration_sec` DOUBLE COMMENT '回传片段时长（秒）',
  `upload_status` STRING COMMENT '回传上云状态',
  `kafka_offset` BIGINT COMMENT 'Kafka 消息位点，支持事件回放不丢数',
  `pre_trigger_seconds` DOUBLE COMMENT '回传片段覆盖触发前的秒数。对应 QG-KFK-003 片段截断：覆盖触发前 ≥ N 秒，不足则标记 truncated_flag 告警放行（N 见 thresholds.TRIGGER_PRE_SECONDS_DEFAULT）',
  `post_trigger_seconds` DOUBLE COMMENT '回传片段覆盖触发后的秒数。对应 QG-KFK-004 片段截断：覆盖触发后 ≥ M 秒，不足则标记 truncated_flag 告警放行（M 见 thresholds.TRIGGER_POST_SECONDS_DEFAULT）',
  `duplicate_rate` DOUBLE COMMENT '所属批次的事件重复率（0~1，批级统计量，随记录落表便于复盘）。对应 QG-KFK-006 重复率监控：事件 ID 幂等去重后重复率 > 5% 告警',
  `gps_jump_meters` DOUBLE COMMENT '相对上一采样点的定位跳变距离（米）。对应 QG-KFK-008 定位跳变：六维之准确性「定位无跳变」，超 100 米 P2 告警标记放行。⚠️ 原文未明确，本项目设计：原文只写「定位无跳变」这一句定性要求，既没给度量也没给米数；本项目落成「相对上一采样点的跳变距离」这一标量列',
  `vehicle_manufacture_time` TIMESTAMP(3) COMMENT '车辆出厂时间（随事件冗余落表，避免门禁为一条规则去 join 车辆档案）。QG-KFK-002 时间戳合理性的下界：trigger_time 早于出厂时间即不合理，上界是服务器时间 + 车端时钟漂移容忍窗（thresholds.CLOCK_DRIFT_TOLERANCE_SECONDS）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`event_id`, `trigger_type`) NOT ENFORCED
) PARTITIONED BY (`trigger_type`)
WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_ota_task  [部署域 / ODS]  OTA 升级任务
-- 来源系统: OTA 平台
-- 备注: 一次发布一行；车辆级下发结果在 dwd_ota_deployment_detail 展开
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_ota_task` (
  `ota_task_id` STRING NOT NULL COMMENT 'OTA 任务 ID',
  `task_name` STRING COMMENT '任务名称',
  `project_code` STRING COMMENT '所属项目',
  `software_package_id` STRING COMMENT '软件包 ID',
  `software_version` STRING COMMENT '目标软件版本号',
  `model_version` STRING COMMENT '随包下发的模型版本，回连训练/评测域',
  `package_size_bytes` BIGINT COMMENT '软件包大小（字节）',
  `release_channel` STRING COMMENT '发布通道：internal/grey/full',
  `rollout_strategy` STRING COMMENT '灰度策略描述（批次/比例）',
  `target_vehicle_count` INT COMMENT '目标车辆数',
  `success_vehicle_count` INT COMMENT '升级成功车辆数',
  `fail_vehicle_count` INT COMMENT '升级失败车辆数',
  `task_status` STRING COMMENT '任务状态：draft/publishing/running/finished/aborted',
  `publish_time` TIMESTAMP(3) COMMENT '发布时间',
  `start_time` TIMESTAMP(3) COMMENT '开始下发时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `operator` STRING COMMENT '发布负责人',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`ota_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_vehicle_software_version  [部署域 / ODS]  车辆软件版本台账
-- 来源系统: 车辆管理平台
-- 备注: 复合主键表达完整粒度：一辆车多个软件模块（域控/MCU/感知/规控）各有版本
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_vehicle_software_version` (
  `vehicle_code` STRING NOT NULL COMMENT '车辆编码',
  `software_module` STRING NOT NULL COMMENT '软件模块：perception/planning/control/soc_os/mcu_fw',
  `software_version` STRING COMMENT '当前版本号',
  `previous_version` STRING COMMENT '升级前版本号',
  `model_version` STRING COMMENT '该模块内置的模型版本',
  `ota_task_id` STRING COMMENT '最近一次生效的 OTA 任务 ID',
  `ecu_name` STRING COMMENT '所属 ECU 名称',
  `hardware_version` STRING COMMENT '配套硬件版本',
  `install_status` STRING COMMENT '安装状态：installed/installing/failed/rollback',
  `install_time` TIMESTAMP(3) COMMENT '安装完成时间',
  `project_code` STRING COMMENT '所属项目',
  `fleet_name` STRING COMMENT '所属车队',
  `is_latest` BOOLEAN COMMENT '是否该模块全网最新版本',
  `last_report_time` TIMESTAMP(3) COMMENT '车端最近一次版本上报时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`vehicle_code`, `software_module`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_issue_record  [分析域 / ODS]  问题单记录（原样同步，不做根因归一）
-- 来源系统: 问题管理平台MySQL
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区。问题单是长生命周期实体（创建后状态反复更新），按 issue_id Upsert；源系统枚举值不统一（如 severity 混用 P0/高），归一化留给 DWD
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_issue_record` (
  `issue_id` STRING NOT NULL COMMENT '问题单 ID，源系统业务主键',
  `issue_title` STRING COMMENT '问题标题',
  `issue_type` STRING COMMENT '问题类型：perception/prediction/planning/control/data_quality',
  `issue_source` STRING COMMENT '问题来源：evaluation_badcase/road_test/shadow_mode/customer_feedback',
  `project_code` STRING COMMENT '所属项目',
  `severity` STRING COMMENT '严重等级（源系统原值，未归一）：P0/P1/P2/P3',
  `priority` STRING COMMENT '处理优先级',
  `issue_status` STRING COMMENT '问题状态：open/analyzing/fixing/verifying/closed/rejected',
  `root_cause_category` STRING COMMENT '根因分类（源系统人工填写）',
  `root_cause_desc` STRING COMMENT '根因描述',
  `badcase_id` STRING COMMENT '关联 Badcase ID（来源为评测时非空）',
  `data_id` STRING COMMENT '关联 clip 的 data_id，回溯原始采集片段',
  `vehicle_code` STRING COMMENT '复现车辆编码',
  `model_version` STRING COMMENT '问题暴露时的模型版本',
  `owner_user` STRING COMMENT '责任人工号',
  `reporter_user` STRING COMMENT '提单人工号',
  `create_time` TIMESTAMP(3) COMMENT '问题创建时间',
  `resolve_time` TIMESTAMP(3) COMMENT '问题解决时间',
  `close_time` TIMESTAMP(3) COMMENT '问题关闭时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`issue_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_mining_rule_config  [挖掘域 / ODS]  挖掘规则配置（「规则即数据」：经 Flink CDC 实时同步入湖，可查可追溯可审计）
-- 来源系统: 数据挖掘平台 MySQL（规则表经 Flink CDC 实时同步）
-- 备注: 规则不是散落在代码里的 if-else，而是与业务数据同等的湖仓资产——谁在什么时候改了什么规则一查便知。本表是 mining 引擎的读入口（mining.tables.RULE_CONFIG_COLUMNS 直接拼进 SELECT 列表），故 [S3-04] 一「全生命周期：创建/修改/禁用」的三个时刻齐备：create_time / last_modify_time / disable_time，配合 create_user / last_modify_user / owner 三个人。近义异名归一见模块 docstring：rule_type→rule_category、execution_mode→exec_mode、expression_mode→express_mode、sql_condition→rule_sql、visual_config_json→rule_condition_json、scene_label→target_tag_id
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_mining_rule_config` (
  `rule_id` STRING NOT NULL COMMENT '规则 ID',
  `rule_name` STRING COMMENT '规则名称',
  `rule_category` STRING COMMENT '规则种类（[S3-04] 二的六大种类，取值以 mining.rules.RuleType 为准）：tag_combination 标签组合/spatiotemporal 时空地理/vehicle_signal 车辆信号/model_output 模型输出/event_trigger 事件触发/composite 多条件复合',
  `rule_version` STRING COMMENT '规则版本（变更全程留痕）',
  `rule_priority` INT COMMENT '规则优先级：不只排序——直接决定 Embedding 与存储分级，高优先级命中数据优先进向量化队列',
  `express_mode` STRING COMMENT '表达方式：sql 工程师写 SQL/visual 业务同学拖配置，两者等价',
  `rule_sql` STRING COMMENT 'SQL 条件表达式（复杂规则可挂自定义 UDF）',
  `rule_condition_json` STRING COMMENT '可视化配置条件（JSON：标签/GPS 围栏/时间/传感器信号/模型输出）',
  `exec_mode` STRING COMMENT '执行模式（取值以 mining.rules.ExecutionMode 为准）：batch_t_plus_1（静态标签与时空条件走 T+1 Spark SQL）/near_realtime（车辆信号与事件流走 Flink 准实时）',
  `schedule_cron` STRING COMMENT '批模式调度表达式',
  `event_window_before_sec` INT COMMENT '事件窗口前置秒数（默认 15 秒，还原事件如何发生）',
  `event_window_after_sec` INT COMMENT '事件窗口后置秒数（默认 5 秒，确认事件后果）',
  `target_tag_id` STRING COMMENT '命中后统一经标签服务打标的目标 tag_id（已过字典归一）——mining 引擎的 scene_label 就写在这一列，缺口评估也按它对齐 dwd_scene_gap_detail.tag_id',
  `target_clip_count` BIGINT COMMENT '本规则对应场景的目标 clip 数（ODD 覆盖目标）。[S3-01] 一「挖掘双出口」的分母：库内命中够数就零采集成本回补，不够才按缺口下发定向采集需求',
  `project_code` STRING COMMENT '适用项目',
  `rule_status` STRING COMMENT '规则状态（取值以 mining.rules.RuleStatus 为准）：draft/enabled/disabled/archived',
  `create_user` STRING COMMENT '创建人',
  `last_modify_user` STRING COMMENT '最近修改人',
  `owner` STRING COMMENT '规则责任人：对规则效果与停用决策负责，与 create_user（谁建的）/last_modify_user（谁最后改的）是三个不同职责。⚠️ 原文未明确，本项目设计——规则即数据就得有 data owner',
  `create_time` TIMESTAMP(3) COMMENT '创建时间（生命周期三时刻之一：创建）',
  `last_modify_time` TIMESTAMP(3) COMMENT '最近修改时间（生命周期三时刻之二：修改）。与既有 last_modify_user 配对——原本只记了「谁改的」没记「什么时候改的」，规则变更留痕不完整。依据 [S3-04] 一「规则的创建、修改、禁用全生命周期都有记录」',
  `disable_time` TIMESTAMP(3) COMMENT '禁用时刻（生命周期三时刻之三：禁用）。rule_status=disabled 时必填，让「这条规则是什么时候停的、停之前命中了多少」可追溯。依据 [S3-04] 一',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`rule_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

-- ods_mining_task  [挖掘域 / ODS]  挖掘任务（规则挖掘/抽帧/推理/向量化的统一任务记录）
-- 来源系统: 数据挖掘平台 MySQL
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ods_mining_task` (
  `mining_task_id` STRING NOT NULL COMMENT '挖掘任务 ID',
  `task_name` STRING COMMENT '任务名称',
  `task_type` STRING COMMENT '任务类型：rule_mining/frame_extract/vlm_infer/embedding/vector_search',
  `rule_id` STRING COMMENT '关联规则 ID（规则挖掘类任务）',
  `project_code` STRING COMMENT '所属项目',
  `exec_mode` STRING COMMENT '执行模式：batch（T+1 Spark SQL）/streaming（Flink 准实时）',
  `scan_start_time` TIMESTAMP(3) COMMENT '扫描范围起（增量水位下界）',
  `scan_end_time` TIMESTAMP(3) COMMENT '扫描范围止（增量水位上界）',
  `scan_scope_json` STRING COMMENT '扫描范围附加条件（JSON：项目/车辆/时段）',
  `task_status` STRING COMMENT '任务状态：pending/running/success/failed/canceled',
  `hit_count` BIGINT COMMENT '命中数量',
  `tag_write_count` BIGINT COMMENT '写入标签量',
  `submit_user` STRING COMMENT '提交人',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `_source_system` STRING NOT NULL COMMENT '来源系统标识',
  PRIMARY KEY (`mining_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);
