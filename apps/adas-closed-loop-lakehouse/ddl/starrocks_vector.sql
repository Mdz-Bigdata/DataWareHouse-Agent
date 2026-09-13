-- =============================================================================
-- StarRocks 向量检索：External Catalog + 双 HNSW 索引 + 检索 SQL + 降级内表
-- 来源：a7 第四 / 五 / 六章
-- 本文件由 `python -m adas_lakehouse.vector.render_sql` 生成，请勿手改；
-- 参数改动请改 src/adas_lakehouse/vector/params.py。
-- =============================================================================

-- 唯一验收线：千万级数据量单次向量检索 P95 ≤ 2.0 秒。
-- POC 五项前置验证（外部表向量索引是相对新的能力，全量上线前必须先过）：
--   1. 多向量列同表索引支持度（两个 ARRAY<FLOAT> 列能否各建一个） [硬验收线]
--   2. 分区级索引支持度（刷新能否精确到单个分区） [硬验收线]
--   3. 索引构建耗时（千万级向量的建索引时间）
--   4. 增量刷新延迟（当日分区刷完的时间窗）
--   5. 千万级检索 P95（P95 ≤ 2 秒 —— 这是整条链路的验收线） [硬验收线]

-- ---------------------------------------------------------------- 1. 外部表（第一档）
-- StarRocks External Catalog：直查 Paimon，StarRocks 只提供检索加速，不持有主数据
CREATE EXTERNAL CATALOG IF NOT EXISTS paimon_catalog
PROPERTIES (
    "type" = "paimon",
    "paimon.catalog.type" = "filesystem",
    "paimon.catalog.warehouse" = "s3://adas-lakehouse/warehouse",
    "aws.s3.endpoint" = "http://localhost:18600",
    "aws.s3.enable_path_style_access" = "true",
    "aws.s3.access_key" = "${MINIO_ACCESS_KEY}",
    "aws.s3.secret_key" = "${MINIO_SECRET_KEY}"
);

-- 在 Paimon 外部表 paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail 上构建 HNSW 索引（图文各一个，余弦相似度）
-- ⚠️ 外部表向量索引是相对新的能力，全量上线前必须先过 POC 验证（原文第四章五项）
-- 图片向量索引：图搜图走它；混合检索两个索引都用
CREATE INDEX idx_image_embedding_hnsw ON paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail (`image_embedding`) USING VECTOR
PROPERTIES (
    "index_type" = "hnsw",
    "dim" = "512",
    "metric_type" = "cosine_similarity",
    "is_vector_normed" = "true",
    "M" = "16",
    "efconstruction" = "200"
);

-- 文本向量索引：文搜图走它；混合检索两个索引都用
CREATE INDEX idx_text_embedding_hnsw ON paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail (`text_embedding`) USING VECTOR
PROPERTIES (
    "index_type" = "hnsw",
    "dim" = "512",
    "metric_type" = "cosine_similarity",
    "is_vector_normed" = "true",
    "M" = "16",
    "efconstruction" = "200"
);

-- 分区级增量刷新（每日向量写入完成后执行，只刷当日分区）
REFRESH EXTERNAL TABLE paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail PARTITION ('2026-09-06');
ALTER TABLE paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail BUILD INDEX idx_image_embedding_hnsw PARTITION (`dt` = '2026-09-06');
ALTER TABLE paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail BUILD INDEX idx_text_embedding_hnsw PARTITION (`dt` = '2026-09-06');

-- ---------------------------------------------------------------- 2. 检索 SQL（四类能力）
-- 检索时下推 efSearch（TopK 默认 50，不为用不到的长尾结果付出检索成本）
SET ann_params = '{"efsearch":"128"}';

-- 2.1 文搜图 / 标签+向量：最近 30 天 + 某城市 + 雨天
--     参数（按顺序绑定）：['2026-08-07', '2026-09-06', 'SH', 'rain']
-- 检索模式: tag_plus_vector | 索引路径: idx_text_embedding_hnsw | 档位: external_paimon
-- 一条语句同时完成：分区裁剪、版本过滤（只查 active）、标量预过滤
SELECT `image_id`, approx_cosine_similarity(`text_embedding`, [/* 512 维 query 向量，由同一个 CLIP 模型在检索服务层实时编码 */]) AS text_sim
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail
WHERE vector_status = 'active'
  AND `dt` >= %s
  AND `dt` <= %s
  AND `city_code` = %s
  AND `weather` = %s
ORDER BY text_sim DESC
LIMIT 50;

-- 2.2 图搜图：Badcase 找相似样本
-- 检索模式: image_to_image | 索引路径: idx_image_embedding_hnsw | 档位: external_paimon
-- 一条语句同时完成：分区裁剪、版本过滤（只查 active）、标量预过滤
SELECT `image_id`, approx_cosine_similarity(`image_embedding`, [/* 512 维 query 向量，由同一个 CLIP 模型在检索服务层实时编码 */]) AS image_sim
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail
WHERE vector_status = 'active'
ORDER BY image_sim DESC
LIMIT 50;

