-- ============================================================================
-- quality_gate_pipeline.sql  入湖质量门禁作业（三通道通用骨架）
--
-- 来源：系列二 · 湖仓实战 第 6 篇《数据质量门禁设计：智驾数据入湖的五步校验链路》
--   ① 门禁拦截：阻断写入 ODS，携带原始报文 + 命中规则 ID
--   ② 异常隔离：写入隔离表 ods_quality_issue，原始数据不丢失、可重放
--
-- 设计要点：门禁判定逻辑不在 SQL 里用 CASE WHEN 堆——规则是数据不是代码
-- （原文第三章「门禁规则不写死在代码里」），SQL 只负责把数据喂给 Python UDF
-- `quality_gate_check`，再按它返回的 disposition 一分为二：
--     REJECT            → ods_quality_issue（隔离，等待 ③④⑤）
--     ACCEPTED / ALLOW_WITH_FLAG → 目标 ODS 表（后者带 _quality_flag）
--
-- 阈值全部来自 src/adas_lakehouse/quality/thresholds.py，SQL 里不出现裸数字。
-- ============================================================================

SET 'pipeline.name' = 'adas-quality-gate';
-- 门禁自监控：quality_check_duration > 1000ms 即告警（原文第六章），
-- 因此 checkpoint 间隔与并行度要留出余量，别让门禁成为瓶颈。
SET 'execution.checkpointing.interval' = '60s';

-- ---------------------------------------------------------------------------
-- 1. 注册门禁 UDF
--    实现见 src/adas_lakehouse/quality/gate.py::QualityGate.check
--    返回 ROW<disposition STRING, quality_flag STRING, rule_ids STRING,
--             issue_json STRING, duration_ms DOUBLE>
-- ---------------------------------------------------------------------------
-- ADD JAR '/opt/flink/lib/flink-python.jar';
-- SET 'python.files' = '/opt/adas/src/adas_lakehouse';
-- SET 'python.client.executable' = 'python3';

CREATE TEMPORARY FUNCTION IF NOT EXISTS quality_gate_check
AS 'adas_lakehouse.quality.udf.QualityGateUdf'
LANGUAGE PYTHON;

-- ---------------------------------------------------------------------------
-- 2. 源表：三通道
--    （建表语句见各通道的入湖作业，这里只引用）
--      MySQL CDC   → cdc_annotation_result_src
--      Kafka       → kafka_vehicle_trigger_event_src
--      OSS 文件元  → oss_data_file_meta_src
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 3. 门禁判定：一次调用，两路落地
-- ---------------------------------------------------------------------------
CREATE TEMPORARY VIEW gated_trigger_event AS
SELECT
    src.*,
    gate.disposition   AS gate_disposition,
    gate.quality_flag  AS gate_quality_flag,
    gate.rule_ids      AS gate_rule_ids,
    gate.issue_json    AS gate_issue_json,
    gate.duration_ms   AS gate_duration_ms
FROM kafka_vehicle_trigger_event_src AS src
CROSS JOIN LATERAL TABLE(
    quality_gate_check(
        'ods_vehicle_trigger_event',   -- 目标表：规则按表粒度生效
        'kafka',                       -- 通道：事件流查「时空合理」
        CAST(src.* AS STRING)          -- 原始报文（JSON），隔离时原样保存
    )
) AS gate;

-- 3.1 通过 + 带标记放行 → 写 ODS
--     原文第二章：「带标记放行的数据写入 _quality_flag，供下游按质量筛选，不阻塞主链路」
INSERT INTO `paimon`.`adas_lakehouse`.`ods_vehicle_trigger_event`
SELECT
    event_id,
    trigger_type,
    data_id,
    vehicle_code,
    project_code,
    event_time,
    pre_trigger_seconds,
    post_trigger_seconds,
    gate_quality_flag AS _quality_flag,
    CURRENT_TIMESTAMP AS _ingest_time,
    'kafka:vehicle.trigger.event' AS _source_system
