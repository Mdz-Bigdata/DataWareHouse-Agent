-- ========================================================================
-- 湖仓建表 · DWS 层
-- 由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：python3 scripts/export_ddl.py
-- 层级定位：汇总指标层：按业务维度预聚合，口径在此层固化，下游不再重复计算
-- 系统字段：_ingest_time + update_time
-- 本层共 14 张表：
--   · 生产域: 2 张
--   · 数据资产域: 2 张
--   · 训练域: 1 张
--   · 评测域: 2 张
--   · 回传域: 1 张
--   · 部署域: 1 张
--   · 挖掘域: 2 张
--   · 闭环域: 3 张
-- 物理策略（分区 / bucket / changelog-producer）由 catalog/spec.py 硬校验后渲染。
-- ========================================================================

-- dws_annotation_quality_daily  [生产域 / DWS]  标注质量日指标（日期 × 项目 × 标注类型 × 供应商）
-- 备注: 原则二：复合主键表达完整粒度——供应商横向对比是标注质量治理的主视角
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_annotation_quality_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目编码',
  `annotation_type` STRING NOT NULL COMMENT '标注类型：2d_box/3d_box/lane/semantic_seg/tracking',
  `vendor_name` STRING NOT NULL COMMENT '标注供应商',
  `annotation_task_count` INT COMMENT '当日标注任务数',
  `annotated_data_count` INT COMMENT '当日完成标注的数据单元数',
  `annotated_object_count` BIGINT COMMENT '当日标注目标总数',
  `qc_sample_count` INT COMMENT '质检抽检目标数',
  `qc_pass_count` INT COMMENT '质检通过目标数',
  `qc_pass_rate` DOUBLE COMMENT '质检通过率',
  `defect_count` INT COMMENT '缺陷总数',
  `miss_label_count` INT COMMENT '漏标数',
  `wrong_label_count` INT COMMENT '错标数',
  `box_inaccurate_count` INT COMMENT '框不准数',
  `attr_error_count` INT COMMENT '属性错误数',
  `rework_task_count` INT COMMENT '返工任务数',
  `rework_rate` DOUBLE COMMENT '返工率',
  `avg_accuracy_rate` DOUBLE COMMENT '平均标注准确率',
  `avg_annotate_duration_min` DOUBLE COMMENT '单数据单元平均标注耗时（分钟）',
  `avg_qc_turnaround_hour` DOUBLE COMMENT '标注提交到质检结论的平均周转（小时）',
  `cost_amount` DECIMAL(18,2) COMMENT '当日标注成本金额',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `annotation_type`, `vendor_name`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_production_efficiency_daily  [生产域 / DWS]  产线效率日指标（日期 × 项目 × 环节）
-- 备注: 口径固化层：耗时/积压/吞吐的算法只在此处定义一次，ADS 与看板不再重复计算。[a12] 第四章 Bucket 五档表点名本表作 2 档「DWS 汇总表 / 小 ADS」的代表表
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_production_efficiency_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目编码',
  `stage_code` STRING NOT NULL COMMENT '产线环节编码',
  `stage_name` STRING COMMENT '环节名称',
  `stage_order` INT COMMENT '环节序号（1~14）',
  `input_data_count` INT COMMENT '当日进入该环节的数据单元数',
  `output_data_count` INT COMMENT '当日完成该环节的数据单元数',
  `delivered_data_count` INT COMMENT '当日交付的数据单元数',
  `backlog_data_count` INT COMMENT '日终积压数据单元数',
  `blocked_over_48h_count` INT COMMENT '停留超 48 小时的数据单元数',
  `avg_duration_hour` DOUBLE COMMENT '平均环节耗时（小时）',
  `p50_duration_hour` DOUBLE COMMENT 'P50 环节耗时（小时）',
  `p90_duration_hour` DOUBLE COMMENT 'P90 环节耗时（小时）',
  `max_duration_hour` DOUBLE COMMENT '最长环节耗时（小时）',
  `avg_queue_wait_min` DOUBLE COMMENT '平均排队时长（分钟）',
  `success_rate` DOUBLE COMMENT '执行成功率',
  `rerun_count` INT COMMENT '当日重跑次数',
  `manual_intervention_count` INT COMMENT '当日人工干预次数',
  `throughput_per_hour` DOUBLE COMMENT '小时吞吐（数据单元/小时）',
  `resource_core_hour` DOUBLE COMMENT '当日资源消耗（核·时）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `stage_code`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_dataset_statistics  [数据资产域 / DWS]  数据集统计指标
