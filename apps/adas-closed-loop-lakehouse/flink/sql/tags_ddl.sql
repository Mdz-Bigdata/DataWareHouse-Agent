-- ============================================================================
-- 统一标签体系 · 建表脚本（Flink SQL / Paimon）
-- 来源：系列三《数据闭环统一标签体系设计：三来源标签的字典映射与去重治理》
--       小周谈智驾数据闭环 · 系列三 · 数据挖掘与 AI 第 3 篇（2026-09-11）
--
-- 本文件由 adas_lakehouse.tags.tables.render_ddl() 渲染生成，改表结构请改 tables.py。
--
-- 物理策略（系列二硬性规则）：
--   · 四张表全部不分区——全湖只有 6 张表分区，标签表不在其中；
--     三张 DWD 表主键 Upsert 且无明确分区维度（分区规则三），
--     DWS 日指标一天只产 6 行，按 dt 分区无收益。
--   · Bucket 五档：字典 1 / clip 事实 8 / image 事实 16 / DWS 汇总 2。
--   · changelog-producer：DWD → lookup，DWS → full-compaction（均为该层默认）。
--   · 联合主键 (data_id/image_id, tag_id, tag_source) 来自原文第二章②。
-- ============================================================================

SET 'pipeline.name' = 'adas-tags-ddl';

-- dwd_mining_tag_dict_detail  [挖掘域 / DWD]  统一标签字典（五大类别受控词表 + 别名治理 + 四态生命周期）
-- 备注: Bucket 第一档（字典/极小表）：受控词表是百量级，不是数据表。分区规则三：主键 Upsert 且无分区维度 → 不分区。五大类别参照四套业界实践：华为云八爪鱼九大类标签（自车–他车–环境三视角）、端到端世界模型三级标签体系、ISO 34504 / SOTIF 场景本体（层级树 + 同层互斥）、ODD 运行设计域（五类映射时空/道路/参与者）。存量 ods_scene_tag 场景标签统一归入 SCENE 类别，字典是它的超集。
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_tag_dict_detail` (
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID，全局唯一口径',
  `tag_name` STRING NOT NULL COMMENT '标准标签名（唯一口径名）',
  `tag_name_en` STRING COMMENT '英文名',
  `tag_category` STRING COMMENT '五大类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR（+CAPTION 特殊类别）',
  `parent_tag_id` STRING COMMENT '二级分类的父标签 ID，支撑三级层级树',
  `tag_depth` INT COMMENT '层级深度：1 类别 / 2 二级分类 / 3 三级标签',
  `tag_level` STRING COMMENT '适用粒度：clip / image / inheritable（可继承到抽出的每张图片）',
  `alias_json` STRING COMMENT '别名映射 JSON 数组：「雨天/降雨/rain/下雨天」同指一个 tag_id',
  `tag_status` STRING NOT NULL COMMENT '四态状态机：candidate/active/deprecated/merged',
  `merged_into_tag_id` STRING COMMENT 'merged 态的合并目标；原名保留在目标的 alias_json 里',
  `mutex_group` STRING COMMENT '同层互斥组（ISO 34504 / SOTIF 同层互斥原则）',
  `applicable_sources` STRING COMMENT '允许写入该标签的来源，逗号分隔；空表示三来源皆可',
  `source_ontology` STRING COMMENT '该枚举参照的业界实践',
  `tag_description` STRING COMMENT '标签释义',
  `change_request_id` STRING COMMENT '引入/变更该标签的审核工单号（平台工单）',
  `reviewer_primary` STRING COMMENT '复核人一（双人复核，谁也不能直接改字典）',
  `reviewer_secondary` STRING COMMENT '复核人二',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`tag_id`) NOT ENFORCED
) WITH (
  'bucket' = '1',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_data_tag_detail  [挖掘域 / DWD]  clip 级标签事实（三源收口管道出口之一）
-- 备注: 原文②：联合主键（data_id, tag_id, tag_source）Upsert——重复写入无副作用，任务重跑不会产生重复标签。主键含 tag_source，因此三来源对同一标签各存一行，跨来源不互相覆盖，检索侧按来源优先级取代表行。分区规则三：主键 Upsert 且无明确分区维度 → 不分区。Bucket 8（大体量明细表）：行数 ≈ clip 数 × 人均标签数 × 来源数。
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_data_tag_detail` (
  `data_id` STRING NOT NULL COMMENT '一级 ID：clip 级终身锚点（联合主键第一段）',
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID，外键指向 dwd_mining_tag_dict_detail',
  `tag_name` STRING COMMENT '标准标签名（冗余，免去检索时回查字典）',
  `tag_category` STRING COMMENT '标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR/CAPTION',
  `tag_source` STRING NOT NULL COMMENT '标签来源：collect/rule/model（联合主键第三段）',
  `tag_level` STRING COMMENT '适用粒度：clip/image/inheritable',
  `confidence` DOUBLE COMMENT '置信度，模型标签必填',
  `rule_id` STRING COMMENT '血缘：规则标签的规则 ID',
  `rule_version` STRING COMMENT '血缘：规则版本（规则标签跟着规则版本走）',
  `model_name` STRING COMMENT '血缘：VLM 模型名',
  `model_version` STRING COMMENT '血缘：模型版本',
  `infer_job_id` STRING COMMENT '血缘：推理作业 ID',
  `run_id` STRING COMMENT '三级 ID：本次打标运行 run_tag_*',
  `artifact_id` STRING COMMENT '二级 ID：标签产物 {data_id}_tag_{algo_version}_{hash}',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `review_status` STRING COMMENT '审核状态：pending/approved/rejected/corrected',
  `review_operator` STRING COMMENT '审核人（原文：审核结论回写 review_status / review_operator）',
  `review_time` TIMESTAMP(3) COMMENT '审核时间',
  `source_raw_tag` STRING COMMENT '来源系统的原始写法，归一前留痕，可回溯',
  `mapping_type` STRING COMMENT '字典映射结果：canonical/alias/merged_redirect',
  `mapping_note` STRING COMMENT '映射判定说明，排障用',
  `conflict_resolution` STRING COMMENT '互斥冲突裁决留痕：kind:role:rule:reason',
  `valid_flag` BOOLEAN COMMENT '是否有效；互斥裁决落败置 false，但不删除，保留可追溯',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '车辆编码',
  `tag_time` TIMESTAMP(3) COMMENT '打标时间',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`data_id`, `tag_id`, `tag_source`) NOT ENFORCED
) WITH (
  'bucket' = '8',
  'changelog-producer' = 'lookup'
);

