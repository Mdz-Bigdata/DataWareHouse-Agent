-- =============================================================================
-- 分层抽帧 · 抽帧产物唯一落表：dwd_mining_image_frame_detail
--
-- 来源：系列三 · 数据挖掘与 AI 第 2 篇《分层抽帧策略：从 TB 级采集数据中提取
--       高价值帧：三道成本闸门》（2026-09-10）
--       https://mp.weixin.qq.com/s/RrD59_FPqek-zSFMIdCRKQ
--
-- 原文一章："抽帧产物只新增一张表——dwd_mining_image_frame_detail（抽帧图片明细表），
--           image_id 内嵌 data_id，免查表即可回溯到采集单元。上游一张表、下游一张表，
--           链路干净。"
-- 原文五章："三层抽帧结果全部写 dwd_mining_image_frame_detail，字段含 image_id、
--           clip 归属、camera_id、帧序号、时间戳、GPS、文件路径与 frame_quality_score。"
--
-- 物理策略（严格套用系列二硬性规则）：
--   · 不分区——主键 Upsert 且无明确分区维度（分区决策规则三）。全湖只有 6 张表分区，
--     本表不在其中（同域分区的是 dwd_mining_image_vector_detail(dt)，不是帧表）。
--   · bucket = 16——五档里的「超大表 / 高并发写入（DWD）」。
--   · changelog-producer = lookup——DWD 层默认，本表是 Upsert 明细。
--   · 主键 image_id 单键——业务主键优先；image_id = {data_id}_{camera_id}_F{帧序号:06d}
--     已完整表达「哪个 clip / 哪路摄像头 / 第几帧」的粒度，重刷同一帧得到同一 ID，
--     Upsert 天然幂等。
--
-- ⚠️ 字段为本项目推断：原文点名了 8 个字段，其余为承接三级 ID、闸门归属与打分明细
--    而补，原文未公开完整 DDL。
--
-- 本文件可由 Python 生成，保持与代码同源：
--   python -c "from adas_lakehouse.sampling import render_frame_table_ddl; \
--              print(render_frame_table_ddl())"
-- =============================================================================

CREATE CATALOG IF NOT EXISTS paimon WITH (
  'type' = 'paimon',
  'warehouse' = 's3://adas-lakehouse/warehouse'
);

USE CATALOG paimon;
CREATE DATABASE IF NOT EXISTS adas_lakehouse;
USE adas_lakehouse;

CREATE TABLE IF NOT EXISTS `paimon`.`adas_lakehouse`.`dwd_mining_image_frame_detail` (
  -- ---- 原文五章点名的字段 ----
  `image_id` STRING NOT NULL COMMENT '帧图片 ID，格式 {data_id}_{camera_id}_F{帧序号:06d}',
  `data_id` STRING NOT NULL COMMENT '一级 ID：所属 clip 的终身锚点（clip 归属）',
  `camera_id` STRING NOT NULL COMMENT '摄像头视角：前视/侧视/后视等，检索按视角过滤、训练按视角组合',
  `frame_index` INT COMMENT '帧序号：原生 30fps 下的帧号，字典序即时间序',
  `frame_timestamp` TIMESTAMP(3) COMMENT '帧时间戳（绝对时间）',
  `gps_lat` DOUBLE COMMENT '帧对应的 GPS 纬度',
  `gps_lon` DOUBLE COMMENT '帧对应的 GPS 经度',
  `file_path` STRING COMMENT '帧图片的对象存储路径',
  `frame_quality_score` DOUBLE COMMENT '图像清晰度分：模糊/过曝/遮挡直接降权，选帧可重算复盘',
  -- ---- 三级 ID 与血缘（闭环公共键）----
  `artifact_id` STRING COMMENT '二级 ID：本帧产物，stage=sampling',
  `parent_artifact_id` STRING COMMENT '血缘父产物（冗余落表，图库对账兜底）',
  `run_id` STRING COMMENT '三级 ID：本次抽帧运行',
  `artifact_status` STRING COMMENT '产物状态：active/superseded/invalid，重刷不覆盖',
  `project_code` STRING COMMENT '所属项目',
  `vehicle_code` STRING COMMENT '车辆编码',
  -- ---- 多路摄像头同步 ----
  `frame_group_id` STRING COMMENT '多路同步组 ID：同一时刻的多路图片作为一组样本',
  -- ---- 文件属性 ----
  `file_size_bytes` BIGINT COMMENT '帧图片大小',
  `image_width` INT COMMENT '图像宽度（像素）',
  `image_height` INT COMMENT '图像高度（像素）',
  `clip_offset_ms` BIGINT COMMENT '相对 clip 起点的偏移（毫秒）',
  -- ---- 闸门归属 ----
  `sampling_tier` STRING COMMENT '产出该帧的闸门：routine 常规/event 事件（推理抽帧不产新帧，见 is_keyframe）',
  `sampling_interval_sec` DOUBLE COMMENT '该层抽帧间隔：常规默认 2 秒 1 帧，事件 1 秒 1 帧',
  `event_trigger_type` STRING COMMENT '事件触发类型：rule_hit/active_safety/driver_takeover/low_confidence',
  `event_time` TIMESTAMP(3) COMMENT '事件发生时刻（事件窗口中心）',
  `event_window_start` TIMESTAMP(3) COMMENT '事件窗口起点：事件前 15 秒',
  `event_window_end` TIMESTAMP(3) COMMENT '事件窗口终点：事件后 5 秒',
  -- ---- 打分明细（推理抽帧）----
  `object_richness_score` DOUBLE COMMENT '目标丰富度分：车辆/行人/交通设施越多分越高',
  `temporal_position_score` DOUBLE COMMENT '时间位置分：事件窗口中心、场景切换时刻优先',
  `keyframe_score` DOUBLE COMMENT '三维加权综合分，选帧依据，可重算重新圈选',
  `is_keyframe` BOOLEAN COMMENT '是否被推理抽帧选中为关键帧（每 clip 1~5 张）',
  -- ---- 合规 ----
  `desensitization_status` STRING COMMENT '抽帧前置双脱敏校验结论，未脱敏一律拒绝抽帧',
  `algo_version` STRING COMMENT '抽帧/打分算法版本，变更即产出新 artifact_id',
  -- ---- 系统字段（由 catalog.spec 按层自动追加）----
  `_ingest_time` TIMESTAMP(3) NOT NULL COMMENT '入湖时间',
  `update_time` TIMESTAMP(3) COMMENT '业务更新时间',
  PRIMARY KEY (`image_id`) NOT ENFORCED
) WITH (
  'bucket' = '16',
  'changelog-producer' = 'lookup'
);