-- 备注: 按「日期 × 项目 × 数据集版本」预聚合，口径在此层固化，ADS 资产目录直接取数
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_dataset_statistics` (
  `stat_date` DATE NOT NULL COMMENT '统计日期（T+1）',
  `project_code` STRING NOT NULL COMMENT '所属项目编码',
  `dataset_id` STRING NOT NULL COMMENT '数据集 ID',
  `dataset_version` STRING NOT NULL COMMENT '数据集版本号',
  `dataset_type` STRING COMMENT '数据集类型：train/eval/test/regression',
  `business_domain` STRING COMMENT '业务域：城区NOA/高速NOA/AVP',
  `data_count` BIGINT COMMENT '数据量（clip 数）',
  `image_count` BIGINT COMMENT '图片数量',
  `annotation_count` BIGINT COMMENT '标注框数量',
  `new_data_count` BIGINT COMMENT '当日新增数据量',
  `removed_data_count` BIGINT COMMENT '当日移出数据量',
  `scene_tag_count` INT COMMENT '覆盖场景标签数',
  `scene_coverage_rate` DOUBLE COMMENT '场景覆盖度 = 覆盖标签数 / 标签库总数',
  `hard_case_count` BIGINT COMMENT '难例数量',
  `hard_case_rate` DOUBLE COMMENT '难例占比',
  `badcase_related_count` BIGINT COMMENT '关联 Badcase 的数据量',
  `train_task_ref_count` INT COMMENT '被训练任务引用次数',
  `eval_task_ref_count` INT COMMENT '被评测任务引用次数',
  `storage_size_bytes` BIGINT COMMENT '占用存储',
  `quality_score` DOUBLE COMMENT '质量评分（0-5）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `dataset_id`, `dataset_version`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_scene_distribution  [数据资产域 / DWS]  场景分布统计
-- 备注: 按「日期 × 项目 × 场景类型 × 场景标签」预聚合，场景缺口在此层算出，下游 ads_scene_library_summary 与挖掘域 dwd_scene_gap_detail 共用同一口径
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_scene_distribution` (
  `stat_date` DATE NOT NULL COMMENT '统计日期（T+1）',
  `project_code` STRING NOT NULL COMMENT '所属项目编码',
  `scene_type` STRING NOT NULL COMMENT '场景类型：道路/天气/光照/交通参与者/驾驶行为',
  `scene_tag_id` STRING NOT NULL COMMENT '场景标签 ID',
  `tag_code` STRING COMMENT '标签编码',
  `tag_name` STRING COMMENT '标签名称',
  `tag_source` STRING COMMENT '主要标签来源：collect/rule/model',
  `total_data_count` BIGINT COMMENT '该场景累计数据量（clip 数）',
  `high_quality_count` BIGINT COMMENT '其中高质量数据量（质检通过且标注合格）',
  `new_data_count` BIGINT COMMENT '当日新增数据量',
  `dataset_ref_count` INT COMMENT '被数据集引用次数',
  `badcase_count` BIGINT COMMENT '关联 Badcase 数量',
  `badcase_rate` DOUBLE COMMENT 'Badcase 占比',
  `target_count` BIGINT COMMENT '达标线（目标数据量）',
  `coverage_rate` DOUBLE COMMENT '覆盖度 = total_data_count / target_count',
  `gap_count` BIGINT COMMENT '缺口量 = max(target_count - total_data_count, 0)',
  `coverage_status` STRING COMMENT '覆盖状态：GAP/FILLING/COVERED',
  `mom_growth_rate` DOUBLE COMMENT '环比增长率',
  `avg_confidence` DOUBLE COMMENT '模型打标平均置信度',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `scene_type`, `scene_tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_training_efficiency_daily  [训练域 / DWS]  训练效率日指标
-- 备注: 口径固化：按「日期 × 项目 × 模型类型」预聚合，闭环效率表的训练环节直接取此表
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_training_efficiency_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目代码',
  `model_type` STRING NOT NULL COMMENT '模型类型：perception/prediction/planning',
  `train_task_cnt` INT COMMENT '训练任务总数',
  `success_task_cnt` INT COMMENT '成功任务数',
  `fail_task_cnt` INT COMMENT '失败任务数',
  `retry_task_cnt` INT COMMENT '发生重试的任务数',
  `task_success_rate` DOUBLE COMMENT '任务成功率',
  `avg_queue_wait_min` DOUBLE COMMENT '平均排队等待时长（分钟）',
  `avg_train_duration_min` DOUBLE COMMENT '平均训练耗时（分钟）',
  `p90_train_duration_min` DOUBLE COMMENT '训练耗时 P90（分钟）',
  `avg_epoch_num` DOUBLE COMMENT '平均训练轮数',
  `total_sample_cnt` BIGINT COMMENT '训练样本总量（clip 数）',
  `total_gpu_hours` DOUBLE COMMENT 'GPU 卡时消耗合计',
  `avg_gpu_util_pct` DOUBLE COMMENT '平均 GPU 利用率（%）',
  `gpu_cost_amount` DOUBLE COMMENT '算力成本（元）',
  `new_model_version_cnt` INT COMMENT '当日新增模型版本数',
  `released_model_version_cnt` INT COMMENT '当日通过评测准入并发布的版本数',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `model_type`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_badcase_statistics  [评测域 / DWS]  Badcase 统计指标
-- 备注: 复合主键表达完整粒度：日期 × 项目 × 模型版本 × 根因大类/子类 × 严重等级
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_badcase_statistics` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `model_version` STRING NOT NULL COMMENT '被测模型版本',
  `root_cause_category` STRING NOT NULL COMMENT '根因大类',
  `root_cause_sub_category` STRING NOT NULL COMMENT '根因子类',
  `severity` STRING NOT NULL COMMENT '严重等级：P0/P1/P2',
  `badcase_type` STRING COMMENT '主要 Badcase 类型',
  `new_badcase_count` BIGINT COMMENT '当日新增 Badcase 数',
  `total_badcase_count` BIGINT COMMENT '累计 Badcase 数',
  `fixed_badcase_count` BIGINT COMMENT '已修复 Badcase 数',
  `resolve_rate` DOUBLE COMMENT 'Badcase 解决率',
  `avg_resolve_hours` DOUBLE COMMENT '平均解决时长（小时）',
  `hard_case_count` BIGINT COMMENT '转入难例库数量',
  `recollect_data_count` BIGINT COMMENT '触发定向补采的数据量',
  `top_scene_tag` STRING COMMENT '占比最高的场景标签',
  `badcase_ratio` DOUBLE COMMENT '占当日 Badcase 总量比例',
  `wow_change_rate` DOUBLE COMMENT '周环比变化率',
  `related_data_count` BIGINT COMMENT '关联 clip 数（data_id 去重）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `model_version`, `root_cause_category`, `root_cause_sub_category`, `severity`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_evaluation_summary  [评测域 / DWS]  评测汇总指标
-- 备注: 口径固化：通过率/Badcase 率在此层算一次，ADS 与报表不再重复 JOIN 计算
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_evaluation_summary` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `evaluation_type` STRING NOT NULL COMMENT '评测类型：offline/simulation/real_vehicle',
  `model_version` STRING NOT NULL COMMENT '被测模型版本',
  `dataset_id` STRING NOT NULL COMMENT '评测数据集 ID',
  `dataset_version` STRING NOT NULL COMMENT '评测数据集版本',
  `task_count` INT COMMENT '评测任务数',
  `finished_task_count` INT COMMENT '完成任务数',
  `total_case_count` BIGINT COMMENT '评测用例总数',
  `pass_case_count` BIGINT COMMENT '通过用例数',
  `pass_rate` DOUBLE COMMENT '通过率',
  `badcase_count` BIGINT COMMENT 'Badcase 数量',
  `badcase_rate` DOUBLE COMMENT 'Badcase 率',
  `avg_metric_score` DOUBLE COMMENT '平均指标得分',
  `baseline_pass_rate` DOUBLE COMMENT '基线模型通过率',
  `pass_rate_diff_pp` DOUBLE COMMENT '与基线通过率差（百分点）',
  `avg_duration_sec` DOUBLE COMMENT '平均评测耗时（秒）',
  `evaluated_data_count` BIGINT COMMENT '覆盖 clip 数（data_id 去重）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `evaluation_type`, `model_version`, `dataset_id`, `dataset_version`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_trigger_statistics  [回传域 / DWS]  触发事件统计指标（日期 × 项目 × 触发类型）
-- 备注: 口径固化：回传相关指标全公司只有这一个算法，下游不再重复 JOIN。原则二：复合主键表达完整聚合粒度。分区规则三：不分区
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_trigger_statistics` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目编码',
  `trigger_type` STRING NOT NULL COMMENT '触发类型',
  `trigger_event_cnt` BIGINT COMMENT '触发事件数',
  `trigger_vehicle_cnt` BIGINT COMMENT '发生触发的车辆数',
  `uploaded_clip_cnt` BIGINT COMMENT '成功回传的 clip 数',
  `upload_success_rate` DOUBLE COMMENT '回传成功率',
  `uploaded_size_gb` DOUBLE COMMENT '回传数据量（GB）',
  `shadow_divergence_cnt` BIGINT COMMENT '影子模式分歧事件数',
  `avg_divergence_score` DOUBLE COMMENT '平均分歧度',
  `hard_case_cnt` BIGINT COMMENT '沉淀为难例的数量',
  `hard_case_rate` DOUBLE COMMENT '难例转化率',
  `into_dataset_cnt` BIGINT COMMENT '已回补进数据集的 clip 数',
  `avg_closed_loop_latency_min` DOUBLE COMMENT '闭环平均耗时：触发 → 进训练集（分钟）',
  `p95_closed_loop_latency_min` DOUBLE COMMENT '闭环 P95 耗时（分钟）',
  `trigger_per_thousand_km` DOUBLE COMMENT '千公里触发次数，按车队里程归一化',
  `top_scene_tag` STRING COMMENT '当日触发占比最高的场景标签',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `trigger_type`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_deployment_statistics  [部署域 / DWS]  部署统计指标（日期 × 项目 × 版本 × 通道）
-- 备注: 口径固化：部署成功率、覆盖率、端到端耗时全公司只算这一次
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_deployment_statistics` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目编码',
  `software_version` STRING NOT NULL COMMENT '软件版本号',
  `release_channel` STRING NOT NULL COMMENT '发布通道：internal/grey/full',
  `model_version` STRING COMMENT '对应模型版本',
  `ota_task_count` INT COMMENT '当日 OTA 任务数',
  `target_vehicle_count` INT COMMENT '目标车辆数',
  `success_vehicle_count` INT COMMENT '部署成功车辆数',
  `fail_vehicle_count` INT COMMENT '部署失败车辆数',
  `rollback_vehicle_count` INT COMMENT '回滚车辆数',
  `deploy_success_rate` DOUBLE COMMENT '部署成功率',
  `avg_download_duration_sec` DOUBLE COMMENT '平均下载耗时（秒）',
  `avg_install_duration_sec` DOUBLE COMMENT '平均安装耗时（秒）',
  `avg_deploy_duration_sec` DOUBLE COMMENT '平均端到端部署耗时（秒）',
  `p90_deploy_duration_sec` DOUBLE COMMENT '部署耗时 P90（秒）',
  `version_coverage_rate` DOUBLE COMMENT '该版本在目标车队的覆盖率',
  `online_vehicle_count` INT COMMENT '在线车辆数',
  `top_fail_reason` STRING COMMENT '失败原因 TOP1',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `software_version`, `release_channel`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_mining_efficiency_daily  [挖掘域 / DWS]  挖掘效率日指标（按 日期 × 项目 × 任务类型 预聚合挖掘漏斗各级的产出与成本）
-- 备注: 复合主键表达完整粒度：日期 × 项目 × 任务类型，口径在此层固化，下游不再重复计算
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_mining_efficiency_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `task_type` STRING NOT NULL COMMENT '任务类型：rule_mining/frame_extract/vlm_infer/embedding',
  `task_count` BIGINT COMMENT '挖掘任务数',
  `success_task_count` BIGINT COMMENT '成功任务数',
  `fail_task_count` BIGINT COMMENT '失败任务数',
  `scan_row_count` BIGINT COMMENT '扫描数据行数（增量水位内）',
  `hit_data_count` BIGINT COMMENT '命中 clip 数',
  `hit_image_count` BIGINT COMMENT '命中图片数',
  `hit_rate` DOUBLE COMMENT '命中率 = 命中数 / 扫描数',
  `tag_write_count` BIGINT COMMENT '经统一标签服务写入的标签数',
  `frame_extract_count` BIGINT COMMENT '抽帧产出图片数（常规抽帧）',
  `event_frame_count` BIGINT COMMENT '事件补抽帧产出图片数',
  `vlm_infer_image_count` BIGINT COMMENT 'VLM 推理图片数',
  `embedding_image_count` BIGINT COMMENT '向量化图片数',
  `gpu_hours` DOUBLE COMMENT 'GPU 消耗（小时）——推理与向量化的主要成本项',
  `avg_task_duration_sec` DOUBLE COMMENT '平均任务耗时（秒）',
  `p95_task_duration_sec` DOUBLE COMMENT 'P95 任务耗时（秒）',
  `vector_search_p95_ms` DOUBLE COMMENT '向量检索 P95 时延（毫秒），验收线 ≤ 2000',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `task_type`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_mining_tag_coverage_daily  [挖掘域 / DWS]  标签覆盖度日指标（按 日期 × 项目 × 标签类别 统计标签健康度，覆盖率长期走低即定向采集的需求信号）
-- 备注: 复合主键表达完整粒度：日期 × 项目 × 标签类别；标签体系由此反过来驱动采集策略。【主键裁决：维持 (stat_date, project_code, tag_category)，不采信子系统的 (dt, tag_category)】按主键三原则之二「复合主键表达粒度」：本湖是多项目共用的，覆盖率天然是按项目算的——去掉 project_code 后，两个项目同一天同一类别的两行会撞成一行，Upsert 静默互相覆盖，而覆盖率正是定向采集的触发信号，算错方向就错。旁证是同层同域的三张表口径一致：dws_mining_efficiency_daily 是 (stat_date, project_code, task_type)、ads_mining_tag_dashboard 是 (stat_date, project_code, tag_id)、dwd_scene_gap_detail 是 (project_code, tag_id)——缺口本来就下发到项目。原则三不适用（本表不分区：一天只产出「项目 × 6 类别」量级的行，按 dt 分区没有收益）。日期列同理采信 registry 的 stat_date(DATE)，子系统的 dt(STRING yyyy-MM-dd) 在接线阶段做类型转换。bucket 两侧一致取 2（Bucket 五档第二档：DWS 汇总表）。本次并入 tags 子系统的四项能力指标：标签行数与去重标签数（词表使用广度）、审核进度三件套（已审/未审/过审率）、互斥裁决落败数、低覆盖标记。近义异名归一见模块 docstring（*_cnt→*_count、*_rate→*_ratio、clip→data）
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_mining_tag_coverage_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `tag_category` STRING NOT NULL COMMENT '标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR（+CAPTION）',
  `active_tag_count` BIGINT COMMENT '该类别下生效（active）标签数',
  `candidate_tag_count` BIGINT COMMENT '候选池待审标签数（字典未匹配的新标签，防爆炸压力表）',
  `deprecated_tag_count` BIGINT COMMENT '废弃标签数',
  `tagged_data_count` BIGINT COMMENT '已打标 clip 数（该类别下至少有一个有效标签）',
  `total_data_count` BIGINT COMMENT '全量 clip 数（clip 覆盖率分母）',
  `data_coverage_ratio` DOUBLE COMMENT 'clip 级覆盖率 = tagged_data_count / total_data_count',
  `tagged_image_count` BIGINT COMMENT '已打标图片数',
  `total_image_count` BIGINT COMMENT '全量图片数',
  `image_coverage_ratio` DOUBLE COMMENT '图片级覆盖率',
  `tag_record_count` BIGINT COMMENT '标签事实行数（含三来源重复计数）。与 tagged_data_count 的差值就是「平均每个 clip 打了几个标签」，这条曲线陡涨往往是标签爆炸的第一个征兆',
  `distinct_tag_count` BIGINT COMMENT '当日实际用到的去重标签数。对照 active_tag_count 即词表使用广度——字典里挂着一千个标签、实际只有三十个在用，说明词表在空转',
  `collect_tag_count` BIGINT COMMENT '采集来源标签条数',
  `rule_tag_count` BIGINT COMMENT '规则来源标签条数',
  `vlm_tag_count` BIGINT COMMENT '模型来源标签条数',
  `reviewed_tag_ratio` DOUBLE COMMENT '已审核标签占比（未审核标签不得进入训练集圈选）',
  `reviewed_tag_count` BIGINT COMMENT '已过审条数（绝对量）。占比能看健康度，绝对量才排得出审核工作量与积压',
  `unreviewed_tag_count` BIGINT COMMENT '未审核条数——这部分明确不得进入训练集圈选，是圈选可用量的直接扣减项',
  `review_pass_ratio` DOUBLE COMMENT '过审率 = approved / (approved + rejected)。注意与 reviewed_tag_ratio 不是一回事：那个答「审了多少」，这个答「审过的里有多少是对的」——模型标签质量下滑先从这条看出来',
  `conflict_invalid_count` BIGINT COMMENT '同层互斥裁决落败被置 invalid 的条数。持续偏高说明互斥组划分或来源优先级有问题，是字典治理的告警信号',
  `avg_confidence` DOUBLE COMMENT '平均置信度（仅带 confidence 的记录参与）',
  `coverage_trend_7d` DOUBLE COMMENT '近 7 日覆盖率变化，持续走低即定向采集需求信号',
  `low_coverage_flag` BOOLEAN COMMENT '当日是否低于低覆盖判定线。判定线口径固化在本层，下游大屏与告警直接读结论，不各自再定一套阈值（口径在 DWS 固化，下游不重复计算）',
  `gap_tag_count` BIGINT COMMENT '存在缺口的标签数（对照 dwd_scene_gap_detail）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `tag_category`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_closed_loop_efficiency  [闭环域 / DWS]  闭环效率指标（按日期 × 项目预聚合，口径在此层固化）
-- 备注: 分区规则三：不分区；bucket 取 2（DWS 汇总表档）。主键原则二：(stat_date, project_code) 复合主键表达「日期 × 项目」聚合粒度。口径固化的价值是「同一个指标全公司只有一个算法」——下游大盘与瓶颈分析直接读本表，不再重复 JOIN dwd_closed_loop_trace
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_closed_loop_efficiency` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目代码',
  `clip_total_count` BIGINT COMMENT '当日纳入统计的数据单元（clip）数',
  `delivered_clip_count` BIGINT COMMENT '当日完成交付的数据单元数',
  `trained_clip_count` BIGINT COMMENT '当日进入训练集的数据单元数',
  `collect_to_delivery_hours` DOUBLE COMMENT '采集到交付平均耗时（小时）',
  `delivery_to_dataset_hours` DOUBLE COMMENT '交付到入数据集平均耗时（小时）',
  `training_duration_hours` DOUBLE COMMENT '训练平均耗时（小时）',
  `evaluation_duration_hours` DOUBLE COMMENT '评测平均耗时（小时）',
  `deployment_duration_hours` DOUBLE COMMENT '评测到 OTA 部署平均耗时（小时）',
  `closed_loop_duration_hours` DOUBLE COMMENT '完整闭环平均耗时（采集 → 部署），核心北极星指标',
  `closed_loop_p90_hours` DOUBLE COMMENT '闭环耗时 P90（小时），看长尾而非只看均值',
  `bottleneck_stage` STRING COMMENT '瓶颈环节：耗时占比超阈值或环比恶化的环节',
  `badcase_total_count` BIGINT COMMENT '当日关联 Badcase 总数',
  `badcase_resolved_count` BIGINT COMMENT '已解决 Badcase 数',
  `badcase_resolve_rate` DOUBLE COMMENT 'Badcase 解决率 = 已解决 / 总数',
  `badcase_avg_resolve_hours` DOUBLE COMMENT 'Badcase 平均解决耗时（小时）',
  `model_version_count` INT COMMENT '当日产出模型版本数，闭环转动圈数',
  `efficiency_improve_rate` DOUBLE COMMENT '效率提升率（闭环耗时环比改善幅度）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_closed_loop_storage_cost_daily  [闭环域 / DWS]  存储成本日指标（日期 × 介质 × 分层 × 数据类型 × 来源域）
-- 备注: 分区规则三：不分区；bucket 取 2（DWS 汇总表档）。主键原则二：五段复合主键即原文给定的聚合维度，缺一维就没法回答「哪类数据在哪个介质上烧钱」。治理动作量（预热/淘汰/降冷/删除）全量登记，既支撑成本看板，也作为规则调优的反馈信号
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_closed_loop_storage_cost_daily` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `storage_media` STRING NOT NULL COMMENT '存储介质：oss_standard/oss_ia/oss_archive/oss_deep_archive/nas',
  `lifecycle_stage` STRING NOT NULL COMMENT '生命周期分层：hot/warm/cold/archive/pending_delete',
  `data_type` STRING NOT NULL COMMENT '数据类型：raw/intermediate/dataset/model/temp',
  `source_domain` STRING NOT NULL COMMENT '来源数据域：collect/production/dataset/training/simulation/trigger',
  `file_count` BIGINT COMMENT '文件数',
  `total_capacity_tb` DOUBLE COMMENT '容量合计（TB）',
  `daily_cost_yuan` DOUBLE COMMENT '当日折算成本（元，容量 × 介质单价）',
  `preheat_volume_tb` DOUBLE COMMENT '当日预热至 NAS 数据量（TB）',
  `evict_volume_tb` DOUBLE COMMENT '当日 NAS 淘汰数据量（TB，淘汰 ≠ 删除）',
  `tier_down_volume_tb` DOUBLE COMMENT '当日降冷/归档流转数据量（TB）',
  `delete_volume_tb` DOUBLE COMMENT '当日删除数据量（TB，过三重确认）',
  `nas_peak_usage` DOUBLE COMMENT 'NAS 峰值使用率（>80% 触发水位淘汰与告警）',
  `preheat_hit_rate` DOUBLE COMMENT '预热命中率 = 训练命中预热 / 总预热请求',
  `archive_restore_count` INT COMMENT '归档取回次数，反哺保留期与降冷阈值调优',
  `baseline_cost_yuan` DOUBLE COMMENT '无治理基线成本（元），节省额对照基准',
  `saved_cost_yuan` DOUBLE COMMENT '治理释放成本 = 基线成本 − 实际成本',
  `cost_mom_rate` DOUBLE COMMENT '成本环比增长率（>10% 触发预算告警）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `storage_media`, `lifecycle_stage`, `data_type`, `source_domain`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- dws_data_contribution  [闭环域 / DWS]  数据贡献度指标（按日期 × 项目 × 数据来源，衡量每类数据的模型增益）
-- 备注: 分区规则三：不分区；bucket 取 2（DWS 汇总表档）。主键原则二：三段复合主键表达「日期 × 项目 × 来源」粒度——双驱动供给（主动采集 + 量产回传）与挖掘回补的贡献必须能分开算账，才能回答「下一轮该往哪类数据投钱」。贡献与成本同表对齐，配合 dws_closed_loop_storage_cost_daily 得出单条有效数据成本
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_data_contribution` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目代码',
  `data_source` STRING NOT NULL COMMENT '数据来源：active_collect/vehicle_trigger/shadow_mode/mining_recall/simulation',
  `supply_clip_count` BIGINT COMMENT '当日供给的数据单元（clip）数',
  `into_dataset_count` BIGINT COMMENT '入数据集的数据单元数',
  `into_dataset_rate` DOUBLE COMMENT '入集率 = 入集数 / 供给数，供给质量',
  `train_used_count` BIGINT COMMENT '被训练任务引用次数（含复用）',
  `model_version_count` INT COMMENT '参与产出的模型版本数',
  `hard_case_count` BIGINT COMMENT '沉淀为难例的数据量',
  `badcase_related_count` BIGINT COMMENT '关联 Badcase 数',
  `badcase_fixed_count` BIGINT COMMENT '补数重训后修复的 Badcase 数',
  `scene_coverage_gain_pp` DOUBLE COMMENT '场景覆盖度提升（百分点）',
  `metric_gain_pp` DOUBLE COMMENT '模型指标提升（百分点，如夜间行人漏检率改善）',
  `contribution_score` DOUBLE COMMENT '综合贡献度评分（入集率 × 复用度 × 指标增益加权）',
  `contribution_rank` INT COMMENT '项目内该来源的贡献度排名',
  `storage_cost_yuan` DOUBLE COMMENT '该来源当日存储成本（元）',
  `cost_per_valid_clip` DOUBLE COMMENT '单条有效数据成本 = 存储成本 / 入集数',
  `dataset_id` STRING COMMENT '贡献最大的数据集 ID（TOP1，便于下钻）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `data_source`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);