-- 2.3 混合检索：图文双向量加权融合（image×w1 + text×w2）+ 服务层重排
-- 检索模式: hybrid | 索引路径: idx_image_embedding_hnsw + idx_text_embedding_hnsw | 档位: external_paimon
-- 一条语句同时完成：分区裁剪、版本过滤（只查 active）、标量预过滤
SELECT `image_id`, approx_cosine_similarity(`image_embedding`, [/* 512 维 query 向量，由同一个 CLIP 模型在检索服务层实时编码 */]) AS image_sim, approx_cosine_similarity(`text_embedding`, [/* 512 维 query 向量，由同一个 CLIP 模型在检索服务层实时编码 */]) AS text_sim, (0.5 * image_sim + 0.5 * text_sim) AS fused_score
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail
WHERE vector_status = 'active'
  AND `dt` >= %s
  AND `dt` <= %s
  AND `city_code` = %s
  AND `weather` = %s
ORDER BY fused_score DESC
LIMIT 50;

-- ---------------------------------------------------------------- 3. 降级内表（第二档）
-- 触发条件：POC 硬验收线不达标，或线上 P95 连续超线
-- 降级形态（第二档）：向量冗余写入 StarRocks 内表，定时同步 + 主键对账
-- 检索链路与 API 完全不变，Paimon 始终是单一事实源与对账基准
-- 向量维度 512（写在索引 PROPERTIES 的 dim 属性里，见 ddl/starrocks_vector.sql）
CREATE TABLE IF NOT EXISTS `adas_ads`.`dwd_mining_image_vector_detail` (
    `image_id` VARCHAR(256) NOT NULL COMMENT "图片级 ID",
    `embedding_version` VARCHAR(256) NOT NULL COMMENT "Embedding 模型版本，入主键实现新旧向量并存",
    `dt` VARCHAR(256) NOT NULL COMMENT "分区字段：向量化日期（yyyy-MM-dd）",
    `data_id` VARCHAR(256) COMMENT "一级 ID：所属 clip，检索命中后回补元数据的关联键",
    `image_embedding` ARRAY<FLOAT> NOT NULL COMMENT "图片向量（CLIP 图像塔输出）",
    `text_embedding` ARRAY<FLOAT> NOT NULL COMMENT "文本向量（CLIP 文本塔输出，与图片向量同空间，图文双向量同行）",
    `caption_text` VARCHAR(2048) COMMENT "caption 冗余（与图片标签表同一份说明，结构化过滤与语义检索共用），text_embedding 的编码输入",
    `embedding_dim` INT COMMENT "向量维度，必须与 HNSW 索引的 dim 属性一致",
    `model_name` VARCHAR(256) COMMENT "Embedding 模型名（图文双塔同模型）",
    `model_version` VARCHAR(256) COMMENT "产出向量的 CLIP 模型版本（跨域公共键；与 embedding_version 一一对应，前者对外后者对内）",
    `vector_status` VARCHAR(256) COMMENT "向量状态：active/deprecated——检索默认只查 active 版本",
    `similarity_metric` VARCHAR(256) COMMENT "相似度度量：cosine（HNSW 图文双索引均采用余弦相似度）",
    `index_refresh_status` VARCHAR(256) COMMENT "当日分区索引刷新状态：pending/refreshing/done",
    `cost_tier` VARCHAR(256) COMMENT "成本分级：full/high_value 高价值全量处理，sample/normal 普通数据按比例抽样",
    `rule_priority` INT COMMENT "触发向量化的规则优先级（高优先级命中数据优先进向量化队列）",
    `capture_time` DATETIME COMMENT "图片采集时间——标量预过滤最常用的维度（「上个月的」「最近一周的」）",
    `camera_id` VARCHAR(256) COMMENT "摄像头 ID（标量预过滤维度之一）",
    `camera_position` VARCHAR(256) COMMENT "摄像头安装位置：front/rear/left/right——按视角过滤比按 camera_id 更贴近业务问法",
    `gps_lat` DOUBLE COMMENT "纬度（标量预过滤：范围框选）",
    `gps_lon` DOUBLE COMMENT "经度（标量预过滤：范围框选）",
    `geo_grid` VARCHAR(256) COMMENT "地理网格编码（标量预过滤：区域等值过滤）。经纬度范围过滤要扫两列做双向比较，网格码一次等值命中，千万级下这点差异直接决定 P95 能不能压进 2 秒",
    `city_code` VARCHAR(256) COMMENT "城市编码（标量预过滤：按城市圈选）",
    `weather` VARCHAR(256) COMMENT "天气标签：rain/snow/fog/clear 等（高选择率，从 vector_meta 提升为正式列）",
    `light_condition` VARCHAR(256) COMMENT "光照条件：day/night/dusk/dawn（高选择率，提升为正式列）",
    `road_type` VARCHAR(256) COMMENT "道路类型：highway/urban/rural（高选择率，提升为正式列）",
    `scene_tag` VARCHAR(256) COMMENT "主场景标签（高选择率，提升为正式列；全集在 vector_meta 里）",
    `vector_meta` JSON COMMENT "半结构化向量元数据：场景标签全集 / 感知事件摘要 / 模型调试属性（a9）。标签维度按项目按车型持续增生，全部提列会让表宽度失控；VARIANT 承接长尾，热路径再按 shredding schema 物化成带类型子列",
    `project_code` VARCHAR(256) COMMENT "所属项目（跨域公共键，也是标量预过滤维度）",
    `vehicle_code` VARCHAR(256) COMMENT "车辆编码（跨域公共键，也是标量预过滤维度）",
    `dataset_id` VARCHAR(256) COMMENT "所属数据集（跨域公共键：检索结果直接圈进训练集时按它去重）",
    `dataset_version` VARCHAR(256) COMMENT "数据集版本（跨域公共键，与 dataset_id 成对使用）",
    `artifact_id` VARCHAR(512) COMMENT "二级 ID：向量作为处理产物的 ID",
    `parent_artifact_id` VARCHAR(512) COMMENT "血缘父产物（抽帧图片产物）",
    `artifact_status` VARCHAR(256) COMMENT "产物状态：active/superseded/invalid",
    `run_id` VARCHAR(256) COMMENT "三级 ID：向量化运行 ID（批次失败可断点续跑）",
    `embed_time` DATETIME COMMENT "向量化完成时间（T+1 每日凌晨 6 点前完成增量处理），即 vector 侧的 encoded_at",
    `_ingest_time` DATETIME COMMENT "入湖时间",
    `update_time` DATETIME COMMENT "业务更新时间"
)
ENGINE = OLAP
PRIMARY KEY (`image_id`, `embedding_version`, `dt`)
PARTITION BY (`dt`)
DISTRIBUTED BY HASH(`image_id`) BUCKETS 32
PROPERTIES (
    "replication_num" = "1",
    "enable_persistent_index" = "true"
);

