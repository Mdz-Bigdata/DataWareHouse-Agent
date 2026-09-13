-- ========================================================================
-- 湖仓建表 · ADS 层
-- 由 scripts/export_ddl.py 生成，请勿手工编辑；重新生成：python3 scripts/export_ddl.py
-- 层级定位：应用数据层：面向报表/大屏/应用，零 JOIN，开箱即用
-- 系统字段：_ingest_time + update_time
-- 本层共 11 张表：
--   · 生产域: 1 张
--   · 数据资产域: 2 张
--   · 训练域: 1 张
--   · 评测域: 2 张
--   · 回传域: 1 张
--   · 部署域: 1 张
--   · 挖掘域: 1 张
--   · 闭环域: 2 张
-- 物理策略（分区 / bucket / changelog-producer）由 catalog/spec.py 硬校验后渲染。
-- ========================================================================

-- ads_production_bottleneck_analysis  [生产域 / ADS]  产线瓶颈分析结果（零 JOIN，直接服务产线看板）
-- 备注: 场景①落表链路终点：dwd_data_production_chain → dws_production_efficiency_daily → 本表。回答「这批数据到哪一步了 / 哪个环节最慢 / 积压多少」
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_production_bottleneck_analysis` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目编码',
  `stage_code` STRING NOT NULL COMMENT '产线环节编码',
  `stage_name` STRING COMMENT '环节名称',
  `stage_order` INT COMMENT '环节序号（1~14）',
  `avg_duration_hour` DOUBLE COMMENT '该环节平均耗时（小时）',
  `duration_ratio` DOUBLE COMMENT '该环节耗时占全链路比例',
  `backlog_data_count` INT COMMENT '积压数据单元数',
  `blocked_over_48h_count` INT COMMENT '停留超 48 小时的数据单元数',
  `affected_batch_count` INT COMMENT '受影响批次数',
  `throughput_per_hour` DOUBLE COMMENT '小时吞吐（数据单元/小时）',
  `capacity_utilization` DOUBLE COMMENT '产能利用率',
  `bottleneck_rank` INT COMMENT '当日瓶颈排名，1 为最严重',
  `bottleneck_level` STRING COMMENT '瓶颈等级：P0/P1/P2/P3',
  `root_cause` STRING COMMENT '瓶颈根因：资源不足/人力不足/上游积压/算法失败',
  `suggestion` STRING COMMENT '优化建议',
  `trend_vs_prev_day` DOUBLE COMMENT '平均耗时环比变化率',
  `sample_data_id` STRING COMMENT '代表性数据单元 data_id，供看板下钻',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `stage_code`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_data_asset_catalog  [数据资产域 / ADS]  数据资产目录（服务数据管理平台）
-- 备注: 零 JOIN：按「资产类型 × 负责人」登记数据集/场景库/难例库/评测集的注册数、使用次数与质量评分；低分且长期无人使用的资产给出归档建议
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_data_asset_catalog` (
  `stat_date` DATE NOT NULL COMMENT '统计日期（T+1 快照）',
  `asset_type` STRING NOT NULL COMMENT '资产类型：dataset/scene_library/hard_case_library/eval_set',
  `asset_id` STRING NOT NULL COMMENT '资产 ID',
  `asset_name` STRING COMMENT '资产名称，如「城区NOA主数据集 v12」',
  `dataset_id` STRING COMMENT '关联数据集 ID（asset_type=dataset 时）',
  `dataset_version` STRING COMMENT '关联数据集版本号',
  `project_code` STRING COMMENT '所属项目编码',
  `business_domain` STRING COMMENT '业务域：城区NOA/高速NOA/AVP',
  `owner` STRING COMMENT '负责人',
  `owner_dept` STRING COMMENT '负责部门',
  `data_count` BIGINT COMMENT '资产数据量（clip 数）',
  `storage_size_bytes` BIGINT COMMENT '占用存储',
  `storage_tier` STRING COMMENT '存储分层：hot/warm/cold',
  `ref_count` INT COMMENT '累计被引用次数（训练/评测任务）',
  `ref_count_90d` INT COMMENT '近 90 天被引用次数（本季度热度）',
  `last_used_time` TIMESTAMP(3) COMMENT '最近一次被引用时间',
  `quality_score` DOUBLE COMMENT '质量评分（0-5）',
  `asset_status` STRING COMMENT '资产状态：active/idle/archived',
  `archive_suggestion` STRING COMMENT '治理建议：keep/archive/delete',
  `register_time` TIMESTAMP(3) COMMENT '资产注册时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `asset_type`, `asset_id`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_scene_library_summary  [数据资产域 / ADS]  场景库汇总（服务数据挖掘平台与数据管理平台）
-- 备注: 零 JOIN：按「场景类型 × 场景标签」统计总量、高质量量、关联 Badcase 与覆盖度；覆盖状态流转 GAP → FILLING → COVERED 驱动定向补采
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_scene_library_summary` (
  `stat_date` DATE NOT NULL COMMENT '统计日期（T+1）',
  `scene_type` STRING NOT NULL COMMENT '场景类型：道路/天气/光照/交通参与者/驾驶行为',
  `scene_tag_id` STRING NOT NULL COMMENT '场景标签 ID',
  `tag_code` STRING COMMENT '标签编码，如 CONSTRUCTION_ZONE',
  `tag_name` STRING COMMENT '标签名称，如「施工区域」',
  `priority` STRING COMMENT '补采优先级：P0/P1/P2',
  `covered_project_count` INT COMMENT '覆盖项目数',
  `total_data_count` BIGINT COMMENT '场景总数据量（clip 数）',
  `high_quality_count` BIGINT COMMENT '高质量数据量',
  `high_quality_rate` DOUBLE COMMENT '高质量占比',
  `related_badcase_count` BIGINT COMMENT '关联 Badcase 数量',
  `badcase_mom_rate` DOUBLE COMMENT 'Badcase 环比变化率',
  `target_count` BIGINT COMMENT '达标线（目标数据量）',
  `coverage_rate` DOUBLE COMMENT '覆盖度 = total_data_count / target_count',
  `gap_count` BIGINT COMMENT '缺口量',
  `coverage_status` STRING COMMENT '覆盖状态：GAP/FILLING/COVERED',
  `dataset_ref_count` INT COMMENT '被数据集引用次数',
  `mining_task_count` INT COMMENT '针对该场景下发的定向补采/挖掘任务数',
  `trend_7d` STRING COMMENT '近 7 日趋势：rising/flat/falling',
  `last_supplement_time` TIMESTAMP(3) COMMENT '最近一次补采入库时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `scene_type`, `scene_tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_model_version_comparison  [训练域 / ADS]  模型版本对比（服务评测平台 / 训练平台）
-- 备注: 表名不含数据域段（源文即如此）。零 JOIN：按「模型版本 × 评测数据集 × 场景类型」把新版本与基线的通过率/Badcase 率/平均指标分并排物化，regression_flag 为真即「带病上车」拦截点
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_model_version_comparison` (
  `model_version` STRING NOT NULL COMMENT '待对比模型版本（新版本）',
  `baseline_model_version` STRING NOT NULL COMMENT '基线模型版本',
  `dataset_id` STRING NOT NULL COMMENT '评测数据集 ID',
  `dataset_version` STRING NOT NULL COMMENT '评测数据集版本',
  `scene_type` STRING NOT NULL COMMENT '场景类型：城区/高速/夜间/逆光…',
  `project_code` STRING COMMENT '所属项目',
  `model_type` STRING COMMENT '模型类型：perception/prediction/planning',
  `evaluation_type` STRING COMMENT '评测类型（跨域公共键）',
  `eval_case_cnt` BIGINT COMMENT '参评用例数',
  `pass_rate` DOUBLE COMMENT '新版本通过率',
  `baseline_pass_rate` DOUBLE COMMENT '基线通过率',
  `pass_rate_diff_pp` DOUBLE COMMENT '通过率差值（百分点，正为提升）',
  `badcase_cnt` BIGINT COMMENT '新版本 Badcase 数',
  `badcase_rate` DOUBLE COMMENT '新版本 Badcase 率',
  `baseline_badcase_rate` DOUBLE COMMENT '基线 Badcase 率',
  `avg_metric_score` DOUBLE COMMENT '新版本平均指标分',
  `baseline_avg_metric_score` DOUBLE COMMENT '基线平均指标分',
  `regression_flag` BOOLEAN COMMENT '是否回归项（该场景指标劣化）',
  `conclusion` STRING COMMENT '对比结论：显著提升/持平/回归',
  `stat_date` DATE COMMENT '统计日期（T+1 加工）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`model_version`, `baseline_model_version`, `dataset_id`, `dataset_version`, `scene_type`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_badcase_root_cause_distribution  [评测域 / ADS]  Badcase 根因分布（问题分析平台 · 评测平台）
-- 备注: 源文实战示例表，域段写作 badcase_ 而非 evaluation_；零 JOIN，前端直接 SELECT
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_badcase_root_cause_distribution` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `model_version` STRING NOT NULL COMMENT '被测模型版本',
  `root_cause_category` STRING NOT NULL COMMENT '根因大类：感知漏检/定位漂移/规控决策/标注错误',
  `root_cause_sub_category` STRING NOT NULL COMMENT '根因子类：夜间行人/逆光车辆/施工区域',
  `evaluation_type` STRING COMMENT '评测类型：offline/simulation/real_vehicle',
  `badcase_count` BIGINT COMMENT '该根因 Badcase 数量',
  `badcase_ratio` DOUBLE COMMENT '占比（如感知漏检 45%）',
  `rank_no` INT COMMENT '根因排名',
  `trend` STRING COMMENT '趋势：up/down/flat',
  `wow_change_rate` DOUBLE COMMENT '周环比变化率',
  `mom_change_rate` DOUBLE COMMENT '月环比变化率',
  `severity_p0_count` BIGINT COMMENT 'P0 级数量',
  `related_scene_tag` STRING COMMENT '关联主要场景标签',
  `related_data_count` BIGINT COMMENT '关联 clip 数（data_id 去重）',
  `suggested_action` STRING COMMENT '建议动作：定向补采/重训/标注返工',
  `suggested_collect_count` BIGINT COMMENT '建议补采数据量',
  `owner_team` STRING COMMENT '归属团队',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `model_version`, `root_cause_category`, `root_cause_sub_category`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_hard_case_library  [评测域 / ADS]  难例库（训练平台 · 数据管理平台）
-- 备注: 按「难例类别 × 来源 × 模型版本」统计数量、采纳率与闭环验证效果（源文表 5 口径）
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_hard_case_library` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `hard_case_category` STRING NOT NULL COMMENT '难例类别：夜间行人/逆光车辆/施工区域',
  `source_type` STRING NOT NULL COMMENT '来源：evaluation/mining/trigger',
  `model_version` STRING NOT NULL COMMENT '挖出该批难例的模型版本',
  `project_code` STRING COMMENT '所属项目',
  `hard_case_count` BIGINT COMMENT '难例数量',
  `adopted_count` BIGINT COMMENT '训练采纳数量',
  `adoption_rate` DOUBLE COMMENT '采纳率',
  `adopted_dataset_id` STRING COMMENT '采纳进入的数据集 ID',
  `adopted_dataset_version` STRING COMMENT '采纳进入的数据集版本',
  `retrain_model_version` STRING COMMENT '重训后的模型版本',
  `metric_gain_pp` DOUBLE COMMENT '重训后指标提升（百分点）',
  `miss_rate_drop_pp` DOUBLE COMMENT '重训后漏检率下降（百分点）',
  `badcase_count` BIGINT COMMENT '关联 Badcase 数量',
  `related_data_count` BIGINT COMMENT '关联 clip 数（data_id 去重）',
  `avg_difficulty_score` DOUBLE COMMENT '平均难度分',
  `pending_review_count` BIGINT COMMENT '候选池待审量',
  `closed_loop_status` STRING COMMENT '闭环验证状态：pending/adopted/verified',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `hard_case_category`, `source_type`, `model_version`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_trigger_heatmap  [回传域 / ADS]  触发事件热力图（零 JOIN，直供大屏地图渲染）
-- 备注: ADS 零 JOIN：网格中心经纬度、城市名、热力等级、代表样本 data_id 全部打平，前端 SELECT 即渲染。行数 = 日期 × 项目 × 网格 × 触发类型，属小 ADS → bucket=2。分区规则三：不分区
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_trigger_heatmap` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目编码',
  `geo_grid_id` STRING NOT NULL COMMENT '地理网格 ID（GeoHash），热力图最小渲染单元',
  `trigger_type` STRING NOT NULL COMMENT '触发类型',
  `grid_center_lat` DOUBLE COMMENT '网格中心纬度',
  `grid_center_lon` DOUBLE COMMENT '网格中心经度',
  `city_code` STRING COMMENT '行政区编码',
  `city_name` STRING COMMENT '城市名称，打平避免前端查维表',
  `road_type` STRING COMMENT '网格内主要道路类型',
  `trigger_cnt` BIGINT COMMENT '网格内触发次数（热力值）',
  `heat_level` INT COMMENT '热力等级 1~5，前端直接取色，零计算',
  `vehicle_cnt` BIGINT COMMENT '网格内触发车辆数',
  `avg_vehicle_speed_kph` DOUBLE COMMENT '触发时平均车速（km/h）',
  `shadow_divergence_cnt` BIGINT COMMENT '网格内影子模式分歧数',
  `hard_case_cnt` BIGINT COMMENT '网格内沉淀的难例数',
  `top_scene_tag` STRING COMMENT '该网格最高频场景标签',
  `top_scene_tag_cnt` BIGINT COMMENT '该场景标签的触发次数',
  `sample_data_id` STRING COMMENT '代表性样本 clip 的 data_id，点击热点即下钻到原始片段',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `geo_grid_id`, `trigger_type`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_ota_deployment_summary  [部署域 / ADS]  OTA 部署汇总（面向发布看板，零 JOIN）
-- 备注: 零 JOIN：项目名、车型分布等展示字段全部冗余在本表，前端直接 SELECT
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_ota_deployment_summary` (
  `ota_task_id` STRING NOT NULL COMMENT 'OTA 任务 ID',
  `task_name` STRING COMMENT '任务名称',
  `project_code` STRING COMMENT '项目编码',
  `project_name` STRING COMMENT '项目名称（冗余，免 JOIN）',
  `software_version` STRING COMMENT '软件版本号',
  `model_version` STRING COMMENT '模型版本',
  `release_channel` STRING COMMENT '发布通道：internal/grey/full',
  `publish_time` TIMESTAMP(3) COMMENT '发布时间',
  `target_vehicle_count` INT COMMENT '目标车辆数',
  `deployed_vehicle_count` INT COMMENT '已部署成功车辆数',
  `fail_vehicle_count` INT COMMENT '失败车辆数',
  `rollback_vehicle_count` INT COMMENT '回滚车辆数',
  `deploy_progress_rate` DOUBLE COMMENT '部署进度（已下发/目标）',
  `deploy_success_rate` DOUBLE COMMENT '部署成功率',
  `avg_deploy_duration_sec` DOUBLE COMMENT '平均端到端部署耗时（秒）',
  `top_fail_reason` STRING COMMENT '失败原因 TOP1',
  `vehicle_model_distribution` STRING COMMENT '车型分布（JSON）',
  `deploy_status` STRING COMMENT '汇总状态：running/finished/aborted',
  `stat_time` TIMESTAMP(3) COMMENT '指标统计时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`ota_task_id`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_mining_tag_dashboard  [挖掘域 / ADS]  挖掘标签分布看板（零 JOIN：标签分布/三来源占比/缺口等级一次 SELECT 取齐）
-- 备注: 复合主键表达完整粒度：日期 × 项目 × 标签；字段已冗余齐全，前端大屏直接 SELECT，不再做任何关联
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_mining_tag_dashboard` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '所属项目',
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID',
  `tag_name` STRING COMMENT '标签名称（冗余，免查字典）',
  `tag_category` STRING COMMENT '标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR',
  `parent_tag_id` STRING COMMENT '二级分类父标签 ID（大屏下钻用）',
  `tag_level` STRING COMMENT '适用粒度：clip/image/inheritable',
  `tag_status` STRING COMMENT '标签状态：candidate/active/deprecated/merged',
  `data_count` BIGINT COMMENT '命中 clip 数',
  `image_count` BIGINT COMMENT '命中图片数',
  `data_ratio` DOUBLE COMMENT 'clip 数占本类别比重',
  `image_ratio` DOUBLE COMMENT '图片数占本类别比重',
  `collect_source_count` BIGINT COMMENT '采集来源标签条数',
  `rule_source_count` BIGINT COMMENT '规则来源标签条数',
  `vlm_source_count` BIGINT COMMENT '模型来源标签条数',
  `avg_confidence` DOUBLE COMMENT '平均置信度',
  `coverage_ratio` DOUBLE COMMENT '对照 ODD 目标的覆盖率',
  `gap_level` STRING COMMENT '缺口等级：critical/high/medium/low',
  `rank_in_category` INT COMMENT '类别内数量排名',
  `dod_change_ratio` DOUBLE COMMENT '环比昨日变化率',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`, `tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_closed_loop_dashboard  [闭环域 / ADS]  闭环大盘指标（监控大屏 · 数据管理平台，按日期 × 项目 T+1 物化）
-- 备注: 分区规则三：不分区；bucket 取 2（小 ADS 档，天数 × 项目数缓慢累积）。ADS 零 JOIN：项目名称等展示字段一并冗余，大屏 SELECT 即渲染。两级下钻的上层——大盘看「闭环慢不慢」，bottleneck_stage 指向 ads_production_bottleneck_analysis 定位「慢在哪一环」
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_closed_loop_dashboard` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `project_code` STRING NOT NULL COMMENT '项目代码',
  `project_name` STRING COMMENT '项目名称（零 JOIN 冗余）',
  `total_data_count` BIGINT COMMENT '累计数据总量（clip 数）',
  `month_new_data_count` BIGINT COMMENT '本月新增数据量',
  `total_capacity_tb` DOUBLE COMMENT '累计数据容量（TB）',
  `avg_closed_loop_hours` DOUBLE COMMENT '平均闭环耗时（车端触发 → OTA 部署各环节汇总）',
  `collect_to_delivery_hours` DOUBLE COMMENT '采集到交付耗时（小时）',
  `training_duration_hours` DOUBLE COMMENT '训练耗时（小时）',
  `evaluation_duration_hours` DOUBLE COMMENT '评测耗时（小时）',
  `collecting_data_count` BIGINT COMMENT '状态分布：采集/上云中',
  `producing_data_count` BIGINT COMMENT '状态分布：产线处理中',
  `delivered_data_count` BIGINT COMMENT '状态分布：已交付',
  `trained_data_count` BIGINT COMMENT '状态分布：已进入训练',
  `badcase_total_count` BIGINT COMMENT 'Badcase 总数',
  `badcase_resolve_rate` DOUBLE COMMENT 'Badcase 解决率',
  `data_growth_rate` DOUBLE COMMENT '数据增长率（环比）',
  `efficiency_improve_rate` DOUBLE COMMENT '效率提升率（闭环耗时环比改善）',
  `health_score` DOUBLE COMMENT '闭环健康度综合评分',
  `bottleneck_stage` STRING COMMENT '当前瓶颈环节，下钻产线瓶颈分析的入口',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `project_code`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);

-- ads_storage_cost_dashboard  [闭环域 / ADS]  存储成本看板（监控大屏 · 数据管理平台，介质 × 分层 × 数据类型）
-- 备注: 分区规则三：不分区；bucket 取 1——原文 Bucket 五档表就以本表作「字典表/极小表」的代表，行数 = 天数 × 4 档介质 × 5 类数据，量级极小。口径见系列一第七篇第六章：容量与成本、分层占比、治理动作量、成本节省额、NAS 峰值使用率、预热命中率、归档取回次数
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`ads_storage_cost_dashboard` (
  `stat_date` DATE NOT NULL COMMENT '统计日期',
  `storage_media` STRING NOT NULL COMMENT '存储介质：nas/oss_standard/oss_ia/oss_archive',
  `lifecycle_stage` STRING NOT NULL COMMENT '生命周期分层：hot/warm/cold/archive',
  `data_type` STRING NOT NULL COMMENT '数据类型：raw/intermediate/dataset/model/temp',
  `total_capacity_tb` DOUBLE COMMENT '总存储容量（TB）',
  `capacity_ratio` DOUBLE COMMENT '分层占比（该档容量 / 全湖容量）',
  `month_cost_yuan` DOUBLE COMMENT '月存储成本（元）',
  `unit_price_yuan_gb_month` DOUBLE COMMENT '介质单价（元/GB·月）',
  `preheat_volume_tb` DOUBLE COMMENT '治理动作量：日预热量（TB）',
  `tier_down_volume_tb` DOUBLE COMMENT '治理动作量：日降冷量（TB）',
  `evict_volume_tb` DOUBLE COMMENT '治理动作量：日淘汰量（TB）',
  `delete_volume_tb` DOUBLE COMMENT '治理动作量：日删除量（TB）',
  `baseline_cost_yuan` DOUBLE COMMENT '无治理基线成本（元）',
  `saved_cost_yuan` DOUBLE COMMENT '成本节省额 = 基线成本 − 实际成本',
  `nas_peak_usage` DOUBLE COMMENT 'NAS 峰值使用率（持续 >80% 告警）',
  `preheat_hit_rate` DOUBLE COMMENT '预热命中率',
  `archive_restore_count` INT COMMENT '归档取回次数（标准恢复 ≤ 4 小时）',
  `cost_mom_rate` DOUBLE COMMENT '成本环比增长率（>10% 触发预算告警）',
  `capacity_mom_rate` DOUBLE COMMENT '容量环比增长率，与成本增速对照看治理是否稳态',
  `budget_alert_flag` BOOLEAN COMMENT '预算告警标识（成本环比 >10% 或 NAS 使用率 >80%）',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`stat_date`, `storage_media`, `lifecycle_stage`, `data_type`) NOT ENFORCED
) WITH (
  'bucket' = '1',
  'changelog-producer' = 'full-compaction'
);
