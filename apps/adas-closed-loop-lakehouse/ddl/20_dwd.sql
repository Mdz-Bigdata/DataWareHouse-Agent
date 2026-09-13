-- ========================================================================
-- 湖仓建表 · DWD 层
-- 由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：python3 scripts/export_ddl.py
-- 层级定位：明细数据层：以 data_id 串联闭环链路，跨源 JOIN、清洗、标准化，血缘与追溯的核心层
-- 系统字段：_ingest_time + update_time
-- 本层共 30 张表：
--   · 采集域: 1 张
--   · 生产域: 5 张
--   · 数据资产域: 3 张
--   · 训练域: 2 张
--   · 评测域: 3 张
--   · 仿真域: 1 张
--   · 回传域: 2 张
--   · 部署域: 2 张
--   · 分析域: 1 张
--   · 挖掘域: 8 张
--   · 闭环域: 2 张
-- 物理策略（分区 / bucket / changelog-producer）由 catalog/spec.py 硬校验后渲染。
-- ========================================================================

-- dwd_collect_clip_detail  [采集域 / DWD]  采集数据单元元信息（clip 级，血缘起点）
-- 备注: 血缘起点。一个 clip ≈ 1 分钟连续采集片段，对应一个 data_id，终身不变
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_collect_clip_detail` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点',
  `artifact_id` STRING COMMENT '二级 ID：本 clip 的处理产物 ID，血缘链路的被引用端——下游表的 parent_artifact_id 指向它，QG-COM-007 据此做图库对账兜底',
  `collect_task_id` STRING COMMENT '采集任务 ID',
  `vehicle_code` STRING COMMENT '车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `collect_start_time` TIMESTAMP(3) COMMENT '采集开始时间',
  `collect_end_time` TIMESTAMP(3) COMMENT '采集结束时间',
  `duration_sec` DOUBLE COMMENT '片段时长（秒）',
  `gps_start_lat` DOUBLE COMMENT '起点纬度',
  `gps_start_lon` DOUBLE COMMENT '起点经度',
  `road_type` STRING COMMENT '道路类型',
  `weather` STRING COMMENT '天气',
  `light_condition` STRING COMMENT '光照条件',
  `sensor_count` INT COMMENT '传感器数量',
  `total_size_bytes` BIGINT COMMENT '原始数据总量',
  `upload_status` STRING COMMENT '上云状态',
  `compliance_status` STRING COMMENT '合规状态：见 ingest.compliance',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dwd_data_production_chain  [生产域 / DWD]  数据生产主链路表（采集→上云→标注→质检→交付 14 环节状态归集）
-- 备注: 全湖写入量最大的表，高并发 Upsert → bucket 16；Upsert 表且无明确分区维度 → 不分区。[a12] 第四章 Bucket 五档表点名本表作 16 档「超大表 / 高并发写入」的代表表；第六章 DDL 实战也以本表逐条对照五章规则（PK(data_id) / 不分区 / bucket 16 / changelog-producer=lookup / _ingest_time + update_time）；PK(data_id) 同时是主键原则一「业务主键优先」的原文示例。所有效率分析、瓶颈定位、血缘追溯的起点。表名不含数据域段（源文原名），域归属以本注册表为准
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_data_production_chain` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点，主链路主键',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '采集车辆编码',
  `batch_id` STRING COMMENT '所属交付批次',
  `collect_time` TIMESTAMP(3) COMMENT '环节 1 采集完成时间',
  `upload_time` TIMESTAMP(3) COMMENT '环节 2 上云完成时间',
  `tagging_time` TIMESTAMP(3) COMMENT '环节 3 打标签完成时间',
  `preprocess_time` TIMESTAMP(3) COMMENT '环节 4 前处理完成时间',
  `annotation_time` TIMESTAMP(3) COMMENT '环节 5 标注完成时间',
  `qc_time` TIMESTAMP(3) COMMENT '环节 6 质检完成时间',
  `postprocess_time` TIMESTAMP(3) COMMENT '环节 7 后处理完成时间',
  `deliver_time` TIMESTAMP(3) COMMENT '环节 8 交付完成时间',
  `current_stage` STRING COMMENT '当前所处环节编码',
  `finished_stage_count` INT COMMENT '已完成环节数（全链路共 14 环节）',
  `chain_status` STRING COMMENT '链路状态：processing/delivered/blocked/invalid',
  `blocked_hour` DOUBLE COMMENT '在当前环节的停留时长（小时），>48 视为积压',
  `bottleneck_stage` STRING COMMENT '本条数据耗时最长的环节',
  `total_duration_hour` DOUBLE COMMENT '采集→交付总耗时（小时）',
  `latest_artifact_id` STRING COMMENT '最新交付产物 artifact_id',
  `dataset_id` STRING COMMENT '交付进入的数据集 ID',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dwd_manual_operation_detail  [生产域 / DWD]  人工操作明细（人工干预次数是效率分析的关键分母）
-- 备注: 表名不含数据域段（源文原名），域归属以本注册表声明为准
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_manual_operation_detail` (
  `operation_id` STRING NOT NULL COMMENT '操作流水 ID',
  `data_id` STRING COMMENT '关联数据单元 data_id',
  `artifact_id` STRING COMMENT '关联产物 artifact_id',
  `run_id` STRING COMMENT '关联处理运行 run_id',
  `line_task_id` STRING COMMENT '关联产线任务 ID',
  `project_code` STRING COMMENT '所属项目',
  `stage_code` STRING COMMENT '被干预的产线环节编码',
  `operator` STRING COMMENT '操作人账号',
  `operator_role` STRING COMMENT '操作人角色',
  `operation_type` STRING COMMENT '操作类型：rerun/invalidate/repriority/manual_fix/force_pass',
  `target_type` STRING COMMENT '操作对象类型：task/data/artifact/run',
  `target_id` STRING COMMENT '操作对象 ID',
  `operation_time` TIMESTAMP(3) COMMENT '操作时间',
  `before_status` STRING COMMENT '操作前状态',
  `after_status` STRING COMMENT '操作后状态',
  `reason` STRING COMMENT '操作原因',
  `is_override_gate` BOOLEAN COMMENT '是否强制跳过质量门禁',
  `impact_data_count` INT COMMENT '影响的数据单元数',
  `cost_minute` DOUBLE COMMENT '人工耗时（分钟）',
  `platform` STRING COMMENT '来源平台',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`operation_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_production_artifact_detail  [生产域 / DWD]  处理产物元信息（血缘核心）