-- 在 StarRocks 内表 adas_ads.dwd_mining_image_vector_detail 上构建 HNSW 索引（图文各一个，余弦相似度）
-- ⚠️ 外部表向量索引是相对新的能力，全量上线前必须先过 POC 验证（原文第四章五项）
-- 图片向量索引：图搜图走它；混合检索两个索引都用
CREATE INDEX idx_image_embedding_hnsw ON adas_ads.dwd_mining_image_vector_detail (`image_embedding`) USING VECTOR
PROPERTIES (
    "index_type" = "hnsw",
    "dim" = "512",
    "metric_type" = "cosine_similarity",
    "is_vector_normed" = "true",
    "M" = "16",
    "efconstruction" = "200"
);

-- 文本向量索引：文搜图走它；混合检索两个索引都用
CREATE INDEX idx_text_embedding_hnsw ON adas_ads.dwd_mining_image_vector_detail (`text_embedding`) USING VECTOR
PROPERTIES (
    "index_type" = "hnsw",
    "dim" = "512",
    "metric_type" = "cosine_similarity",
    "is_vector_normed" = "true",
    "M" = "16",
    "efconstruction" = "200"
);

-- 定时同步
-- 第二档定时同步：paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail -> adas_ads.dwd_mining_image_vector_detail（分区 2026-09-06）
-- 主键模型 INSERT 即 Upsert，重跑无副作用；Paimon 仍是单一事实源
INSERT INTO adas_ads.dwd_mining_image_vector_detail
SELECT * FROM paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail
WHERE `dt` = '2026-09-06'
  AND `image_embedding` IS NOT NULL
  AND `text_embedding` IS NOT NULL
  AND vector_status = 'active';

-- 主键对账（以 Paimon 外部表为基准）
-- 主键对账（分区 2026-09-06）：以 Paimon 外部表为基准
SELECT
    COUNT(*)                                              AS lake_rows,
    COUNT(i.`image_id`)                                   AS internal_rows,
    COUNT(*) - COUNT(i.`image_id`)                        AS missing_in_internal,
    SUM(CASE WHEN i.`image_id` IS NOT NULL
              AND i.`artifact_id` <> e.`artifact_id` THEN 1 ELSE 0 END) AS artifact_drift
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail e
LEFT JOIN adas_ads.dwd_mining_image_vector_detail i ON e.`image_id` = i.`image_id` AND e.`embedding_version` = i.`embedding_version` AND e.`dt` = i.`dt`
WHERE e.`dt` = '2026-09-06' AND e.vector_status = 'active';

-- 缺失主键明细（最多 1000 行，用于补数）
SELECT e.`image_id`, e.`embedding_version`, e.`dt`
FROM paimon_catalog.adas_lakehouse.dwd_mining_image_vector_detail e
LEFT JOIN adas_ads.dwd_mining_image_vector_detail i ON e.`image_id` = i.`image_id` AND e.`embedding_version` = i.`embedding_version` AND e.`dt` = i.`dt`
WHERE e.`dt` = '2026-09-06' AND e.vector_status = 'active' AND i.`image_id` IS NULL
LIMIT 1000;