FROM gated_trigger_event
WHERE gate_disposition IN ('ACCEPTED', 'ALLOW_WITH_FLAG');

-- 3.2 拒绝入湖 → 写隔离表（recordRejectedData）
--     原文第三章：「被拒绝的数据连同命中规则一起落表，而不是打日志了事」
INSERT INTO `paimon`.`adas_lakehouse`.`ods_quality_issue`
SELECT
    JSON_VALUE(gate_issue_json, '$.issue_id')        AS issue_id,
    DATE_FORMAT(CURRENT_TIMESTAMP, 'yyyy-MM-dd')     AS dt,
    CURRENT_TIMESTAMP                                AS detected_at,
    'ods_vehicle_trigger_event'                      AS source_table,
    'kafka'                                          AS source_channel,
    'kafka:vehicle.trigger.event'                    AS source_system,
    CAST(event_id AS STRING)                         AS record_key,
    data_id,
    CAST(NULL AS STRING)                             AS artifact_id,
    CAST(NULL AS STRING)                             AS run_id,
    CAST(NULL AS STRING)                             AS parent_artifact_id,
    project_code,
    vehicle_code,
    gate_rule_ids                                    AS rule_ids,
    JSON_VALUE(gate_issue_json, '$.severity')        AS severity,
    JSON_VALUE(gate_issue_json, '$.issue_level')     AS issue_level,
    JSON_VALUE(gate_issue_json, '$.dimension')       AS dimension,
    JSON_VALUE(gate_issue_json, '$.quality_layer')   AS quality_layer,
    JSON_VALUE(gate_issue_json, '$.message')         AS message,
    JSON_VALUE(gate_issue_json, '$.detail')          AS detail,
    JSON_QUERY(gate_issue_json, '$.hits')            AS hits_json,
    JSON_VALUE(gate_issue_json, '$.raw_payload')     AS raw_payload,
    JSON_VALUE(gate_issue_json, '$.payload_hash')    AS payload_hash,
    CAST(NULL AS STRING)                             AS payload_object_key,
    TRUE                                             AS replayable,
    'isolated'                                       AS issue_status,
    JSON_VALUE(gate_issue_json, '$.repair_action')   AS repair_action,
    0                                                AS recheck_count,
    FALSE                                            AS escalated,
    JSON_VALUE(gate_issue_json, '$.owner')           AS owner,
    'platform-oncall'                                AS on_duty,
    CAST(JSON_VALUE(gate_issue_json, '$.response_due_at') AS TIMESTAMP(3))  AS response_due_at,
    CAST(JSON_VALUE(gate_issue_json, '$.closure_due_at')  AS TIMESTAMP(3))  AS closure_due_at,
    CAST(NULL AS TIMESTAMP(3))                       AS responded_at,
    CAST(NULL AS TIMESTAMP(3))                       AS resolved_at,
    CAST(NULL AS BOOLEAN)                            AS sla_met,
    CAST(NULL AS TIMESTAMP(3))                       AS reingested_at,
    CAST(NULL AS STRING)                             AS discard_reason,
    JSON_VALUE(gate_issue_json, '$.gate_version')    AS gate_version,
    JSON_QUERY(gate_issue_json, '$.history')         AS history,
    CURRENT_TIMESTAMP                                AS _ingest_time,
    'quality-gate'                                   AS _source_system
FROM gated_trigger_event
WHERE gate_disposition = 'REJECT';

-- ---------------------------------------------------------------------------
-- 4. 另外两条通道同构，替换三处即可：
--      源表名、目标表名、quality_gate_check 的前两个参数
--        MySQL CDC : ('ods_annotation_result', 'mysql_cdc', ...) —— 查「流程合规」
--        OSS 文件  : ('ods_data_file_meta',   'oss_file',  ...) —— 查「物理完整」
--    规则差异全在规则中心里，SQL 不需要跟着变（原文第四章：
--    「三条通道的规则都注册在同一个规则中心，按表粒度生效」）。
-- ---------------------------------------------------------------------------