-- dwd_mining_image_tag_detail  [挖掘域 / DWD]  image 级标签事实（三源收口管道出口之二，含 CAPTION 关键说明）
-- 备注: 原文②：联合主键（image_id, tag_id, tag_source）Upsert。Bucket 16（超大表/高并发写入）：图片数 ≈ clip 数 × 抽帧数，再乘标签数，且 clip 标签会自动继承到它抽出来的每一张图片，写入放大最严重。caption 以 tag_category=CAPTION 的特殊标签写在本表，正文落 caption_text，同时冗余一份到向量表 dwd_mining_image_vector_detail——结构化过滤与语义检索共用一份说明。分区规则三：主键 Upsert 且无明确分区维度 → 不分区（注意：同域的 dwd_mining_image_vector_detail 才是按 dt 分区的那张）。
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_image_tag_detail` (
  `image_id` STRING NOT NULL COMMENT '图片 ID（联合主键第一段）',
  `data_id` STRING NOT NULL COMMENT '所属 clip 的 data_id，图片永远可回溯到采集单元',
  `tag_id` STRING NOT NULL COMMENT '标准标签 ID，外键指向 dwd_mining_tag_dict_detail',
  `tag_name` STRING COMMENT '标准标签名（冗余，免去检索时回查字典）',
  `tag_category` STRING COMMENT '标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR/CAPTION',
  `tag_source` STRING NOT NULL COMMENT '标签来源：collect/rule/model（联合主键第三段）',
  `tag_level` STRING COMMENT '适用粒度：clip/image/inheritable',
  `confidence` DOUBLE COMMENT '置信度，模型标签必填',
  `rule_id` STRING COMMENT '血缘：规则标签的规则 ID',
  `rule_version` STRING COMMENT '血缘：规则版本（规则标签跟着规则版本走）',
  `model_name` STRING COMMENT '血缘：VLM 模型名',
  `model_version` STRING COMMENT '血缘：模型版本',
  `infer_job_id` STRING COMMENT '血缘：推理作业 ID',
  `run_id` STRING COMMENT '三级 ID：本次打标运行 run_tag_*',
  `artifact_id` STRING COMMENT '二级 ID：标签产物 {data_id}_tag_{algo_version}_{hash}',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid',
  `review_status` STRING COMMENT '审核状态：pending/approved/rejected/corrected',
  `review_operator` STRING COMMENT '审核人（原文：审核结论回写 review_status / review_operator）',
  `review_time` TIMESTAMP(3) COMMENT '审核时间',
  `source_raw_tag` STRING COMMENT '来源系统的原始写法，归一前留痕，可回溯',
  `mapping_type` STRING COMMENT '字典映射结果：canonical/alias/merged_redirect',
  `mapping_note` STRING COMMENT '映射判定说明，排障用',
  `conflict_resolution` STRING COMMENT '互斥冲突裁决留痕：kind:role:rule:reason',
  `valid_flag` BOOLEAN COMMENT '是否有效；互斥裁决落败置 false，但不删除，保留可追溯',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '车辆编码',
  `tag_time` TIMESTAMP(3) COMMENT '打标时间',
  `inherited_from_clip` BOOLEAN COMMENT '是否由 clip 标签自动继承而来（tag_level=inheritable）',
  `caption_text` STRING COMMENT 'VLM 关键说明正文，仅 tag_category=CAPTION 时有值',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`image_id`, `tag_id`, `tag_source`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);

-- dws_mining_tag_coverage_daily  [挖掘域 / DWS]  标签覆盖度日指标（按「日期 × 标签类别」统计，低覆盖即定向采集信号）
-- 备注: 原文第四章指定的统计维度就是「日期 × 标签类别」，故直接作为联合主键。分区规则三：日指标表一天只产出 6 行（五大类别 + CAPTION），体量极小，按 dt 分区没有收益，不分区。Bucket 2（DWS 汇总表）。覆盖率分子只算 active 字典标签且 valid_flag=true 的记录——candidate 态「不参与正式检索与统计」。
-- ⚠️ 字段为本项目推断：原文仅给出表名与语义，未公开完整 DDL
CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dws_mining_tag_coverage_daily` (
  `dt` STRING NOT NULL COMMENT '统计日期 yyyy-MM-dd',
  `tag_category` STRING NOT NULL COMMENT '标签类别',
  `total_clip_cnt` BIGINT COMMENT '当日 clip 总数（clip 覆盖率分母）',
  `tagged_clip_cnt` BIGINT COMMENT '当日该类别下至少有一个有效标签的 clip 数',
  `clip_coverage_rate` DOUBLE COMMENT 'clip 覆盖率 = tagged_clip_cnt / total_clip_cnt',
  `total_image_cnt` BIGINT COMMENT '当日图片总数（image 覆盖率分母）',
  `tagged_image_cnt` BIGINT COMMENT '当日该类别下至少有一个有效标签的图片数',
  `image_coverage_rate` DOUBLE COMMENT 'image 覆盖率',
  `tag_record_cnt` BIGINT COMMENT '标签事实行数（含三来源重复计数）',
  `distinct_tag_cnt` BIGINT COMMENT '去重后的标签数，观测词表使用广度',
  `active_tag_cnt` BIGINT COMMENT '字典中该类别的 active 标签数',
  `candidate_tag_cnt` BIGINT COMMENT '候选池中该类别的积压数（防爆炸压力表）',
  `collect_source_cnt` BIGINT COMMENT '采集标签条数',
  `rule_source_cnt` BIGINT COMMENT '规则标签条数',
  `model_source_cnt` BIGINT COMMENT '模型标签条数',
  `reviewed_cnt` BIGINT COMMENT '已过审条数',
  `unreviewed_cnt` BIGINT COMMENT '未审核条数（这部分不得进入训练集圈选）',
  `review_pass_rate` DOUBLE COMMENT '过审率',
  `conflict_invalid_cnt` BIGINT COMMENT '互斥裁决落败被置无效的条数',
  `avg_confidence` DOUBLE COMMENT '平均置信度（仅带 confidence 的记录参与）',
  `low_coverage_flag` BOOLEAN COMMENT '当日是否低于低覆盖判定线',
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`dt`, `tag_category`) NOT ENFORCED
) WITH (
  'bucket' = '2',
  'changelog-producer' = 'full-compaction'
);
