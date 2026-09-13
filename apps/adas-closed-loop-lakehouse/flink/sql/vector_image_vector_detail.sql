-- =============================================================================
-- 向量明细表 dwd_mining_image_vector_detail（Paimon，单一事实源）
-- 来源：a7 第二章《向量落湖：一张千万级的向量明细表》 + a9（vector_meta VARIANT）
-- 本文件由 `python -m adas_lakehouse.vector.render_sql` 生成，请勿手改；
-- 参数改动请改 src/adas_lakehouse/vector/params.py。
-- =============================================================================

-- 三个设计决策：
--   1. 图文双向量同行——同一个 CLIP 模型双塔编码，保证图文向量在同一空间；
--   2. 按 dt 分区——全湖唯一按日期分区的明细表，支撑生命周期降冷与分区级索引增量刷新；
--   3. embedding_version 入主键——模型换代新旧向量并存，vector_status 区分 active/deprecated。
-- VARIANT 列要求：Spark 4.0+ 或 Flink 2.1+，数据文件必须是 parquet（a9 第 03 节）。
-- vector_meta 热路径：
--     $.perception.weather -> STRING
--     $.perception.object_count -> INT
--     $.scene.tags -> STRING
--     $.diagnostics.code -> STRING
--     $.model.debug_score -> DOUBLE

-- vector_meta 的显式 shredding schema（热路径物化成带类型的 Parquet 子列）
--   'variant.shreddingSchema' = '{"type":"ROW","fields":[{"name":"vector_meta","type":{"type":"ROW","fields":[{"name":"perception_weather","type":"STRING"},{"name":"perception_object_count","type":"INT"},{"name":"scene_tags","type":"STRING"},{"name":"diagnostics_code","type":"STRING"},{"name":"model_debug_score","type":"DOUBLE"}]}}]}'
-- 探索期可改用自动推断（a9 原文第 04 节参数，逐字）：
--   'variant.inferShreddingSchema' = 'true'
--   'variant.shredding.maxInferBufferRow' = '4096'
--   'variant.shredding.maxSchemaWidth' = '300'
--   'variant.shredding.maxSchemaDepth' = '50'
--   'variant.shredding.minFieldCardinalityRatio' = '0.1'

USE CATALOG `paimon`;
USE `adas_lakehouse`;

-- dwd_mining_image_vector_detail  [挖掘域 / DWD]  图片向量明细（全湖体量最大：千万~亿级行 × 高维向量；HNSW 索引建在其 StarRocks 外部表上）
-- 备注: 分区规则一：大体量 + 时间范围查询 → 按 dt 分区。两个目的——生命周期降冷（历史向量归档）与索引分区级增量刷新（只刷新新增分区，千万级全量索引不用每天重建）。embedding_version 入主键让模型换代时新旧向量并存，灰度切换与一键回滚都不需要重写数据；dt 入主键是 Paimon 对分区表的硬要求。向量本体只存一份，Paimon 保持单一事实源，StarRocks 只提供检索加速（P95 ≤ 2s 不达标则降级为内表冗余，检索 API 零感知）。本次并入原文第五章第 ② 步「标量预过滤」要用的一整组过滤列（时间/GPS/地理网格/城市/天气/光照/道路/场景标签/摄像头位置）——检索是「向量召回 + 标量过滤」两条腿，过滤列不在表里，vector.search 的过滤器白名单就会编译出引用不存在列的 SQL；另并入四个跨域公共键（project_code/vehicle_code/dataset_id/dataset_version/model_version）与半结构化元数据列 vector_meta。vector_meta 为 VARIANT，故 extra_options 固定 file.format=parquet（a9 硬要求）；热路径的 variant.shreddingSchema 由向量子系统在建表时按 vector.variant 追加——catalog 是最底层契约，不反向依赖子系统。近义异名归一见模块 docstring：caption→caption_text、encoded_at→embed_time
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
  'file.format' = 'parquet',
  'variant.shreddingSchema' = '{"type":"ROW","fields":[{"name":"vector_meta","type":{"type":"ROW","fields":[{"name":"perception_weather","type":"STRING"},{"name":"perception_object_count","type":"INT"},{"name":"scene_tags","type":"STRING"},{"name":"diagnostics_code","type":"STRING"},{"name":"model_debug_score","type":"DOUBLE"}]}}]}'
);