-- 备注: 血缘核心表，并发读写最高 → bucket 16。「处理版本化」在此落地：重刷不覆盖，旧产物标 superseded 并指向新产物，新旧并存可对比可回滚。parent_artifact_id 冗余落表，作为 Neo4j 图库 DERIVED_FROM 边的对账兜底
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_production_artifact_detail` (
  `artifact_id` STRING NOT NULL COMMENT '二级 ID：处理产物，内嵌 data_id',
  `data_id` STRING COMMENT '一级 ID：所属采集单元，Badcase 回溯的落点',
  `parent_artifact_id` STRING COMMENT '血缘父产物 ID（冗余落表，图库对账兜底）',
  `run_id` STRING COMMENT '三级 ID：产出该产物的那一次运行',
  `stage` STRING COMMENT '产物所属环节：align/slam/preprocess/annotate/qc/package',
  `artifact_type` STRING COMMENT '产物类型：align/slam/annotation/qc_report/package',
  `algo_version` STRING COMMENT '算法版本，与 artifact_id 中的段一致',
  `content_hash` STRING COMMENT '内容哈希，重试幂等的来源',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `superseded_by_artifact_id` STRING COMMENT '被哪个新产物替代（重刷版本链）',
  `param_snapshot_json` STRING COMMENT '参数快照（JSON），可重放依据；大属性不进图库，血缘按 ID 回湖仓取',
  `storage_uri` STRING COMMENT '产物对象存储路径（大文件外置，湖仓只存元信息）',
  `file_count` INT COMMENT '产物文件数',
  `file_size_bytes` BIGINT COMMENT '产物总大小（字节）',
  `checksum_md5` STRING COMMENT '产物校验和',
  `quality_score` DOUBLE COMMENT '产物质量评分',
  `produce_time` TIMESTAMP(3) COMMENT '产出时间',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '来源车辆编码',
  `dataset_id` STRING COMMENT '被引用的数据集 ID（冗余，便于影响分析）',
  `reference_count` INT COMMENT '血缘引用数，删除三重确认之一（>0 不可删）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`artifact_id`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dwd_production_execution_detail  [生产域 / DWD]  产线执行明细（data_id × 环节 × 运行）
-- 备注: 原则二：复合主键表达完整粒度——同一 clip 的同一环节因重跑会有多次执行。大体量明细表 → bucket 8；[a12] 第四章 Bucket 五档表点名本表作 8 档「大体量明细表」的代表表
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_production_execution_detail` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：数据单元锚点',
  `stage_code` STRING NOT NULL COMMENT '产线环节编码',
  `run_id` STRING NOT NULL COMMENT '三级 ID：本次执行所属的处理运行',
  `line_task_id` STRING COMMENT '关联产线任务 ID',
  `project_code` STRING COMMENT '所属项目',
  `stage_name` STRING COMMENT '环节名称',
  `stage_order` INT COMMENT '环节序号（1~14）',
  `exec_status` STRING COMMENT '执行状态：success/failed/running/skipped',
  `start_time` TIMESTAMP(3) COMMENT '执行开始时间',
  `end_time` TIMESTAMP(3) COMMENT '执行结束时间',
  `duration_sec` DOUBLE COMMENT '执行耗时（秒）',
  `queue_wait_sec` DOUBLE COMMENT '排队等待时长（秒）',
  `input_artifact_id` STRING COMMENT '输入产物 artifact_id',
  `output_artifact_id` STRING COMMENT '输出产物 artifact_id',
  `algo_version` STRING COMMENT '算法版本',
  `retry_count` INT COMMENT '本环节重试次数',
  `executor` STRING COMMENT '执行引擎：argo/flink/manual',
  `resource_core_hour` DOUBLE COMMENT '资源消耗（核·时）',
  `error_code` STRING COMMENT '失败错误码',
  `error_message` STRING COMMENT '失败信息',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `stage_code`, `run_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_production_run_detail  [生产域 / DWD]  处理运行记录（含重跑信息）
-- 备注: 绑定算法版本与参数快照，保证可重放；重跑不覆盖，通过 rerun_of_run_id 串成版本链
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_production_run_detail` (
  `run_id` STRING NOT NULL COMMENT '三级 ID：产线的一次执行',
  `stage` STRING COMMENT '运行环节，与 run_id 中的段一致',
  `line_task_id` STRING COMMENT '关联产线任务 ID',
  `workflow_uid` STRING COMMENT 'Argo 工作流 UID',
  `project_code` STRING COMMENT '所属项目',
  `batch_id` STRING COMMENT '处理批次 ID',
  `data_id` STRING COMMENT '关联数据单元 data_id（批量运行取代表 clip，逐条见执行明细）',
  `algo_version` STRING COMMENT '算法版本',
  `image_version` STRING COMMENT '容器镜像版本',
  `param_snapshot_json` STRING COMMENT '参数快照（JSON），可重放的前提',
  `run_status` STRING COMMENT '运行状态：running/success/failed/canceled',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `duration_sec` DOUBLE COMMENT '运行时长（秒）',
  `input_data_count` INT COMMENT '输入数据单元数',
  `output_artifact_count` INT COMMENT '产出产物数',
  `input_artifact_ids` STRING COMMENT '输入产物 ID 列表（冗余落表，图库 INPUT 边的对账源）',
  `output_artifact_ids` STRING COMMENT '产出产物 ID 列表（冗余落表，图库 PRODUCED 边的对账源）',
  `success_count` INT COMMENT '成功数据单元数',
  `failed_count` INT COMMENT '失败数据单元数',
  `is_rerun` BOOLEAN COMMENT '是否为重跑',
  `rerun_of_run_id` STRING COMMENT '重跑来源的 run_id',
  `rerun_reason` STRING COMMENT '重跑原因：算法升级/数据修复/资源失败',
  `resource_core_hour` DOUBLE COMMENT '资源消耗（核·时）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`run_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_dataset_data_relation  [数据资产域 / DWD]  数据集-数据关联（data_id 级成员关系）
-- 备注: 大体量明细：数据集版本 × clip 多对多展开。挂 data_id + artifact_id，让「某模型训练用过哪些原始 clip」成为主键查询
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_dataset_data_relation` (
  `dataset_id` STRING NOT NULL COMMENT '数据集 ID',
  `dataset_version` STRING NOT NULL COMMENT '数据集版本号',
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点',
  `artifact_id` STRING COMMENT '二级 ID：入集所用的处理产物',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `run_id` STRING COMMENT '三级 ID：产出该产物的处理运行',
  `project_code` STRING COMMENT '所属项目编码',
  `vehicle_code` STRING COMMENT '采集车辆编码',
  `split_type` STRING COMMENT '数据划分：train/val/test',
  `source_channel` STRING COMMENT '来源渠道：collect/trigger/mining/simulation',
  `add_type` STRING COMMENT '加入方式：manual/rule/mining',
  `primary_scene_tag_id` STRING COMMENT '主场景标签 ID',
  `scene_tag_count` INT COMMENT '该 clip 命中的场景标签数',
  `is_hard_case` BOOLEAN COMMENT '是否难例（难例库回流）',
  `sample_weight` DOUBLE COMMENT '采样权重',
  `relation_status` STRING COMMENT '成员状态：active/removed',
  `add_time` TIMESTAMP(3) COMMENT '加入时间',
  `remove_time` TIMESTAMP(3) COMMENT '移出时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`dataset_id`, `dataset_version`, `data_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_dataset_version_detail  [数据资产域 / DWD]  数据集版本明细
-- 备注: 主键原则二的原文点名示例：PK=(dataset_id, version)，同一数据集可有多个版本。dataset_version 是跨域公共键的同名冗余列，取值恒等于 version——原文 PK 用 version，而训练/评测域统一用 dataset_version 关联，冗余一列避免下游记两套列名（与 parent_artifact_id 冗余落表同理）。dataset_version_id 是血缘图库 DatasetVersion 节点的单列 ID（形如 DS_0001_V2），artifact_refs 是 REFERENCES 边的对账源
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_dataset_version_detail` (
  `dataset_id` STRING NOT NULL COMMENT '数据集 ID',
  `version` STRING NOT NULL COMMENT '版本号（原文点名的复合主键第二段）',
  `dataset_version` STRING COMMENT '跨域公共键冗余列，恒等于 version',
  `dataset_version_id` STRING COMMENT '版本单列 ID（{dataset_id}_{version}，如 DS_0001_V2），血缘图库 DatasetVersion 节点 ID',
  `artifact_refs` STRING COMMENT '锁定引用的产物 ID 列表（冗余落表，图库 REFERENCES 边的对账源，保证可复现）',
  `dataset_name` STRING COMMENT '数据集名称',
  `dataset_type` STRING COMMENT '数据集类型：train/eval/test/regression',
  `project_code` STRING COMMENT '所属项目编码',
  `task_type` STRING COMMENT '任务类型：detection/segmentation/prediction',
  `parent_version` STRING COMMENT '父版本号（增量版本溯源）',
  `version_status` STRING COMMENT '版本状态：draft/released/deprecated',
  `data_count` BIGINT COMMENT '数据量（clip 数）',
  `image_count` BIGINT COMMENT '图片数量',
  `annotation_count` BIGINT COMMENT '标注框数量',
  `scene_tag_count` INT COMMENT '覆盖场景标签数',
  `badcase_data_count` BIGINT COMMENT '其中来自 Badcase 回流的数据量',
  `mining_data_count` BIGINT COMMENT '其中来自挖掘平台的数据量',
  `storage_path` STRING COMMENT '对象存储路径前缀',
  `storage_size_bytes` BIGINT COMMENT '版本占用存储',
  `model_version` STRING COMMENT '首个消费该版本的模型版本',
  `quality_score` DOUBLE COMMENT '质量评分（0-5）',
  `release_time` TIMESTAMP(3) COMMENT '发布时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`dataset_id`, `version`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_scene_tag_relation  [数据资产域 / DWD]  数据-场景标签关联（clip 级打标结果）
-- 备注: 复合主键表达完整粒度：一个 clip 命中多个场景标签。三来源（collect/rule/model）在此合流，模型打标的结果带 artifact_id 与 run_id 可追溯
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_scene_tag_relation` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点',
  `scene_tag_id` STRING NOT NULL COMMENT '场景标签 ID',
  `tag_code` STRING COMMENT '标签编码',
  `tag_name` STRING COMMENT '标签名称',
  `scene_type` STRING COMMENT '场景类型：道路/天气/光照/交通参与者/驾驶行为',
  `tag_source` STRING COMMENT '标签来源三分类：collect/rule/model',
  `confidence` DOUBLE COMMENT '打标置信度（model 来源有效）',
  `artifact_id` STRING COMMENT '二级 ID：打标产物（模型推理产物）',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `run_id` STRING COMMENT '三级 ID：打标运行 ID',
  `model_version` STRING COMMENT '打标模型版本（tag_source=model）',
  `project_code` STRING COMMENT '所属项目编码',
  `vehicle_code` STRING COMMENT '采集车辆编码',
  `is_primary` BOOLEAN COMMENT '是否该 clip 的主场景标签',
  `verify_status` STRING COMMENT '人工校验状态：unverified/confirmed/rejected',
  `verifier` STRING COMMENT '校验人',
  `tag_time` TIMESTAMP(3) COMMENT '打标时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `scene_tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_training_metric_detail  [训练域 / DWD]  训练指标明细
-- 备注: 大体量明细：任务数 × 指标数 × step 数，故 bucket=8。复合主键表达完整粒度；已冗余 model_version，便于按模型版本直接拉收敛曲线
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_training_metric_detail` (
  `training_task_id` STRING NOT NULL COMMENT '训练任务 ID',
  `metric_name` STRING NOT NULL COMMENT '指标名称：loss/mAP/precision/recall/miss_rate',
  `step_no` BIGINT NOT NULL COMMENT '全局训练步数',
  `epoch_no` INT COMMENT '训练轮次',
  `project_code` STRING COMMENT '所属项目',
  `model_version` STRING COMMENT '该任务产出的模型版本（冗余，免 JOIN）',
  `dataset_id` STRING COMMENT '训练数据集 ID',
  `metric_type` STRING COMMENT '指标口径：train/val/test',
  `metric_value` DOUBLE COMMENT '指标值',
  `best_value` DOUBLE COMMENT '截至当前 step 的历史最优值',
  `is_best` BOOLEAN COMMENT '当前 step 是否刷新最优',
  `learning_rate` DOUBLE COMMENT '该 step 的学习率',
  `gpu_util_pct` DOUBLE COMMENT 'GPU 利用率（%）',
  `throughput_sample_per_sec` DOUBLE COMMENT '吞吐（样本/秒）',
  `converged_flag` BOOLEAN COMMENT '是否已判定收敛',
  `train_elapsed_min` DOUBLE COMMENT '距训练开始的已耗时（分钟）',
  `log_time` TIMESTAMP(3) COMMENT '指标上报时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`training_task_id`, `metric_name`, `step_no`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_training_task_detail  [训练域 / DWD]  训练任务明细
-- 备注: 训练是数据集级作业，无 clip 级 data_id；回溯 clip 走 dataset_id + dataset_version → dwd_dataset_data_relation → data_id。run_id 记录本次训练运行（stage=train），重跑产生新 run_id 但 training_task_id 不变。[a12] 第四章 Bucket 五档表点名本表作 4 档「中等体量 ODS/DWD」的代表表（与 ods_collect_task 并列）；PK(training_task_id) 是主键原则一「业务主键优先」的原文示例
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_training_task_detail` (
  `training_task_id` STRING NOT NULL COMMENT '训练任务 ID',
  `run_id` STRING COMMENT '三级 ID：本次训练运行（run_train_yyyyMMddHHmmss_seq）',
  `project_code` STRING COMMENT '所属项目',
  `task_name` STRING COMMENT '任务名称',
  `dataset_id` STRING COMMENT '训练数据集 ID（下钻到 data_id 的入口）',
  `dataset_version` STRING COMMENT '训练数据集版本',
  `sample_count` BIGINT COMMENT '训练样本量（clip 数）',
  `base_model_version` STRING COMMENT '基线模型版本',
  `model_version` STRING COMMENT '产出模型版本',
  `model_type` STRING COMMENT '模型类型：perception/prediction/planning',
  `gpu_type` STRING COMMENT 'GPU 型号',
  `gpu_card_num` INT COMMENT 'GPU 卡数',
  `epoch_num` INT COMMENT '训练轮数',
  `queue_wait_min` DOUBLE COMMENT '排队等待时长（分钟）',
  `train_duration_min` DOUBLE COMMENT '训练耗时（分钟），闭环耗时的训练环节',
  `gpu_hours` DOUBLE COMMENT 'GPU 卡时消耗',
  `task_status` STRING COMMENT '任务状态：success/failed/killed',
  `fail_reason` STRING COMMENT '失败原因（标准化后）',
  `retry_count` INT COMMENT '重试次数',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`training_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_badcase_detail  [评测域 / DWD]  Badcase 明细
-- 备注: 挂 data_id：从 Badcase 回溯到原始采集 clip 是一次主键查询，不需要跨表 JOIN
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_badcase_detail` (
  `badcase_id` STRING NOT NULL COMMENT 'Badcase ID',
  `data_id` STRING COMMENT '一级 ID：回溯到原始采集 clip',
  `artifact_id` STRING COMMENT '二级 ID：产出该 Badcase 的评测产物',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `traced_artifact_ids` STRING COMMENT '反向追溯到的产物 ID 列表（冗余落表，图库 TRACED_TO 边的对账源）',
  `run_id` STRING COMMENT '三级 ID：产出该 Badcase 的评测运行',
  `evaluation_task_id` STRING COMMENT '来源评测任务 ID',
  `evaluation_type` STRING COMMENT '评测类型：offline/simulation/real_vehicle',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '采集车辆编码（由 data_id 关联采集域补齐）',
  `model_version` STRING COMMENT '被测模型版本',
  `badcase_type` STRING COMMENT '类型：miss_detection/false_alarm/track_break/planning_error',
  `severity` STRING COMMENT '严重等级：P0/P1/P2',
  `root_cause_category` STRING COMMENT '根因大类：感知漏检/定位漂移/规控决策/标注错误',
  `root_cause_sub_category` STRING COMMENT '根因子类：夜间行人/逆光车辆/施工区域',
  `scene_tag` STRING COMMENT '场景标签',
  `hard_case_flag` BOOLEAN COMMENT '是否入选难例库',
  `dataset_id` STRING COMMENT '回流训练的数据集 ID',
  `handle_status` STRING COMMENT '处理状态：open/analyzing/fixed/closed',
  `occur_time` TIMESTAMP(3) COMMENT '问题发生时刻',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`badcase_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_evaluation_result_detail  [评测域 / DWD]  评测结果明细（clip 级）
-- 备注: 分区规则二：有明确业务分类过滤 → 按业务字段分区；离线/仿真/实车三类评测体量与查询模式差异大。原则三：分区表主键必须含分区字段，故 PK 带 evaluation_type
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_evaluation_result_detail` (
  `evaluation_task_id` STRING NOT NULL COMMENT '评测任务 ID',
  `data_id` STRING NOT NULL COMMENT '一级 ID：被评测 clip 的终身锚点',
  `evaluation_type` STRING NOT NULL COMMENT '评测类型：offline/simulation/real_vehicle（分区字段）',
  `artifact_id` STRING COMMENT '二级 ID：被评测的处理产物',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `run_id` STRING COMMENT '三级 ID：本次评测运行 ID',
  `project_code` STRING COMMENT '所属项目',
  `model_version` STRING COMMENT '被测模型版本',
  `baseline_model_version` STRING COMMENT '基线对比模型版本',
  `dataset_id` STRING COMMENT '评测数据集 ID',
  `dataset_version` STRING COMMENT '评测数据集版本',
  `scene_tag` STRING COMMENT '场景标签',
  `pass_flag` BOOLEAN COMMENT '该 clip 是否通过评测',
  `score` DOUBLE COMMENT '综合得分',
  `miss_count` INT COMMENT '漏检数',
  `false_alarm_count` INT COMMENT '误检数',
  `badcase_flag` BOOLEAN COMMENT '是否产出 Badcase',
  `evaluate_time` TIMESTAMP(3) COMMENT '评测产出时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`evaluation_task_id`, `data_id`, `evaluation_type`) NOT ENFORCED
) PARTITIONED BY (`evaluation_type`)
WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_evaluation_task_detail  [评测域 / DWD]  评测任务明细
-- 备注: 任务级粒度，挂不上 data_id；clip 级关联在 dwd_evaluation_result_detail
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_evaluation_task_detail` (
  `evaluation_task_id` STRING NOT NULL COMMENT '评测任务 ID',
  `task_name` STRING COMMENT '评测任务名称',
  `project_code` STRING COMMENT '所属项目',
  `evaluation_type` STRING COMMENT '评测类型：offline/simulation/real_vehicle',
  `model_version` STRING COMMENT '被测模型版本',
  `baseline_model_version` STRING COMMENT '基线对比模型版本',
  `dataset_id` STRING COMMENT '评测数据集 ID',
  `dataset_version` STRING COMMENT '评测数据集版本',
  `run_id` STRING COMMENT '三级 ID：本次评测运行 ID',
  `total_case_count` INT COMMENT '评测用例总数',
  `pass_case_count` INT COMMENT '通过用例数',
  `fail_case_count` INT COMMENT '未通过用例数',
  `pass_rate` DOUBLE COMMENT '通过率',
  `badcase_count` INT COMMENT '产出 Badcase 数',
  `avg_metric_score` DOUBLE COMMENT '平均指标得分',
  `task_status` STRING COMMENT '任务状态：pending/running/success/failed',
  `duration_sec` DOUBLE COMMENT '评测耗时（秒）',
  `start_time` TIMESTAMP(3) COMMENT '开始时间',
  `end_time` TIMESTAMP(3) COMMENT '结束时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`evaluation_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_simulation_result_detail  [仿真域 / DWD]  仿真结果明细（run 级，串联 data_id / model_version）
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区。bucket=8：模型版本回归一次跑上万场景，属大体量 DWD 明细。粒度 = 一次仿真运行（一个场景一次执行），主键用三级 ID run_id；data_id 作为关联键冗余落表，让「仿真失败 → 原始采集 clip」保持一次主键查询。重刷（同场景换算法版本重跑）不覆盖：生成新 artifact_id，旧行标 superseded
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_simulation_result_detail` (
  `run_id` STRING NOT NULL COMMENT '三级 ID：一次仿真运行，run_{stage}_{yyyyMMddHHmmss}_{seq}',
  `data_id` STRING COMMENT '一级 ID：回灌场景对应的原始 clip，Badcase 回溯的关联键',
  `artifact_id` STRING COMMENT '二级 ID：本次仿真产出的结果产物',
  `parent_artifact_id` STRING COMMENT '血缘父产物（被测模型 / 回灌输入产物），图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `scenario_id` STRING COMMENT '仿真场景 ID（关联 ods_simulation_scenario）',
  `simulation_run_id` STRING COMMENT '源系统运行 ID，冗余保留用于与仿真平台对账',
  `project_code` STRING COMMENT '所属项目',
  `model_version` STRING COMMENT '被测模型版本（关联训练域）',
  `dataset_id` STRING COMMENT '回归所用数据集 ID',
  `dataset_version` STRING COMMENT '回归所用数据集版本',
  `sim_mode` STRING COMMENT '仿真模式：open_loop 开环回灌 / closed_loop 闭环',
  `sim_start_time` TIMESTAMP(3) COMMENT '仿真开始时间',
  `sim_end_time` TIMESTAMP(3) COMMENT '仿真结束时间',
  `duration_sec` DOUBLE COMMENT '仿真耗时（秒），闭环效率指标输入',
  `pass_flag` STRING COMMENT '准出判定：pass/fail',
  `failure_reason` STRING COMMENT '失败原因分类：collision/timeout/lane_departure/takeover',
  `collision_flag` BOOLEAN COMMENT '是否发生碰撞',
  `min_ttc_sec` DOUBLE COMMENT '最小碰撞时间 TTC（秒）',
  `score` DOUBLE COMMENT '综合评分',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`run_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_shadow_mode_detail  [回传域 / DWD]  影子模式数据明细（算法-人类分歧，长尾场景线索）
-- 备注: 原则二：复合主键表达完整粒度——同一段回传 clip 内可能捕捉到多次分歧，单靠 data_id 无法唯一标识。data_id 置于主键首位，保证按 clip 前缀回溯仍走主键。bucket=8：影子模式后台全量运行，体量大于触发器回传
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_shadow_mode_detail` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：分歧片段所属 clip 的终身锚点',
  `shadow_record_id` STRING NOT NULL COMMENT '影子模式记录 ID，同一 clip 内多次分歧靠它区分',
  `vehicle_code` STRING COMMENT '车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `software_version` STRING COMMENT '车端软件版本（部署域交接标识）',
  `model_version` STRING COMMENT '影子模式运行的模型版本（训练域交接标识）',
  `divergence_type` STRING COMMENT '分歧类型：trajectory/speed/lane_change/braking',
  `divergence_score` DOUBLE COMMENT '分歧度打分（标准化到 0~1）',
  `algo_action` STRING COMMENT '算法拟执行动作',
  `human_action` STRING COMMENT '人类驾驶实际动作',
  `lateral_offset_m` DOUBLE COMMENT '横向轨迹偏差（米）',
  `speed_diff_kph` DOUBLE COMMENT '纵向速度差（km/h）',
  `occur_time` TIMESTAMP(3) COMMENT '分歧发生时刻',
  `city_code` STRING COMMENT '行政区编码，热力图聚合维度',
  `road_type` STRING COMMENT '道路类型',
  `scene_tag` STRING COMMENT '清洗打标后的场景标签',
  `is_long_tail` BOOLEAN COMMENT '是否长尾场景：影子模式的核心产出',
  `is_hard_case` BOOLEAN COMMENT '是否已沉淀为难例',
  `dataset_id` STRING COMMENT '已回补进的数据集 ID',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `shadow_record_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_vehicle_trigger_detail  [回传域 / DWD]  车端触发事件明细（清洗打标后，难例库上游）
-- 备注: 一次触发对应一段回传 clip，data_id 与 event_id 一一对应；以 data_id 为主键，让「从 Badcase / 难例回溯到原始回传 clip」成为一次主键查询。bucket=8：量产回传随车队规模持续放量，属大体量明细表。不分区（规则三）
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_vehicle_trigger_detail` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：回传 clip 级终身锚点',
  `event_id` STRING COMMENT '源触发事件 ID（ods_vehicle_trigger_event）',
  `trigger_type` STRING COMMENT '触发类型（标准化后）',
  `trigger_rule_id` STRING COMMENT '命中的触发规则 ID',
  `trigger_rule_name` STRING COMMENT '触发规则名称',
  `vehicle_code` STRING COMMENT '触发车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `software_version` STRING COMMENT '触发时车端软件版本（部署域交接标识）',
  `model_version` STRING COMMENT '触发时车端模型版本（训练域交接标识）',
  `trigger_time` TIMESTAMP(3) COMMENT '车端触发时刻',
  `gps_lat` DOUBLE COMMENT '触发点纬度',
  `gps_lon` DOUBLE COMMENT '触发点经度',
  `city_code` STRING COMMENT '行政区编码，热力图聚合维度',
  `road_type` STRING COMMENT '道路类型',
  `scene_tag` STRING COMMENT '清洗打标后的场景标签，回补训练集的依据',
  `severity_level` STRING COMMENT '严重程度：P0/P1/P2/P3',
  `is_hard_case` BOOLEAN COMMENT '是否已沉淀为难例（→ ads_hard_case_library）',
  `dataset_id` STRING COMMENT '已回补进的数据集 ID',
  `closed_loop_latency_min` BIGINT COMMENT '闭环耗时：触发 → 进训练集（分钟）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_ota_deployment_detail  [部署域 / DWD]  OTA 部署明细（任务 × 车辆）
-- 备注: 复合主键表达完整粒度：一个 OTA 任务下发到多台车，每台车一行结果。本表不挂 data_id——部署粒度是车辆不是 clip；跨域追溯经 model_version 起跳
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_ota_deployment_detail` (
  `ota_task_id` STRING NOT NULL COMMENT 'OTA 任务 ID',
  `vehicle_code` STRING NOT NULL COMMENT '目标车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `model_version` STRING COMMENT '下发的模型版本，回连训练域/评测域的公共键',
  `software_version` STRING COMMENT '目标软件版本',
  `source_software_version` STRING COMMENT '升级前软件版本',
  `release_channel` STRING COMMENT '发布通道：internal/grey/full',
  `deploy_batch` STRING COMMENT '灰度批次标识',
  `vehicle_model` STRING COMMENT '车型',
  `push_time` TIMESTAMP(3) COMMENT '推送到车时间',
  `download_start_time` TIMESTAMP(3) COMMENT '开始下载时间',
  `download_end_time` TIMESTAMP(3) COMMENT '下载完成时间',
  `download_duration_sec` DOUBLE COMMENT '下载耗时（秒）',
  `install_time` TIMESTAMP(3) COMMENT '安装完成时间',
  `install_duration_sec` DOUBLE COMMENT '安装耗时（秒）',
  `deploy_status` STRING COMMENT '部署状态：pending/downloading/installing/success/failed/rollback',
  `fail_reason` STRING COMMENT '失败原因',
  `retry_count` INT COMMENT '重试次数',
  `rollback_flag` BOOLEAN COMMENT '是否发生回滚',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`ota_task_id`, `vehicle_code`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_vehicle_software_distribution_detail  [部署域 / DWD]  车端软件版本分布（车辆 × 模块当前生效版本）
-- 备注: 当前态快照，主键 Upsert 覆盖更新（分区规则三：无明确分区维度 → 不分区）；版本变更历史由 dwd_ota_deployment_detail 承载，本表只回答「现在全网跑的是哪个版本」
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_vehicle_software_distribution_detail` (
  `vehicle_code` STRING NOT NULL COMMENT '车辆编码',
  `software_module` STRING NOT NULL COMMENT '软件模块：perception/planning/control/soc_os/mcu_fw',
  `software_version` STRING COMMENT '当前生效版本号',
  `previous_software_version` STRING COMMENT '上一生效版本号',
  `model_version` STRING COMMENT '该版本内置的模型版本，回连训练域/评测域',
  `ota_task_id` STRING COMMENT '使该版本生效的 OTA 任务 ID',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_model` STRING COMMENT '车型',
  `fleet_name` STRING COMMENT '所属车队',
  `autonomy_level` STRING COMMENT '智驾等级',
  `release_channel` STRING COMMENT '该车所在发布通道：internal/grey/full',
  `version_effective_time` TIMESTAMP(3) COMMENT '版本生效时间',
  `version_age_days` INT COMMENT '版本在役天数',
  `is_latest_version` BOOLEAN COMMENT '是否该模块全网最新版本',
  `version_lag_count` INT COMMENT '落后最新版本的版本数',
  `online_status` STRING COMMENT '车辆在线状态：online/offline/maintenance',
  `last_report_time` TIMESTAMP(3) COMMENT '车端最近一次版本上报时间',
  `stat_date` DATE COMMENT '快照日期',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`vehicle_code`, `software_module`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_issue_detail  [分析域 / DWD]  问题明细（根因归一 + 挂 data_id 回溯链路）
-- 备注: 主键取业务主键 issue_id 而非 data_id：一个 clip 可暴露多个问题，data_id / artifact_id 作为关联键冗余落表，使「从问题回溯到原始 clip」成为一次主键查询。问题单总量远小于 clip 明细，bucket 取中等档 4
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_issue_detail` (
  `issue_id` STRING NOT NULL COMMENT '问题单 ID',
  `data_id` STRING COMMENT '一级 ID：关联 clip 级终身锚点，回溯血缘起点',
  `artifact_id` STRING COMMENT '二级 ID：问题定位到的处理产物',
  `badcase_id` STRING COMMENT '关联 Badcase ID',
  `evaluation_type` STRING COMMENT '暴露该问题的评测类型',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '复现车辆编码',
  `model_version` STRING COMMENT '问题暴露时的模型版本',
  `issue_type` STRING COMMENT '问题类型（已归一）：perception/prediction/planning/control/data_quality',
  `issue_source` STRING COMMENT '问题来源（已归一）：evaluation_badcase/road_test/shadow_mode/customer_feedback',
  `severity_level` STRING COMMENT '严重等级（已归一）：P0/P1/P2/P3',
  `issue_status` STRING COMMENT '问题状态：open/analyzing/fixing/verifying/closed/rejected',
  `root_cause_category` STRING COMMENT '根因大类（已归一，供 ADS 根因分布聚合）',
  `fix_solution` STRING COMMENT '修复方案：模型迭代/数据补采/规则调整/标注返工',
  `fix_model_version` STRING COMMENT '修复后验证通过的模型版本',
  `owner_user` STRING COMMENT '责任人工号',
  `create_time` TIMESTAMP(3) COMMENT '问题创建时间',
  `close_time` TIMESTAMP(3) COMMENT '问题关闭时间',
  `resolve_duration_hours` DOUBLE COMMENT '解决耗时（小时），DWS Badcase 解决率口径输入',
  `reopen_count` INT COMMENT '重开次数，衡量修复质量',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`issue_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_data_tag_detail  [挖掘域 / DWD]  数据级标签明细（clip 级，三来源标签管道的出口之一）
-- 备注: 复合主键表达完整粒度「一个 clip × 一个标准标签 × 一个来源」，幂等去重靠它——重复写入无副作用，任务重跑不产生重复标签；标签变更只 Upsert 变更行，配合时间旅行可回溯任意时点。【bucket 裁决：4 → 8】本表行数 ≈ clip 数 × 人均标签数 × 来源数，是不折不扣的大体量明细表，对应 Bucket 五档的第四档「大体量明细表（DWD）」；原本的 4 是第三档「中等体量 ODS/DWD」，明显低配——同域按 clip 粒度、且只有一行一 clip 的 dwd_mining_result_detail 就已经是 8，本表在它之上再乘标签数与来源数，不可能更小。故采信子系统的 8。本次并入产物血缘（artifact_id/parent_artifact_id/artifact_status）与字典映射留痕（source_raw_tag/mapping_type/mapping_note/conflict_resolution）——三来源收口的全部判断依据。近义异名归一见模块 docstring：tag_time→first_tag_time、valid_flag→tag_status
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_data_tag_detail` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点',
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID（已过字典映射与别名归一）',
  `tag_source` STRING NOT NULL COMMENT '标签来源：collect 采集/rule 规则/vlm 模型',
  `tag_name` STRING COMMENT '标准标签名（冗余自字典，检索与导出免回查字典表）',
  `tag_category` STRING COMMENT '标签类别（冗余自字典，免 JOIN 过滤）',
  `tag_level` STRING COMMENT '适用粒度（冗余自字典）：clip/image/inheritable——继承作业据此决定要不要下发到图片级',
  `tag_value` STRING COMMENT '标签取值（枚举型标签的具体值，如 rain_level=heavy）',
  `confidence` DOUBLE COMMENT '置信度（模型标签必填，人工/规则标签为 1.0）',
  `rule_id` STRING COMMENT '血缘：产出该标签的规则 ID',
  `rule_version` STRING COMMENT '血缘：规则版本',
  `model_name` STRING COMMENT '血缘：VLM 模型名',
  `model_version` STRING COMMENT '血缘：模型版本',
  `infer_job_id` STRING COMMENT '血缘：推理作业 ID',
  `run_id` STRING COMMENT '三级 ID：写入本条标签的运行 ID',
  `artifact_id` STRING COMMENT '二级 ID：本条标签作为产物的 ID（{data_id}_tag_{algo_version}_{hash}）',
  `parent_artifact_id` STRING COMMENT '血缘父产物（被打标的 clip 产物），冗余落表供图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `source_raw_tag` STRING COMMENT '来源系统的原始写法，字典归一前的原样留痕（如采集端写的「下雨天」）。没有它，别名归一就是一次不可逆的信息丢失，出错时无从对账',
  `mapping_type` STRING COMMENT '字典映射结果：canonical 直接命中标准名/alias 经别名归一/merged_redirect 经 merged 态重定向。三种路径的可信度不同，审核抽检按它分层',
  `mapping_note` STRING COMMENT '映射判定说明（命中了哪条别名、走了哪次合并），排障用',
  `conflict_resolution` STRING COMMENT '互斥冲突裁决留痕，格式 kind:role:rule:reason。同层互斥组内两个标签同时命中时，落败方置 tag_status=invalid 但不删除——裁决理由记在本列，可复核可翻案',
  `review_status` STRING COMMENT '审核状态：unreviewed(pending)/approved/rejected/corrected——未审核标签不得进入训练集圈选',
  `review_operator` STRING COMMENT '审核人',
  `review_time` TIMESTAMP(3) COMMENT '审核时间',
  `tag_status` STRING COMMENT '标签事实状态：active/invalid（互斥裁决落败置 invalid，即 tags 侧的 valid_flag=false）',
  `vehicle_code` STRING COMMENT '车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `first_tag_time` TIMESTAMP(3) COMMENT '首次打标时间（最近一次打标由系统字段 update_time 承载）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `tag_id`, `tag_source`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_image_frame_detail  [挖掘域 / DWD]  抽帧图片明细（三级分层抽帧的唯一产物表：常规普查/事件勘查/推理取证三路统一落表）
-- 备注: 亿级行 + 批（Spark 回刷存量）流（Flink 实时抽帧）双模写入，并发最高，取 bucket 16。image_id 内嵌 data_id，免查表即可回溯采集单元；data_id 冗余落列供主键式回溯。本次并入 sampling 子系统的三类能力列：多路同步组（frame_group_id）、事件窗口三列（event_time/event_window_start_time/event_window_end_time，让事件抽帧的 20 秒窗口可复盘）、推理选帧的三维打分明细（object_richness_score/temporal_position_score/keyframe_score/is_keyframe，原文四章要求选帧「可重算复盘」，只留总分就复盘不了）。★ 近义异名归一见模块 docstring，其中 event_trigger_type→trigger_event_type 是字序颠倒的同义列，取值域与 ods_vehicle_trigger_event.trigger_type 同口径；另有 sampling_tier→extract_level、file_path→image_object_key、file_size_bytes→image_size_bytes、clip_offset_ms→frame_offset_sec（毫秒÷1000）、desensitization_status→desensitize_status
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_image_frame_detail` (
  `image_id` STRING NOT NULL COMMENT '图片级 ID（{data_id}_{camera_id}_F{帧序号:06d}），内嵌 data_id，免查表即可回溯采集单元',
  `data_id` STRING COMMENT '一级 ID：所属 clip 的终身锚点',
  `artifact_id` STRING COMMENT '二级 ID：本帧作为抽帧产物的 ID',
  `parent_artifact_id` STRING COMMENT '血缘父产物（原始视频文件产物），图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid（重刷不覆盖，旧产物标 superseded）',
  `run_id` STRING COMMENT '三级 ID：产出本帧的抽帧运行 ID',
  `extract_level` STRING COMMENT '抽帧层级 = 产出本帧的成本闸门：routine 常规（2 秒 1 帧）/event 事件（1 秒 1 帧）/inference 推理选帧',
  `sampling_interval_sec` DOUBLE COMMENT '产出本帧时该层实际用的抽帧间隔（秒）：常规默认 2 秒 1 帧、事件 1 秒 1 帧（原文二章闸门表）。间隔是可调参数，只记层级不记实际间隔，后面就算不出「这一批帧密度为什么不一样」',
  `camera_id` STRING COMMENT '摄像头 ID（多路同步：同一时刻多路图片为一组样本，逐图保留视角）',
  `camera_position` STRING COMMENT '摄像头安装位置：front/left/right/rear',
  `frame_group_id` STRING COMMENT '多路同步组 ID：同一采集时刻的多路图片共用一个组 ID，作为一组样本进训练。原文五章「四个实现要点」之多路摄像头同步——缺它则多路图片只能靠时间戳容差事后拼组',
  `frame_index` INT COMMENT '帧序号（clip 内递增，原生 30fps 下的帧号，字典序即时间序）',
  `frame_timestamp` TIMESTAMP(3) COMMENT '帧绝对时间戳',
  `frame_offset_sec` DOUBLE COMMENT '相对 clip 起点的时间偏移（秒；sampling 侧 clip_offset_ms 毫秒值 ÷ 1000 写入）',
  `frame_quality_score` DOUBLE COMMENT '图像清晰度分：模糊/过曝/遮挡直接降权，推理抽帧选 1~5 关键帧的依据之一，可重算复盘',
  `object_richness_score` DOUBLE COMMENT '目标丰富度分：画面里车辆/行人/交通设施越多分越高。原文四章选帧三维之二',
  `temporal_position_score` DOUBLE COMMENT '时间位置分：事件窗口中心、场景切换时刻优先。原文四章选帧三维之三',
  `keyframe_score` DOUBLE COMMENT '三维加权综合分 = 清晰度 × 丰富度 × 时间位置的加权和，推理抽帧的选帧依据。三个分项与总分都落表，换权重后可直接重算重新圈选，不必重跑推理',
  `is_keyframe` BOOLEAN COMMENT '是否被推理抽帧选中为关键帧（每 clip 1~5 张）。推理抽帧不产新图、只在已落湖的帧里挑，所以选中与否是本列，而不是另一个 extract_level 取值',
  `trigger_event_type` STRING COMMENT '触发事件类型（事件抽帧）：rule_hit/aeb(active_safety)/takeover(driver_takeover)/low_confidence，与 ods_vehicle_trigger_event.trigger_type 同口径',
  `event_time` TIMESTAMP(3) COMMENT '事件发生时刻（事件窗口中心），事件抽帧专用',
  `event_window_start_time` TIMESTAMP(3) COMMENT '事件窗口起点：事件前 15 秒（与 dwd_mining_result_detail 同名同义）',
  `event_window_end_time` TIMESTAMP(3) COMMENT '事件窗口终点：事件后 5 秒',
  `gps_lat` DOUBLE COMMENT '抽帧时刻纬度',
  `gps_lon` DOUBLE COMMENT '抽帧时刻经度',
  `image_object_key` STRING COMMENT '图片对象存储 key（即 sampling 侧的 file_path）',
  `image_size_bytes` BIGINT COMMENT '图片大小（字节）',
  `image_width` INT COMMENT '图像宽度（像素）——分辨率随车型与摄像头代际变化，训练侧要按它做尺寸对齐',
  `image_height` INT COMMENT '图像高度（像素）',
  `desensitize_status` STRING COMMENT '双脱敏校验状态：passed/pending/rejected——未脱敏一律拒绝抽帧',
  `algo_version` STRING COMMENT '抽帧/打分算法版本。算法一变即产出新 artifact_id（旧帧标 superseded），没有这一列就无法解释「同一个 clip 为什么两次抽出的帧不一样」',
  `create_time` TIMESTAMP(3) COMMENT '记录创建时间——向量化流水线第 ① 步增量识别的水位字段之一（另一个是 update_time）',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '车辆编码（检索与配额统计的常用过滤维度，与 clip 表同名同义）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`image_id`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_image_tag_detail  [挖掘域 / DWD]  图片级标签明细（含 VLM caption 特殊标签，三来源标签管道的出口之二）
-- 备注: 复合主键表达完整粒度「一张图 × 一个标准标签 × 一个来源」，与 clip 级表同一套幂等去重口径。caption 以 tag_category=CAPTION 的特殊标签写入本表，并冗余一份到向量表——结构化过滤与语义检索共用同一份说明，不用两套维护。【bucket 裁决：8 → 16】行数 ≈ 图片数（= clip 数 × 每 clip 抽帧数）× 标签数，且 clip 标签会自动继承到它抽出的每一张图片，写入放大全域最严重，对应 Bucket 五档第五档「超大表 / 高并发写入（DWD）」。同域两张图片粒度的表（dwd_mining_image_frame_detail、dwd_mining_image_vector_detail）都已取 16，本表在帧表之上再乘标签数，只可能更大不可能更小，原本的 8 属低配。故采信子系统的 16。本次并入内容与 clip 级表对齐（产物血缘 + 字典映射留痕 + 冗余维度），并补齐 project_code/vehicle_code/rule_version——clip 级表有而本表没有，同一套查询在图片级就得多一次 JOIN。近义异名归一见模块 docstring：tag_time→first_tag_time、valid_flag→tag_status、inherited_from_clip→inherited_from_data_tag
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_image_tag_detail` (
  `image_id` STRING NOT NULL COMMENT '图片级 ID',
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID（已过字典映射与别名归一）',
  `tag_source` STRING NOT NULL COMMENT '标签来源：collect 采集/rule 规则/vlm 模型',
  `data_id` STRING COMMENT '一级 ID：所属 clip，让「Badcase 图片 → 原始 clip」是一次主键查询',
  `tag_name` STRING COMMENT '标准标签名（冗余自字典，免回查）',
  `tag_category` STRING COMMENT '标签类别（五大类别 + CAPTION 特殊类）',
  `tag_level` STRING COMMENT '适用粒度（冗余自字典）：clip/image/inheritable',
  `caption_text` STRING COMMENT 'VLM 生成的关键说明（tag_category=CAPTION 时写入）',
  `confidence` DOUBLE COMMENT '置信度',
  `inherited_from_data_tag` BOOLEAN COMMENT '是否由 clip 标签自动继承而来（字典 tag_level=inheritable）',
  `bbox_json` STRING COMMENT '目标框（JSON）：目标级标签的画面位置',
  `rule_id` STRING COMMENT '血缘：产出该标签的规则 ID',
  `rule_version` STRING COMMENT '血缘：规则版本——规则标签跟着规则版本走，与 clip 级表同口径',
  `model_name` STRING COMMENT '血缘：VLM 模型名',
  `model_version` STRING COMMENT '血缘：模型版本',
  `infer_job_id` STRING COMMENT '血缘：推理作业 ID',
  `run_id` STRING COMMENT '三级 ID：写入本条标签的运行 ID',
  `artifact_id` STRING COMMENT '二级 ID：本条标签作为产物的 ID',
  `parent_artifact_id` STRING COMMENT '血缘父产物（被打标的抽帧图片产物），图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `camera_id` STRING COMMENT '摄像头 ID（检索时可按视角过滤）',
  `source_raw_tag` STRING COMMENT '来源系统的原始写法，字典归一前留痕，可回溯',
  `mapping_type` STRING COMMENT '字典映射结果：canonical/alias/merged_redirect',
  `mapping_note` STRING COMMENT '映射判定说明，排障用',
  `conflict_resolution` STRING COMMENT '互斥冲突裁决留痕：kind:role:rule:reason（落败方置 tag_status=invalid 但不删除）',
  `review_status` STRING COMMENT '审核状态：unreviewed(pending)/approved/rejected/corrected——模型产出先过审再上岗',
  `review_operator` STRING COMMENT '审核人',
  `review_time` TIMESTAMP(3) COMMENT '审核时间',
  `tag_status` STRING COMMENT '标签事实状态：active/invalid（即 tags 侧的 valid_flag）',
  `project_code` STRING COMMENT '所属项目（与 clip 级表对齐，覆盖度统计按项目分组免 JOIN）',
  `vehicle_code` STRING COMMENT '车辆编码（与 clip 级表对齐）',
  `first_tag_time` TIMESTAMP(3) COMMENT '首次打标时间（最近一次打标由系统字段 update_time 承载）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`image_id`, `tag_id`, `tag_source`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_image_vector_detail  [挖掘域 / DWD]  图片向量明细（全湖体量最大：千万~亿级行 × 高维向量；HNSW 索引建在其 StarRocks 外部表上）
-- 备注: [a12] 原文三处点名本表：第三章分区决策规则一的代表表（千万~亿级，按 dt 分区支撑降冷与向量索引增量刷新）、第四章 Bucket 五档表 16 档「超大表 / 高并发写入」的代表表（与 dwd_data_production_chain 并列）、第五章主键原则三的示例（PK=(image_id, embedding_version, dt)，分区表主键必须含分区字段）。分区规则一：大体量 + 时间范围查询 → 按 dt 分区。两个目的——生命周期降冷（历史向量归档）与索引分区级增量刷新（只刷新新增分区，千万级全量索引不用每天重建）。embedding_version 入主键让模型换代时新旧向量并存，灰度切换与一键回滚都不需要重写数据；dt 入主键是 Paimon 对分区表的硬要求。向量本体只存一份，Paimon 保持单一事实源，StarRocks 只提供检索加速（P95 ≤ 2s 不达标则降级为内表冗余，检索 API 零感知）。本次并入原文第五章第 ② 步「标量预过滤」要用的一整组过滤列（时间/GPS/地理网格/城市/天气/光照/道路/场景标签/摄像头位置）——检索是「向量召回 + 标量过滤」两条腿，过滤列不在表里，vector.search 的过滤器白名单就会编译出引用不存在列的 SQL；另并入四个跨域公共键（project_code/vehicle_code/dataset_id/dataset_version/model_version）与半结构化元数据列 vector_meta。vector_meta 为 VARIANT，故 extra_options 固定 file.format=parquet（a9 硬要求）；热路径的 variant.shreddingSchema 由向量子系统在建表时按 vector.variant 追加——catalog 是最底层契约，不反向依赖子系统。近义异名归一见模块 docstring：caption→caption_text、encoded_at→embed_time
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_image_vector_detail` (
  `image_id` STRING NOT NULL COMMENT '图片级 ID',
  `embedding_version` STRING NOT NULL COMMENT 'Embedding 模型版本，入主键实现新旧向量并存',
  `dt` STRING NOT NULL COMMENT '分区字段：向量化日期（yyyy-MM-dd）',
  `data_id` STRING COMMENT '一级 ID：所属 clip，检索命中后回补元数据的关联键',
  `image_embedding` ARRAY<FLOAT> COMMENT '图片向量（CLIP 图像塔输出）',
  `text_embedding` ARRAY<FLOAT> COMMENT '文本向量（CLIP 文本塔输出，与图片向量同空间，图文双向量同行）',
  `caption_text` STRING COMMENT 'caption 冗余（与图片标签表同一份说明，结构化过滤与语义检索共用），text_embedding 的编码输入',
  `embedding_dim` INT COMMENT '向量维度，必须与 HNSW 索引的 dim 属性一致',
  `model_name` STRING COMMENT 'Embedding 模型名（图文双塔同模型）',
  `model_version` STRING COMMENT '产出向量的 CLIP 模型版本（跨域公共键；与 embedding_version 一一对应，前者对外后者对内）',
  `vector_status` STRING COMMENT '向量状态：active/deprecated——检索默认只查 active 版本',
  `similarity_metric` STRING COMMENT '相似度度量：cosine（HNSW 图文双索引均采用余弦相似度）',
  `index_refresh_status` STRING COMMENT '当日分区索引刷新状态：pending/refreshing/done',
  `cost_tier` STRING COMMENT '成本分级：full/high_value 高价值全量处理，sample/normal 普通数据按比例抽样',
  `rule_priority` INT COMMENT '触发向量化的规则优先级（高优先级命中数据优先进向量化队列）',
  `capture_time` TIMESTAMP(3) COMMENT '图片采集时间——标量预过滤最常用的维度（「上个月的」「最近一周的」）',
  `camera_id` STRING COMMENT '摄像头 ID（标量预过滤维度之一）',
  `camera_position` STRING COMMENT '摄像头安装位置：front/rear/left/right——按视角过滤比按 camera_id 更贴近业务问法',
  `gps_lat` DOUBLE COMMENT '纬度（标量预过滤：范围框选）',
  `gps_lon` DOUBLE COMMENT '经度（标量预过滤：范围框选）',
  `geo_grid` STRING COMMENT '地理网格编码（标量预过滤：区域等值过滤）。经纬度范围过滤要扫两列做双向比较，网格码一次等值命中，千万级下这点差异直接决定 P95 能不能压进 2 秒',
  `city_code` STRING COMMENT '城市编码（标量预过滤：按城市圈选）',
  `weather` STRING COMMENT '天气标签：rain/snow/fog/clear 等（高选择率，从 vector_meta 提升为正式列）',
  `light_condition` STRING COMMENT '光照条件：day/night/dusk/dawn（高选择率，提升为正式列）',
  `road_type` STRING COMMENT '道路类型：highway/urban/rural（高选择率，提升为正式列）',
  `scene_tag` STRING COMMENT '主场景标签（高选择率，提升为正式列；全集在 vector_meta 里）',
  `vector_meta` VARIANT COMMENT '半结构化向量元数据：场景标签全集 / 感知事件摘要 / 模型调试属性（a9）。标签维度按项目按车型持续增生，全部提列会让表宽度失控；VARIANT 承接长尾，热路径再按 shredding schema 物化成带类型子列',
  `project_code` STRING COMMENT '所属项目（跨域公共键，也是标量预过滤维度）',
  `vehicle_code` STRING COMMENT '车辆编码（跨域公共键，也是标量预过滤维度）',
  `dataset_id` STRING COMMENT '所属数据集（跨域公共键：检索结果直接圈进训练集时按它去重）',
  `dataset_version` STRING COMMENT '数据集版本（跨域公共键，与 dataset_id 成对使用）',
  `artifact_id` STRING COMMENT '二级 ID：向量作为处理产物的 ID',
  `parent_artifact_id` STRING COMMENT '血缘父产物（抽帧图片产物）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `run_id` STRING COMMENT '三级 ID：向量化运行 ID（批次失败可断点续跑）',
  `embed_time` TIMESTAMP(3) COMMENT '向量化完成时间（T+1 每日凌晨 6 点前完成增量处理），即 vector 侧的 encoded_at',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`image_id`, `embedding_version`, `dt`) NOT ENFORCED
) PARTITIONED BY (`dt`)
WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup',
  'file.format' = 'parquet'
);

-- dwd_mining_result_detail  [挖掘域 / DWD]  挖掘结果明细（规则/模型/检索命中的 clip 级结果，供回补闭环消费）
-- 备注: 复合主键表达完整粒度「一次任务 × 一个命中 clip」；同任务重跑按 data_id Upsert 覆盖为最新结果，run_id 记录最近一次执行。挂 data_id 让「命中场景 → 原始 clip」是一次主键查询。本表是 mining 引擎 INSERT INTO 的目标（compiler._RESULT_INSERT_COLUMNS 逐列对位），本次补齐二级 ID 血缘（artifact_id/parent_artifact_id/artifact_status）、高价值分级（value_score/value_tier/vectorize_policy）与四个跨域公共键。近义异名归一见模块 docstring：window_start/end_time→event_window_start/end_time、scene_label→matched_tag_id、backfill_dataset_id→consumed_dataset_id、rule_type→rule_category
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_result_detail` (
  `mining_task_id` STRING NOT NULL COMMENT '挖掘任务 ID',
  `data_id` STRING NOT NULL COMMENT '一级 ID：命中的 clip 级终身锚点',
  `result_id` STRING COMMENT '本条命中的行 ID = CONCAT(run_id, ''_'', data_id)，确定性派生故重跑幂等（见 compiler._render_batch_select）。主键是 (mining_task_id, data_id)，result_id 供下游按「哪一次运行产出的这条命中」引用，不参与 Upsert',
  `run_id` STRING COMMENT '三级 ID：产出本条命中的运行 ID',
  `artifact_id` STRING COMMENT '二级 ID：本次命中作为处理产物的 ID（ids.derive_artifact_id(data_id, stage=''mining'', ...)）。缺了它，命中结果既接不上上游抽帧产物也接不上下游向量产物，产物血缘在挖掘这一环断掉',
  `parent_artifact_id` STRING COMMENT '血缘父产物（被扫描的 clip 产物），冗余落表供图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid（重刷不覆盖，旧产物标 superseded）',
  `rule_id` STRING COMMENT '命中规则 ID（携带 rule_id 血缘，标签自动继承字典映射与审核体系）',
  `rule_version` STRING COMMENT '命中时的规则版本',
  `rule_category` STRING COMMENT '规则种类（六大种类之一，取值见 mining.rules.RuleType）',
  `rule_priority` INT COMMENT '规则优先级（决定是否优先进向量化队列）',
  `exec_mode` STRING COMMENT '产出本条命中的执行模式：batch_t_plus_1/near_realtime。批流双写同一张表，不记模式则「这条是批补的还是流出的」无从分辨，补数与对账都要按它切分（[S3-04] 三、批流双模·结果双写）',
  `hit_type` STRING COMMENT '命中来源：rule 规则粗筛/vlm 模型细筛/vector_search 检索扩散',
  `hit_time` TIMESTAMP(3) COMMENT '命中时间',
  `hit_reason` STRING COMMENT '命中原因描述（如「CAN 减速度 < -4m/s² 持续 ≥ 0.5s」）',
  `hit_score` DOUBLE COMMENT '命中打分/置信度（规则命中为 1.0，模型命中为模型置信度）',
  `matched_tag_id` STRING COMMENT '命中后统一打标写入的 tag_id（mining 引擎的 scene_label 写在这一列）',
  `value_score` DOUBLE COMMENT '高价值综合评分（规则优先级 + 稀缺度 + 时效性多因子加权，见 mining.scoring）。与 hit_score 不是一回事：hit_score 答「命中得准不准」，value_score 答「这条值不值得花 GPU」',
  `value_tier` STRING COMMENT '评分分档：S/A/B/C（value_score 的分段函数，见 mining.scoring.ValueTier）。分档在 SQL 外层算，避免打分表达式被复制三遍',
  `vectorize_policy` STRING COMMENT '向量化策略：priority_vectorize 优先进队列/sampled 按比例抽样。[S3-01] 五 + [S3-04] 一：规则优先级直接决定 Embedding 与存储分级，不是所有命中都全量向量化',
  `event_time` TIMESTAMP(3) COMMENT '事件发生时刻（事件触发类规则）',
  `event_window_start_time` TIMESTAMP(3) COMMENT '事件窗口起（事件前 15 秒）',
  `event_window_end_time` TIMESTAMP(3) COMMENT '事件窗口止（事件后 5 秒）',
  `frame_supplement_status` STRING COMMENT '补抽帧状态：pending/running/done/skipped（异步补抽，不阻塞主链路）',
  `vehicle_code` STRING COMMENT '车辆编码',
  `project_code` STRING COMMENT '所属项目',
  `consumed_dataset_id` STRING COMMENT '回补闭环消费的目标数据集 ID（[S3-01] 六：命中结果凭它回补训练集）',
  `dataset_id` STRING COMMENT '命中 clip 当前所属数据集 ID（跨域公共键，接数据集域）',
  `dataset_version` STRING COMMENT '数据集版本（跨域公共键，与 dataset_id 成对使用）',
  `model_version` STRING COMMENT '相关模型版本（跨域公共键：模型细筛命中时即产出该结果的 VLM/感知模型版本）',
  `evaluation_type` STRING COMMENT '评测类型（跨域公共键，接评测域）：offline/simulation/real_vehicle',
  `trigger_type` STRING COMMENT '回传触发类型（跨域公共键，与 ods_vehicle_trigger_event.trigger_type 同名同义同取值域）：takeover/aeb/hard_brake/rule_hit/corner_case。事件触发类规则的命中由它回指源触发事件',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`mining_task_id`, `data_id`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_tag_dict_detail  [挖掘域 / DWD]  统一标签字典（五大类别受控词表，三来源标签收口的地基，防标签爆炸）
-- 备注: 受控词表仅千级行，取 bucket 1「字典表」档——这是全域唯一取 1 的 DWD 表。枚举参照华为云八爪鱼九大类 / 端到端世界模型三级标签 / ISO 34504·SOTIF 场景本体 / ODD 四套实践。本次并入 tags 子系统的字典治理能力列：tag_depth（层级深度）、tag_description（释义）、change_request_id（变更工单）、reviewer_secondary（复核人二）——「字典变更走工单 + 双人复核」这句话里，工单号与第二个人原本都没有落表的地方。近义异名归一见模块 docstring：applicable_sources→tag_source_type、mutex_group→mutual_exclusive_group、source_ontology→ontology_ref、reviewer_primary→review_operator
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail` (
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID（全域唯一，别名一律归一到它）',
  `tag_name` STRING COMMENT '标签标准名称',
  `tag_name_en` STRING COMMENT '标签英文名',
  `tag_category` STRING COMMENT '五大类别：SCENE 场景/ENV 环境/ROAD 道路/PARTICIPANT 参与者/BEHAVIOR 行为事件（另含 CAPTION 特殊类）',
  `parent_tag_id` STRING COMMENT '二级分类父标签 ID（类别下支持层级树）',
  `tag_path` STRING COMMENT '标签层级路径，如 ENV/天气/大雨',
  `tag_depth` INT COMMENT '层级深度：1 类别 / 2 二级分类 / 3 三级标签。tag_path 已能算出深度，但按深度过滤（大屏只展开到二级、互斥判定只在同层做）是高频查询，物化成一列免去每次切字符串',
  `tag_level` STRING COMMENT '适用粒度：clip/image/inheritable——clip 标签可自动继承到它抽出的每张图片',
  `alias_json` STRING COMMENT '别名映射（JSON）：「雨天/降雨/rain/下雨天」全部指向同一 tag_id',
  `tag_status` STRING COMMENT '四态状态机：candidate 候选/active 生效/deprecated 废弃/merged 合并',
  `merged_into_tag_id` STRING COMMENT '合并目标标签 ID（merged 态时原名保留为别名）',
  `tag_source_type` STRING COMMENT '允许写入该标签的来源：collect 采集/rule 规则/vlm 模型（可多选，逗号分隔；空表示三来源皆可）',
  `odd_dimension` STRING COMMENT '对应 ODD 运行设计域要素，支撑覆盖率统计与定向采集',
  `ontology_ref` STRING COMMENT '场景本体参照（ISO 34504 / SOTIF 条目号，或所参照的业界实践名）',
  `mutual_exclusive_group` STRING COMMENT '同层互斥组（ISO 34504 / SOTIF 同层互斥原则：同组标签不可同时命中）',
  `tag_description` STRING COMMENT '标签释义：这个标签到底指什么、边界在哪。受控词表防的是标签爆炸，而同名不同义是爆炸的第二种形态——没有释义，三个来源会各按各的理解打同一个 tag_id',
  `review_status` STRING COMMENT '审核状态：pending/approved/rejected——字典变更走工单 + 双人复核',
  `change_request_id` STRING COMMENT '引入/变更该标签的审核工单号（平台工单）。原文要求字典变更走工单，工单号不落表则「这个标签当初是凭什么加进来的」查不到',
  `review_operator` STRING COMMENT '复核人一（原文：审核结论回写 review_status / review_operator）',
  `reviewer_secondary` STRING COMMENT '复核人二。双人复核的前提是记下两个人——只留一个 review_operator，「谁也不能独自改字典」这条约束就无法事后验证',
  `effective_from` TIMESTAMP(3) COMMENT '生效时间',
  `deprecated_time` TIMESTAMP(3) COMMENT '废弃时间（历史数据仍可按原标签回溯）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '1',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_task_detail  [挖掘域 / DWD]  挖掘任务明细（规则执行追溯：扫描范围/命中数量/写入标签量可度量，而不是配完就黑盒）
-- 备注: 任务级粒度不挂 data_id（一次任务命中多个 clip），clip 级命中落 dwd_mining_result_detail。[S3-04] 一「执行追溯」要求记录的四项在本表齐备：执行时间（start_time/end_time/duration_sec）、扫描范围（scan_start_time/scan_end_time/scan_row_count）、命中数量（hit_data_count）、写入标签量（tag_write_count）；失败原因与 SLA 判定本次补齐。近义异名归一见模块 docstring：task_id→mining_task_id、executed_at→start_time、finished_at→end_time、elapsed_seconds→duration_sec、scan_low/high_watermark→scan_start/end_time、scanned_row_count→scan_row_count、hit_count→hit_data_count、tag_written_count→tag_write_count
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_task_detail` (
  `mining_task_id` STRING NOT NULL COMMENT '挖掘任务 ID',
  `run_id` STRING COMMENT '三级 ID：本次挖掘运行 ID（run_{stage}_{yyyyMMddHHmmss}_{seq}）',
  `rule_id` STRING COMMENT '关联规则 ID',
  `rule_version` STRING COMMENT '执行时的规则版本快照',
  `rule_category` STRING COMMENT '规则种类（六大种类之一，取值见 mining.rules.RuleType）',
  `rule_priority` INT COMMENT '规则优先级（驱动下游向量化与存储分级）',
  `task_type` STRING COMMENT '任务类型：rule_mining/frame_extract/vlm_infer/embedding',
  `exec_mode` STRING COMMENT '执行模式：batch_t_plus_1/near_realtime（取值见 mining.rules.ExecutionMode）',
  `engine` STRING COMMENT '执行引擎：spark/flink/ray',
  `project_code` STRING COMMENT '所属项目',
  `scan_start_time` TIMESTAMP(3) COMMENT '增量扫描水位下界（按 _ingest_time/update_time 推进，避免全表回扫）',
  `scan_end_time` TIMESTAMP(3) COMMENT '增量扫描水位上界',
  `scan_row_count` BIGINT COMMENT '扫描行数',
  `hit_data_count` BIGINT COMMENT '命中 clip 数',
  `hit_image_count` BIGINT COMMENT '命中图片数',
  `tag_write_count` BIGINT COMMENT '经统一标签服务写入的标签数',
  `frame_supplement_triggered` BOOLEAN COMMENT '是否异步触发事件补抽帧（规则结果 → 抽帧引擎解耦联动）',
  `task_status` STRING COMMENT '任务状态：pending/running/success/failed/skipped/canceled',
  `duration_sec` DOUBLE COMMENT '执行耗时（秒）',
  `start_time` TIMESTAMP(3) COMMENT '开始时间（[S3-04] 一「执行时间」）',
  `end_time` TIMESTAMP(3) COMMENT '结束时刻。原本只有 start_time + duration_sec，跨天任务与并发排队要反推结束点，对齐 executor.RuleRunRecord.finished_at。依据 [S3-04] 一「执行追溯」',
  `error_message` STRING COMMENT '失败原因（截断至 2000 字符，见 executor.RuleRunRecord.finish）。task_status=failed 而不记原因，重跑前得翻作业日志——追溯表就白建了',
  `sla_breached` BOOLEAN COMMENT '是否突破批模式 SLA。[S3-04] 三承诺「亿级以下数据 4 小时内跑完」，超时即置 true 供容量规划复盘（判定见 mining.constants.BATCH_SLA_SECONDS）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`mining_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_scene_gap_detail  [挖掘域 / DWD]  场景缺口清单（标签覆盖率对照 ODD 目标，缺口反过来驱动定向采集）
-- 备注: 复合主键表达完整粒度「一个项目 × 一个场景标签」的当前缺口快照，随覆盖度日指标 Upsert 刷新。本表是跨 clip 的聚合缺口清单，**刻意不挂 data_id**——一条缺口对应的是一批 clip 的缺失，挂上去只会恒为 NULL（同 controlplane.contracts 对任务级粒度的判断）；命中回补的 clip 级明细在 dwd_mining_result_detail，按 (project_code, tag_id) 关联即可。本次补齐的是产物血缘那一路：gap_id/run_id/artifact_id/parent_artifact_id/artifact_status——一次缺口评估就是一次运行、一份产物，没有这几列就说不清「这份缺口清单是谁、什么时候、按哪版规则算的」。近义异名归一见模块 docstring：scene_label→tag_id、hit_clip_count→current_clip_count、evaluated_at→last_eval_time、backfill_dataset_id→consumed_dataset_id
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_scene_gap_detail` (
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `tag_id` STRING NOT NULL COMMENT '场景标签 ID（对齐统一标签字典；mining 引擎的 scene_label 写在这一列）',
  `gap_id` STRING COMMENT '缺口记录 ID（一次评估 × 一个场景一条，见 mining.gaps.SceneGap）。主键是业务粒度 (project_code, tag_id)，gap_id 供采集需求单与告警回指这一条具体缺口',
  `run_id` STRING COMMENT '三级 ID：算出本条缺口的那次缺口评估运行',
  `artifact_id` STRING COMMENT '二级 ID：本条缺口作为评估产物的 ID',
  `parent_artifact_id` STRING COMMENT '血缘父产物（本次评估消费的挖掘结果产物），图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid——重算不覆盖，旧快照标 superseded',
  `rule_id` STRING COMMENT '血缘：产出本条缺口的挖掘规则 ID（规则自带 target_clip_count 目标量）。与下面 suggest_rule_id 不同——那是「建议再触发哪条规则去补」，这是「这条缺口是谁算出来的」',
  `rule_version` STRING COMMENT '血缘：评估时的规则版本快照',
  `tag_name` STRING COMMENT '场景标签名称',
  `tag_category` STRING COMMENT '标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR',
  `odd_dimension` STRING COMMENT '对应 ODD 运行设计域要素维度',
  `target_clip_count` BIGINT COMMENT '目标 clip 数（ODD 覆盖目标，取自规则的 target_clip_count）',
  `current_clip_count` BIGINT COMMENT '当前已有（库内命中）clip 数——零采集成本可直接回补的那部分',
  `current_image_count` BIGINT COMMENT '当前已有图片数',
  `gap_clip_count` BIGINT COMMENT '缺口 clip 数 = max(0, 目标 - 当前)',
  `coverage_ratio` DOUBLE COMMENT '当前覆盖率 = 当前 / 目标',
  `gap_level` STRING COMMENT '缺口等级：critical/high/medium/low（gap_severity 的分档，给人看）',
  `gap_severity` DOUBLE COMMENT '缺口严重度评分（连续值，见 mining.gaps.gap_severity）：按缺口量 × 规则优先级加权。gap_level 是它的分档；排序下发定向采集要用连续值，分档只有四级排不动序',
  `suggest_action` STRING COMMENT '建议动作：directed_collect 定向采集/rule_mining 规则挖掘/simulation 仿真生成',
  `suggest_rule_id` STRING COMMENT '建议触发的挖掘规则 ID',
  `consumed_dataset_id` STRING COMMENT '命中部分回补进的目标数据集 ID（[S3-01] 一、六：库内命中直接回补训练集，零采集成本）。与 dwd_mining_result_detail 同名同义',
  `collect_demand_id` STRING COMMENT '缺口部分下发的定向采集**需求单** ID（见 mining.gaps.CollectDemand）。与下面 related_collect_task_id 是需求与受理两端：需求由挖掘侧生成，任务由采集系统受理后回填，两者都留才能度量「下发了多少 / 真正排产了多少」',
  `related_collect_task_id` STRING COMMENT '采集系统受理该需求后生成的定向采集任务 ID',
  `gap_status` STRING COMMENT '缺口状态：satisfied 已满足/partial 部分满足/missing 完全缺失（另有 open/collecting/closed 流转态）',
  `last_eval_time` TIMESTAMP(3) COMMENT '最近一次缺口评估时间',
  `stat_date` DATE COMMENT '统计日期',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`project_code`, `tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'lookup'
);

-- dwd_closed_loop_storage_lifecycle  [闭环域 / DWD]  存储生命周期状态明细（五级分层快照，治理动作唯一决策源）
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区。主键原则二：(data_id, file_path) 复合主键表达完整粒度——一个 clip 对应多个文件（视频/点云/中间产物），治理动作以文件为最小单位。bucket 取 16：行数是 clip 级的数倍（文件级），且日级治理批量回写并发高。五级分层：hot(NAS) / warm(OSS 标准) / cold(OSS 低频) / archive(OSS 归档) /pending_delete / deleted；铁律「淘汰 ≠ 删除」——淘汰只清 NAS 副本，OSS 永远是唯一事实源；删除需三重确认（过保留期 + 血缘零引用 + 白名单校验）
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_closed_loop_storage_lifecycle` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：关联 dwd_closed_loop_trace 的 clip 锚点',
  `file_path` STRING NOT NULL COMMENT '文件路径（OSS object key / NAS 路径）',
  `artifact_id` STRING COMMENT '二级 ID：该文件所属处理产物（原始文件为空）',
  `project_code` STRING COMMENT '所属项目',
  `source_domain` STRING COMMENT '来源数据域：collect/production/dataset/training/simulation/trigger',
  `data_type` STRING COMMENT '数据类型（保留期策略维度）：raw/intermediate/dataset/model/temp',
  `storage_media` STRING COMMENT '当前介质：oss_standard/oss_ia/oss_archive/oss_deep_archive/nas',
  `lifecycle_stage` STRING COMMENT '分层状态：hot/warm/cold/archive/pending_delete/deleted',
  `file_size_bytes` BIGINT COMMENT '文件大小（字节），容量与成本折算基数',
  `create_time` TIMESTAMP(3) COMMENT '数据创建时间——温层条件「创建 30 天内」与全部保留期的计时起点',
  `stage_entered_at` TIMESTAMP(3) COMMENT '进入当前 lifecycle_stage 的时间，判定停留时长',
  `last_access_time` TIMESTAMP(3) COMMENT '最后访问时间，降冷驱动依据',
  `access_count_30d` INT COMMENT '近 30 天访问次数，NAS LRU 淘汰依据',
  `lineage_ref_count` INT COMMENT '下游血缘引用数，删除保护依据（>0 拦截删除）',
  `whitelist_flag` BOOLEAN COMMENT '白名单豁免标志：合规留存/长期回归数据跳过分层流转',
  `expire_policy` STRING COMMENT '保留策略：raw_365d/intermediate_365d/dataset_forever/model_top_n/temp_7d',
  `preheat_task_id` STRING COMMENT '最近一次预热任务 ID，预热归因',
  `preheat_time` TIMESTAMP(3) COMMENT '最近一次预热至 NAS 的时间',
  `evict_status` STRING COMMENT '淘汰状态：none/pending/done/skipped',
  `checksum_md5` STRING COMMENT '对象校验和，NAS 副本与 OSS 一致才允许清除',
  `tier_down_time` TIMESTAMP(3) COMMENT '最近一次降冷/归档流转时间',
  `monthly_cost_yuan` DOUBLE COMMENT '当前介质下的月成本折算（容量 × 介质单价）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `file_path`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dwd_closed_loop_trace  [闭环域 / DWD]  闭环全链路追溯表（终极汇总表，data_id 级全局状态快照）
-- 备注: 分区规则三：主键 Upsert 且无明确分区维度 → 不分区。bucket 取最高档 16：全湖每个 clip 一行，且被采集/生产/资产/训练/评测/部署/回传/挖掘 8 个域的 Flink 任务并发 Upsert，属超大表 + 高并发写入。只记全局状态快照、不记过程细节——过程细节在各域自己的明细表里，本表的职责是让「Badcase → 原始 clip」退化成一次主键查询。list 类字段落 JSON 数组字符串（一个 clip 会被多个训练/评测任务复用），图库（Neo4j）存完整血缘，本表冗余落表作对账兜底
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_closed_loop_trace` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点，闭环唯一入口',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '采集车辆编码',
  `collect_time` TIMESTAMP(3) COMMENT '采集时间，闭环计时起点',
  `latest_artifact_id` STRING COMMENT '二级 ID：当前生效的处理产物',
  `parent_artifact_id` STRING COMMENT '血缘父产物，冗余落表作图库对账兜底',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid（重刷不覆盖，旧产物标 superseded）',
  `latest_run_id` STRING COMMENT '三级 ID：产出当前产物的那次处理运行，支撑可重放',
  `dataset_id` STRING COMMENT '最近一次入集的数据集 ID',
  `dataset_version` STRING COMMENT '对应数据集版本',
  `training_task_id_list` STRING COMMENT '被哪些训练任务用过（JSON 数组），元素数即复用度',
  `model_version` STRING COMMENT '参与产出的最新模型版本',
  `evaluation_task_id_list` STRING COMMENT '在哪些评测任务中出现过（JSON 数组）',
  `evaluation_type` STRING COMMENT '最近一次评测类型：offline/simulation/real_vehicle',
  `badcase_flag` BOOLEAN COMMENT '是否触发过 Badcase',
  `badcase_count` INT COMMENT '关联 Badcase 数量',
  `trigger_flag` BOOLEAN COMMENT '是否被量产车触发回传过',
  `trigger_type` STRING COMMENT '量产车触发类型（回传域口径）',
  `closed_loop_stage` STRING COMMENT '当前闭环环节：collect/produce/dataset/train/evaluate/deploy/trigger',
  `closed_loop_duration_hours` DOUBLE COMMENT '完整闭环耗时（采集 → OTA 部署），DWS 效率口径输入',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);
