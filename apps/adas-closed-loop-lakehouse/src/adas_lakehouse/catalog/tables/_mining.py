"""挖掘域（mining_）：挖掘任务/规则/抽帧/标签/向量/推理。

挖掘平台是闭环的「第二入口」——采集是第一入口，挖掘负责从既有海量数据里
二次发现高价值场景。它的 DWD 表数量为全湖各域之最（8 张），原因是
抽帧 → 标签 → 向量 → 推理每个环节都要独立明细表，且彼此经湖仓表解耦协作
（规则命中异步触发补抽帧，抽帧产物喂 VLM，VLM 的 caption 又回流向量表）。

本域三张「全湖之最」的表：
  · dwd_mining_image_vector_detail —— 全湖体量最大（千万~亿级行 × 高维向量），
    也是本域唯一分区表（按 dt），HNSW 索引建在它的 StarRocks 外部表上；
  · dwd_mining_tag_dict_detail   —— 统一标签字典，三来源标签收口的地基；
  · dwd_mining_image_frame_detail —— 三级分层抽帧（常规/事件/推理）的唯一产物表。

来源：系列二第 1/7 篇、系列三第 2/3/4 篇。

================================================================================
子系统列契约并入说明（本次施工）
================================================================================
挖掘域是「一表四写」的热点：mining / tags / sampling / vector 四个子系统各自
持有一份本地列契约（``mining.tables.*_COLUMNS`` 直接拼进 INSERT INTO；
``tags.tables.TABLES`` / ``sampling.table.FRAME_TABLE_SPEC`` / ``vector.schema._local_spec``
是本地 TableSpec）。本模块是表结构的唯一事实源，子系统需要而 registry 缺失的
**能力列**已全部并入下面各 TableSpec。

并入采用两条规则，不许第三条：

  ① 能力列（registry 没有等义列）→ 直接并入，列注释写清用途与出处。
  ② 近义异名（registry 已有等义列，只是名字不同）→ **不新增孪生列**，
     以 registry 既有列名为权威，子系统那边在「接线」阶段改名对齐。

规则 ② 不是洁癖：registry 只增不减，若把 ``exec_mode`` 与 ``execution_mode``
两个名字同时落成物理列，写入方只写一个，另一个恒为 NULL——读到 NULL 的下游
既不报错也不告警，这正是本次要消灭的那类静默失效（参见 a6 质量门禁：列不存在
= 阈值静默不生效）。宁可让接线阶段改一次名，也不留一列永远为 NULL 的影子列。

--------------------------------------------------------------------------------
归一映射表（接线阶段按此改子系统，registry 侧不再变动）
--------------------------------------------------------------------------------
ods_mining_rule_config（mining.tables.RULE_CONFIG_COLUMNS）
    rule_type            -> rule_category
    execution_mode       -> exec_mode
    expression_mode      -> express_mode
    sql_condition        -> rule_sql
    visual_config_json   -> rule_condition_json
    scene_label          -> target_tag_id
    created_at           -> create_time
    updated_at           -> last_modify_time   （本次新增，与既有 last_modify_user 配对）
    disabled_at          -> disable_time       （本次新增）

dwd_mining_result_detail（mining.tables.MINING_RESULT_COLUMNS）
    rule_type            -> rule_category
    execution_mode       -> exec_mode          （本次新增，与本域其余三张表同名同义）
    window_start_time    -> event_window_start_time
    window_end_time      -> event_window_end_time
    scene_label          -> matched_tag_id
    backfill_dataset_id  -> consumed_dataset_id

dwd_scene_gap_detail（mining.tables.SCENE_GAP_COLUMNS）
    scene_label          -> tag_id
    hit_clip_count       -> current_clip_count
    backfill_dataset_id  -> consumed_dataset_id（本次新增）
    evaluated_at         -> last_eval_time

dwd_mining_task_detail（mining.tables.MINING_TASK_COLUMNS）
    task_id              -> mining_task_id
    execution_mode       -> exec_mode
    executed_at          -> start_time
    finished_at          -> end_time            （本次新增）
    elapsed_seconds      -> duration_sec
    scan_low_watermark   -> scan_start_time
    scan_high_watermark  -> scan_end_time
    scanned_row_count    -> scan_row_count
    hit_count            -> hit_data_count
    tag_written_count    -> tag_write_count

dwd_mining_tag_dict_detail（tags.tables）
    applicable_sources   -> tag_source_type
    mutex_group          -> mutual_exclusive_group
    source_ontology      -> ontology_ref
    reviewer_primary     -> review_operator     （复核人二为本次新增 reviewer_secondary）

dwd_mining_data_tag_detail / dwd_mining_image_tag_detail（tags.tables）
    tag_time             -> first_tag_time      （「最近一次打标」由系统字段 update_time 承载）
    valid_flag           -> tag_status          （false ≡ 'invalid'）
    inherited_from_clip  -> inherited_from_data_tag（仅 image 表）

dws_mining_tag_coverage_daily（tags.tables）
    dt                   -> stat_date（DATE，需 yyyy-MM-dd 字符串 → DATE 转换）
    total_clip_cnt       -> total_data_count
    tagged_clip_cnt      -> tagged_data_count
    clip_coverage_rate   -> data_coverage_ratio
    total_image_cnt      -> total_image_count
    tagged_image_cnt     -> tagged_image_count
    image_coverage_rate  -> image_coverage_ratio
    active_tag_cnt       -> active_tag_count
    candidate_tag_cnt    -> candidate_tag_count
    collect_source_cnt   -> collect_tag_count
    rule_source_cnt      -> rule_tag_count
    model_source_cnt     -> vlm_tag_count

dwd_mining_image_frame_detail（sampling.table.FRAME_TABLE_SPEC）
    sampling_tier          -> extract_level
    event_trigger_type     -> trigger_event_type   ★ 字序颠倒的近义异名，取值域与
                              ods_vehicle_trigger_event.trigger_type 同口径
    file_path              -> image_object_key
    file_size_bytes        -> image_size_bytes
    clip_offset_ms         -> frame_offset_sec     （单位换算：毫秒 ÷ 1000 → 秒）
    desensitization_status -> desensitize_status
    event_window_start     -> event_window_start_time（本次新增，与结果表同名同义）
    event_window_end       -> event_window_end_time  （本次新增）

dwd_mining_image_vector_detail（vector.schema._local_spec）
    caption              -> caption_text（与图片标签表的 CAPTION 正文同名同义）
    encoded_at           -> embed_time

--------------------------------------------------------------------------------
待接线阶段一并处理的类型口径（本模块不改既有列的类型，以免打断已建表）
--------------------------------------------------------------------------------
  · rule_version：registry 为 STRING，mining 子系统的 RuleDefinition.rule_version 是 int，
    拼 SQL 时需显式 CAST，否则 Flink 侧隐式转换行为依版本而异。
  · clip_offset_ms → frame_offset_sec：毫秒整数 → 秒浮点，转换在写入侧做。
  · dt → stat_date：字符串 → DATE。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.MINING

TABLES: list[TableSpec] = [
    # ------------------------------------------------------------------ ODS
    TableSpec(
        name="ods_mining_task",
        layer=Layer.ODS,
        domain=D,
        comment="挖掘任务（规则挖掘/抽帧/推理/向量化的统一任务记录）",
        source_system="数据挖掘平台 MySQL",
        bucket=4,
        primary_key=("mining_task_id",),
        columns=[
            C("mining_task_id", "STRING", "挖掘任务 ID", nullable=False),
            C("task_name", "STRING", "任务名称"),
            C(
                "task_type",
                "STRING",
                "任务类型：rule_mining/frame_extract/vlm_infer/embedding/vector_search",
            ),
            C("rule_id", "STRING", "关联规则 ID（规则挖掘类任务）"),
            C("project_code", "STRING", "所属项目"),
            C("exec_mode", "STRING", "执行模式：batch（T+1 Spark SQL）/streaming（Flink 准实时）"),
            C("scan_start_time", "TIMESTAMP(3)", "扫描范围起（增量水位下界）"),
            C("scan_end_time", "TIMESTAMP(3)", "扫描范围止（增量水位上界）"),
            C("scan_scope_json", "STRING", "扫描范围附加条件（JSON：项目/车辆/时段）"),
            C("task_status", "STRING", "任务状态：pending/running/success/failed/canceled"),
            C("hit_count", "BIGINT", "命中数量"),
            C("tag_write_count", "BIGINT", "写入标签量"),
            C("submit_user", "STRING", "提交人"),
            C("start_time", "TIMESTAMP(3)", "开始时间"),
            C("end_time", "TIMESTAMP(3)", "结束时间"),
        ],
    ),
    TableSpec(
        name="ods_mining_rule_config",
        layer=Layer.ODS,
        domain=D,
        comment="挖掘规则配置（「规则即数据」：经 Flink CDC 实时同步入湖，可查可追溯可审计）",
        source_system="数据挖掘平台 MySQL（规则表经 Flink CDC 实时同步）",
        bucket=4,
        primary_key=("rule_id",),
        notes=(
            "规则不是散落在代码里的 if-else，而是与业务数据同等的湖仓资产——谁在什么时候改了什么规则一查便知。"
            "本表是 mining 引擎的读入口（mining.tables.RULE_CONFIG_COLUMNS 直接拼进 SELECT 列表），"
            "故 [S3-04] 一「全生命周期：创建/修改/禁用」的三个时刻齐备："
            "create_time / last_modify_time / disable_time，配合 create_user / last_modify_user / owner 三个人。"
            "近义异名归一见模块 docstring：rule_type→rule_category、execution_mode→exec_mode、"
            "expression_mode→express_mode、sql_condition→rule_sql、visual_config_json→rule_condition_json、"
            "scene_label→target_tag_id"
        ),
        columns=[
            C("rule_id", "STRING", "规则 ID", nullable=False),
            C("rule_name", "STRING", "规则名称"),
            C(
                "rule_category",
                "STRING",
                "规则种类（[S3-04] 二的六大种类，取值以 mining.rules.RuleType 为准）："
                "tag_combination 标签组合/spatiotemporal 时空地理/vehicle_signal 车辆信号/"
                "model_output 模型输出/event_trigger 事件触发/composite 多条件复合",
            ),
            C("rule_version", "STRING", "规则版本（变更全程留痕）"),
            C(
                "rule_priority",
                "INT",
                "规则优先级：不只排序——直接决定 Embedding 与存储分级，高优先级命中数据优先进向量化队列",
            ),
            C(
                "express_mode",
                "STRING",
                "表达方式：sql 工程师写 SQL/visual 业务同学拖配置，两者等价",
            ),
            C("rule_sql", "STRING", "SQL 条件表达式（复杂规则可挂自定义 UDF）"),
            C(
                "rule_condition_json",
                "STRING",
                "可视化配置条件（JSON：标签/GPS 围栏/时间/传感器信号/模型输出）",
            ),
            C(
                "exec_mode",
                "STRING",
                "执行模式（取值以 mining.rules.ExecutionMode 为准）：batch_t_plus_1（静态标签与时空条件走 T+1 "
                "Spark SQL）/near_realtime（车辆信号与事件流走 Flink 准实时）",
            ),
            C("schedule_cron", "STRING", "批模式调度表达式"),
            C("event_window_before_sec", "INT", "事件窗口前置秒数（默认 15 秒，还原事件如何发生）"),
            C("event_window_after_sec", "INT", "事件窗口后置秒数（默认 5 秒，确认事件后果）"),
            C(
                "target_tag_id",
                "STRING",
                "命中后统一经标签服务打标的目标 tag_id（已过字典归一）——"
                "mining 引擎的 scene_label 就写在这一列，缺口评估也按它对齐 dwd_scene_gap_detail.tag_id",
            ),
            # ---- 缺口驱动：规则自带 ODD 目标量 ----
            C(
                "target_clip_count",
                "BIGINT",
                "本规则对应场景的目标 clip 数（ODD 覆盖目标）。[S3-01] 一「挖掘双出口」的分母："
                "库内命中够数就零采集成本回补，不够才按缺口下发定向采集需求",
            ),
            C("project_code", "STRING", "适用项目"),
            C(
                "rule_status",
                "STRING",
                "规则状态（取值以 mining.rules.RuleStatus 为准）：draft/enabled/disabled/archived",
            ),
            C("create_user", "STRING", "创建人"),
            C("last_modify_user", "STRING", "最近修改人"),
            C(
                "owner",
                "STRING",
                "规则责任人：对规则效果与停用决策负责，与 create_user（谁建的）/last_modify_user（谁最后改的）"
                "是三个不同职责。⚠️ 原文未明确，本项目设计——规则即数据就得有 data owner",
            ),
            C("create_time", "TIMESTAMP(3)", "创建时间（生命周期三时刻之一：创建）"),
            C(
                "last_modify_time",
                "TIMESTAMP(3)",
                "最近修改时间（生命周期三时刻之二：修改）。与既有 last_modify_user 配对——"
                "原本只记了「谁改的」没记「什么时候改的」，规则变更留痕不完整。"
                "依据 [S3-04] 一「规则的创建、修改、禁用全生命周期都有记录」",
            ),
            C(
                "disable_time",
                "TIMESTAMP(3)",
                "禁用时刻（生命周期三时刻之三：禁用）。rule_status=disabled 时必填，"
                "让「这条规则是什么时候停的、停之前命中了多少」可追溯。依据 [S3-04] 一",
            ),
        ],
    ),
    # ------------------------------------------------------------------ DWD
    TableSpec(
        name="dwd_mining_task_detail",
        layer=Layer.DWD,
        domain=D,
        comment="挖掘任务明细（规则执行追溯：扫描范围/命中数量/写入标签量可度量，而不是配完就黑盒）",
        bucket=4,
        primary_key=("mining_task_id",),
        notes=(
            "任务级粒度不挂 data_id（一次任务命中多个 clip），clip 级命中落 dwd_mining_result_detail。"
            "[S3-04] 一「执行追溯」要求记录的四项在本表齐备：执行时间（start_time/end_time/duration_sec）、"
            "扫描范围（scan_start_time/scan_end_time/scan_row_count）、命中数量（hit_data_count）、"
            "写入标签量（tag_write_count）；失败原因与 SLA 判定本次补齐。"
            "近义异名归一见模块 docstring：task_id→mining_task_id、executed_at→start_time、"
            "finished_at→end_time、elapsed_seconds→duration_sec、scan_low/high_watermark→scan_start/end_time、"
            "scanned_row_count→scan_row_count、hit_count→hit_data_count、tag_written_count→tag_write_count"
        ),
        columns=[
            C("mining_task_id", "STRING", "挖掘任务 ID", nullable=False),
            C("run_id", "STRING", "三级 ID：本次挖掘运行 ID（run_{stage}_{yyyyMMddHHmmss}_{seq}）"),
            C("rule_id", "STRING", "关联规则 ID"),
            C("rule_version", "STRING", "执行时的规则版本快照"),
            C("rule_category", "STRING", "规则种类（六大种类之一，取值见 mining.rules.RuleType）"),
            C("rule_priority", "INT", "规则优先级（驱动下游向量化与存储分级）"),
            C("task_type", "STRING", "任务类型：rule_mining/frame_extract/vlm_infer/embedding"),
            C(
                "exec_mode",
                "STRING",
                "执行模式：batch_t_plus_1/near_realtime（取值见 mining.rules.ExecutionMode）",
            ),
            C("engine", "STRING", "执行引擎：spark/flink/ray"),
            C("project_code", "STRING", "所属项目"),
            C(
                "scan_start_time",
                "TIMESTAMP(3)",
                "增量扫描水位下界（按 _ingest_time/update_time 推进，避免全表回扫）",
            ),
            C("scan_end_time", "TIMESTAMP(3)", "增量扫描水位上界"),
            C("scan_row_count", "BIGINT", "扫描行数"),
            C("hit_data_count", "BIGINT", "命中 clip 数"),
            C("hit_image_count", "BIGINT", "命中图片数"),
            C("tag_write_count", "BIGINT", "经统一标签服务写入的标签数"),
            C(
                "frame_supplement_triggered",
                "BOOLEAN",
                "是否异步触发事件补抽帧（规则结果 → 抽帧引擎解耦联动）",
            ),
            C("task_status", "STRING", "任务状态：pending/running/success/failed/skipped/canceled"),
            C("duration_sec", "DOUBLE", "执行耗时（秒）"),
            C("start_time", "TIMESTAMP(3)", "开始时间（[S3-04] 一「执行时间」）"),
            # ---- 执行追溯补齐：没有结束时刻与失败原因，追溯只能追一半 ----
            C(
                "end_time",
                "TIMESTAMP(3)",
                "结束时刻。原本只有 start_time + duration_sec，跨天任务与并发排队要反推结束点，"
                "对齐 executor.RuleRunRecord.finished_at。依据 [S3-04] 一「执行追溯」",
            ),
            C(
                "error_message",
                "STRING",
                "失败原因（截断至 2000 字符，见 executor.RuleRunRecord.finish）。"
                "task_status=failed 而不记原因，重跑前得翻作业日志——追溯表就白建了",
            ),
            C(
                "sla_breached",
                "BOOLEAN",
                "是否突破批模式 SLA。[S3-04] 三承诺「亿级以下数据 4 小时内跑完」，"
                "超时即置 true 供容量规划复盘（判定见 mining.constants.BATCH_SLA_SECONDS）",
            ),
        ],
    ),
    TableSpec(
        name="dwd_mining_result_detail",
        layer=Layer.DWD,
        domain=D,
        comment="挖掘结果明细（规则/模型/检索命中的 clip 级结果，供回补闭环消费）",
        bucket=8,
        primary_key=("mining_task_id", "data_id"),
        notes=(
            "复合主键表达完整粒度「一次任务 × 一个命中 clip」；同任务重跑按 data_id Upsert 覆盖为最新结果，"
            "run_id 记录最近一次执行。挂 data_id 让「命中场景 → 原始 clip」是一次主键查询。"
            "本表是 mining 引擎 INSERT INTO 的目标（compiler._RESULT_INSERT_COLUMNS 逐列对位），"
            "本次补齐二级 ID 血缘（artifact_id/parent_artifact_id/artifact_status）、"
            "高价值分级（value_score/value_tier/vectorize_policy）与四个跨域公共键。"
            "近义异名归一见模块 docstring：window_start/end_time→event_window_start/end_time、"
            "scene_label→matched_tag_id、backfill_dataset_id→consumed_dataset_id、rule_type→rule_category"
        ),
        columns=[
            C("mining_task_id", "STRING", "挖掘任务 ID", nullable=False),
            C("data_id", "STRING", "一级 ID：命中的 clip 级终身锚点", nullable=False),
            C(
                "result_id",
                "STRING",
                "本条命中的行 ID = CONCAT(run_id, '_', data_id)，确定性派生故重跑幂等"
                "（见 compiler._render_batch_select）。主键是 (mining_task_id, data_id)，"
                "result_id 供下游按「哪一次运行产出的这条命中」引用，不参与 Upsert",
            ),
            C("run_id", "STRING", "三级 ID：产出本条命中的运行 ID"),
            # ---- 二级 ID 血缘：原本整条断链，命中结果无法回指产物 ----
            C(
                "artifact_id",
                "STRING",
                "二级 ID：本次命中作为处理产物的 ID（ids.derive_artifact_id(data_id, stage='mining', ...)）。"
                "缺了它，命中结果既接不上上游抽帧产物也接不上下游向量产物，产物血缘在挖掘这一环断掉",
            ),
            C(
                "parent_artifact_id",
                "STRING",
                "血缘父产物（被扫描的 clip 产物），冗余落表供图库对账兜底",
            ),
            C(
                "artifact_status",
                "STRING",
                "产物状态：active/superseded/invalid（重刷不覆盖，旧产物标 superseded）",
            ),
            C(
                "rule_id",
                "STRING",
                "命中规则 ID（携带 rule_id 血缘，标签自动继承字典映射与审核体系）",
            ),
            C("rule_version", "STRING", "命中时的规则版本"),
            C("rule_category", "STRING", "规则种类（六大种类之一，取值见 mining.rules.RuleType）"),
            C("rule_priority", "INT", "规则优先级（决定是否优先进向量化队列）"),
            C(
                "exec_mode",
                "STRING",
                "产出本条命中的执行模式：batch_t_plus_1/near_realtime。"
                "批流双写同一张表，不记模式则「这条是批补的还是流出的」无从分辨，"
                "补数与对账都要按它切分（[S3-04] 三、批流双模·结果双写）",
            ),
            C("hit_type", "STRING", "命中来源：rule 规则粗筛/vlm 模型细筛/vector_search 检索扩散"),
            C("hit_time", "TIMESTAMP(3)", "命中时间"),
            C("hit_reason", "STRING", "命中原因描述（如「CAN 减速度 < -4m/s² 持续 ≥ 0.5s」）"),
            C("hit_score", "DOUBLE", "命中打分/置信度（规则命中为 1.0，模型命中为模型置信度）"),
            C(
                "matched_tag_id",
                "STRING",
                "命中后统一打标写入的 tag_id（mining 引擎的 scene_label 写在这一列）",
            ),
            # ---- 高价值分级：驱动向量化队列与存储分级 ----
            C(
                "value_score",
                "DOUBLE",
                "高价值综合评分（规则优先级 + 稀缺度 + 时效性多因子加权，见 mining.scoring）。"
                "与 hit_score 不是一回事：hit_score 答「命中得准不准」，value_score 答「这条值不值得花 GPU」",
            ),
            C(
                "value_tier",
                "STRING",
                "评分分档：S/A/B/C（value_score 的分段函数，见 mining.scoring.ValueTier）。"
                "分档在 SQL 外层算，避免打分表达式被复制三遍",
            ),
            C(
                "vectorize_policy",
                "STRING",
                "向量化策略：priority_vectorize 优先进队列/sampled 按比例抽样。"
                "[S3-01] 五 + [S3-04] 一：规则优先级直接决定 Embedding 与存储分级，不是所有命中都全量向量化",
            ),
            C("event_time", "TIMESTAMP(3)", "事件发生时刻（事件触发类规则）"),
            C("event_window_start_time", "TIMESTAMP(3)", "事件窗口起（事件前 15 秒）"),
            C("event_window_end_time", "TIMESTAMP(3)", "事件窗口止（事件后 5 秒）"),
            C(
                "frame_supplement_status",
                "STRING",
                "补抽帧状态：pending/running/done/skipped（异步补抽，不阻塞主链路）",
            ),
            C("vehicle_code", "STRING", "车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C(
                "consumed_dataset_id",
                "STRING",
                "回补闭环消费的目标数据集 ID（[S3-01] 六：命中结果凭它回补训练集）",
            ),
            # ---- 跨域公共键：闭环归因靠它们把「命中」接回数据集/模型/评测/回传 ----
            C("dataset_id", "STRING", "命中 clip 当前所属数据集 ID（跨域公共键，接数据集域）"),
            C("dataset_version", "STRING", "数据集版本（跨域公共键，与 dataset_id 成对使用）"),
            C(
                "model_version",
                "STRING",
                "相关模型版本（跨域公共键：模型细筛命中时即产出该结果的 VLM/感知模型版本）",
            ),
            C(
                "evaluation_type",
                "STRING",
                "评测类型（跨域公共键，接评测域）：offline/simulation/real_vehicle",
            ),
            C(
                "trigger_type",
                "STRING",
                "回传触发类型（跨域公共键，与 ods_vehicle_trigger_event.trigger_type 同名同义同取值域）："
                "takeover/aeb/hard_brake/rule_hit/corner_case。事件触发类规则的命中由它回指源触发事件",
            ),
        ],
    ),
    TableSpec(
        name="dwd_scene_gap_detail",
        layer=Layer.DWD,
        name_omits_domain=True,
        domain=D,
        comment="场景缺口清单（标签覆盖率对照 ODD 目标，缺口反过来驱动定向采集）",
        bucket=4,
        primary_key=("project_code", "tag_id"),
        notes=(
            "复合主键表达完整粒度「一个项目 × 一个场景标签」的当前缺口快照，随覆盖度日指标 Upsert 刷新。"
            "本表是跨 clip 的聚合缺口清单，**刻意不挂 data_id**——一条缺口对应的是一批 clip 的缺失，"
            "挂上去只会恒为 NULL（同 controlplane.contracts 对任务级粒度的判断）；"
            "命中回补的 clip 级明细在 dwd_mining_result_detail，按 (project_code, tag_id) 关联即可。"
            "本次补齐的是产物血缘那一路：gap_id/run_id/artifact_id/parent_artifact_id/artifact_status——"
            "一次缺口评估就是一次运行、一份产物，没有这几列就说不清「这份缺口清单是谁、什么时候、按哪版规则算的」。"
            "近义异名归一见模块 docstring：scene_label→tag_id、hit_clip_count→current_clip_count、"
            "evaluated_at→last_eval_time、backfill_dataset_id→consumed_dataset_id"
        ),
        columns=[
            C("project_code", "STRING", "所属项目", nullable=False),
            C(
                "tag_id",
                "STRING",
                "场景标签 ID（对齐统一标签字典；mining 引擎的 scene_label 写在这一列）",
                nullable=False,
            ),
            C(
                "gap_id",
                "STRING",
                "缺口记录 ID（一次评估 × 一个场景一条，见 mining.gaps.SceneGap）。"
                "主键是业务粒度 (project_code, tag_id)，gap_id 供采集需求单与告警回指这一条具体缺口",
            ),
            C("run_id", "STRING", "三级 ID：算出本条缺口的那次缺口评估运行"),
            C("artifact_id", "STRING", "二级 ID：本条缺口作为评估产物的 ID"),
            C(
                "parent_artifact_id",
                "STRING",
                "血缘父产物（本次评估消费的挖掘结果产物），图库对账兜底",
            ),
            C(
                "artifact_status",
                "STRING",
                "产物状态：active/superseded/invalid——重算不覆盖，旧快照标 superseded",
            ),
            C(
                "rule_id",
                "STRING",
                "血缘：产出本条缺口的挖掘规则 ID（规则自带 target_clip_count 目标量）。"
                "与下面 suggest_rule_id 不同——那是「建议再触发哪条规则去补」，这是「这条缺口是谁算出来的」",
            ),
            C("rule_version", "STRING", "血缘：评估时的规则版本快照"),
            C("tag_name", "STRING", "场景标签名称"),
            C("tag_category", "STRING", "标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR"),
            C("odd_dimension", "STRING", "对应 ODD 运行设计域要素维度"),
            C(
                "target_clip_count",
                "BIGINT",
                "目标 clip 数（ODD 覆盖目标，取自规则的 target_clip_count）",
            ),
            C(
                "current_clip_count",
                "BIGINT",
                "当前已有（库内命中）clip 数——零采集成本可直接回补的那部分",
            ),
            C("current_image_count", "BIGINT", "当前已有图片数"),
            C("gap_clip_count", "BIGINT", "缺口 clip 数 = max(0, 目标 - 当前)"),
            C("coverage_ratio", "DOUBLE", "当前覆盖率 = 当前 / 目标"),
            C(
                "gap_level",
                "STRING",
                "缺口等级：critical/high/medium/low（gap_severity 的分档，给人看）",
            ),
            C(
                "gap_severity",
                "DOUBLE",
                "缺口严重度评分（连续值，见 mining.gaps.gap_severity）：按缺口量 × 规则优先级加权。"
                "gap_level 是它的分档；排序下发定向采集要用连续值，分档只有四级排不动序",
            ),
            C(
                "suggest_action",
                "STRING",
                "建议动作：directed_collect 定向采集/rule_mining 规则挖掘/simulation 仿真生成",
            ),
            C("suggest_rule_id", "STRING", "建议触发的挖掘规则 ID"),
            C(
                "consumed_dataset_id",
                "STRING",
                "命中部分回补进的目标数据集 ID（[S3-01] 一、六：库内命中直接回补训练集，零采集成本）。"
                "与 dwd_mining_result_detail 同名同义",
            ),
            C(
                "collect_demand_id",
                "STRING",
                "缺口部分下发的定向采集**需求单** ID（见 mining.gaps.CollectDemand）。"
                "与下面 related_collect_task_id 是需求与受理两端：需求由挖掘侧生成，任务由采集系统受理后回填，"
                "两者都留才能度量「下发了多少 / 真正排产了多少」",
            ),
            C("related_collect_task_id", "STRING", "采集系统受理该需求后生成的定向采集任务 ID"),
            C(
                "gap_status",
                "STRING",
                "缺口状态：satisfied 已满足/partial 部分满足/missing 完全缺失（另有 open/collecting/closed 流转态）",
            ),
            C("last_eval_time", "TIMESTAMP(3)", "最近一次缺口评估时间"),
            C("stat_date", "DATE", "统计日期"),
        ],
    ),
    TableSpec(
        name="dwd_mining_image_frame_detail",
        layer=Layer.DWD,
        domain=D,
        comment="抽帧图片明细（三级分层抽帧的唯一产物表：常规普查/事件勘查/推理取证三路统一落表）",
        bucket=16,
        primary_key=("image_id",),
        notes=(
            "亿级行 + 批（Spark 回刷存量）流（Flink 实时抽帧）双模写入，并发最高，取 bucket 16。"
            "image_id 内嵌 data_id，免查表即可回溯采集单元；data_id 冗余落列供主键式回溯。"
            "本次并入 sampling 子系统的三类能力列：多路同步组（frame_group_id）、"
            "事件窗口三列（event_time/event_window_start_time/event_window_end_time，让事件抽帧的 20 秒窗口可复盘）、"
            "推理选帧的三维打分明细（object_richness_score/temporal_position_score/keyframe_score/is_keyframe，"
            "原文四章要求选帧「可重算复盘」，只留总分就复盘不了）。"
            "★ 近义异名归一见模块 docstring，其中 event_trigger_type→trigger_event_type 是字序颠倒的同义列，"
            "取值域与 ods_vehicle_trigger_event.trigger_type 同口径；"
            "另有 sampling_tier→extract_level、file_path→image_object_key、file_size_bytes→image_size_bytes、"
            "clip_offset_ms→frame_offset_sec（毫秒÷1000）、desensitization_status→desensitize_status"
        ),
        columns=[
            C(
                "image_id",
                "STRING",
                "图片级 ID（{data_id}_{camera_id}_F{帧序号:06d}），内嵌 data_id，免查表即可回溯采集单元",
                nullable=False,
            ),
            C("data_id", "STRING", "一级 ID：所属 clip 的终身锚点"),
            C("artifact_id", "STRING", "二级 ID：本帧作为抽帧产物的 ID"),
            C("parent_artifact_id", "STRING", "血缘父产物（原始视频文件产物），图库对账兜底"),
            C(
                "artifact_status",
                "STRING",
                "产物状态：active/superseded/invalid（重刷不覆盖，旧产物标 superseded）",
            ),
            C("run_id", "STRING", "三级 ID：产出本帧的抽帧运行 ID"),
            C(
                "extract_level",
                "STRING",
                "抽帧层级 = 产出本帧的成本闸门：routine 常规（2 秒 1 帧）/event 事件（1 秒 1 帧）/inference 推理选帧",
            ),
            C(
                "sampling_interval_sec",
                "DOUBLE",
                "产出本帧时该层实际用的抽帧间隔（秒）：常规默认 2 秒 1 帧、事件 1 秒 1 帧（原文二章闸门表）。"
                "间隔是可调参数，只记层级不记实际间隔，后面就算不出「这一批帧密度为什么不一样」",
            ),
            C(
                "camera_id",
                "STRING",
                "摄像头 ID（多路同步：同一时刻多路图片为一组样本，逐图保留视角）",
            ),
            C("camera_position", "STRING", "摄像头安装位置：front/left/right/rear"),
            C(
                "frame_group_id",
                "STRING",
                "多路同步组 ID：同一采集时刻的多路图片共用一个组 ID，作为一组样本进训练。"
                "原文五章「四个实现要点」之多路摄像头同步——缺它则多路图片只能靠时间戳容差事后拼组",
            ),
            C("frame_index", "INT", "帧序号（clip 内递增，原生 30fps 下的帧号，字典序即时间序）"),
            C("frame_timestamp", "TIMESTAMP(3)", "帧绝对时间戳"),
            C(
                "frame_offset_sec",
                "DOUBLE",
                "相对 clip 起点的时间偏移（秒；sampling 侧 clip_offset_ms 毫秒值 ÷ 1000 写入）",
            ),
            C(
                "frame_quality_score",
                "DOUBLE",
                "图像清晰度分：模糊/过曝/遮挡直接降权，推理抽帧选 1~5 关键帧的依据之一，可重算复盘",
            ),
            C(
                "object_richness_score",
                "DOUBLE",
                "目标丰富度分：画面里车辆/行人/交通设施越多分越高。原文四章选帧三维之二",
            ),
            C(
                "temporal_position_score",
                "DOUBLE",
                "时间位置分：事件窗口中心、场景切换时刻优先。原文四章选帧三维之三",
            ),
            C(
                "keyframe_score",
                "DOUBLE",
                "三维加权综合分 = 清晰度 × 丰富度 × 时间位置的加权和，推理抽帧的选帧依据。"
                "三个分项与总分都落表，换权重后可直接重算重新圈选，不必重跑推理",
            ),
            C(
                "is_keyframe",
                "BOOLEAN",
                "是否被推理抽帧选中为关键帧（每 clip 1~5 张）。推理抽帧不产新图、只在已落湖的帧里挑，"
                "所以选中与否是本列，而不是另一个 extract_level 取值",
            ),
            C(
                "trigger_event_type",
                "STRING",
                "触发事件类型（事件抽帧）：rule_hit/aeb(active_safety)/takeover(driver_takeover)/low_confidence，与 ods_vehicle_trigger_event.trigger_type 同口径",
            ),
            C("event_time", "TIMESTAMP(3)", "事件发生时刻（事件窗口中心），事件抽帧专用"),
            C(
                "event_window_start_time",
                "TIMESTAMP(3)",
                "事件窗口起点：事件前 15 秒（与 dwd_mining_result_detail 同名同义）",
            ),
            C("event_window_end_time", "TIMESTAMP(3)", "事件窗口终点：事件后 5 秒"),
            C("gps_lat", "DOUBLE", "抽帧时刻纬度"),
            C("gps_lon", "DOUBLE", "抽帧时刻经度"),
            C("image_object_key", "STRING", "图片对象存储 key（即 sampling 侧的 file_path）"),
            C("image_size_bytes", "BIGINT", "图片大小（字节）"),
            C(
                "image_width",
                "INT",
                "图像宽度（像素）——分辨率随车型与摄像头代际变化，训练侧要按它做尺寸对齐",
            ),
            C("image_height", "INT", "图像高度（像素）"),
            C(
                "desensitize_status",
                "STRING",
                "双脱敏校验状态：passed/pending/rejected——未脱敏一律拒绝抽帧",
            ),
            C(
                "algo_version",
                "STRING",
                "抽帧/打分算法版本。算法一变即产出新 artifact_id（旧帧标 superseded），"
                "没有这一列就无法解释「同一个 clip 为什么两次抽出的帧不一样」",
            ),
            C(
                "create_time",
                "TIMESTAMP(3)",
                "记录创建时间——向量化流水线第 ① 步增量识别的水位字段之一（另一个是 update_time）",
            ),
            C("project_code", "STRING", "所属项目"),
            C(
                "vehicle_code",
                "STRING",
                "车辆编码（检索与配额统计的常用过滤维度，与 clip 表同名同义）",
            ),
        ],
    ),
    TableSpec(
        name="dwd_mining_tag_dict_detail",
        layer=Layer.DWD,
        domain=D,
        comment="统一标签字典（五大类别受控词表，三来源标签收口的地基，防标签爆炸）",
        bucket=1,
        primary_key=("tag_id",),
        notes=(
            "受控词表仅千级行，取 bucket 1「字典表」档——这是全域唯一取 1 的 DWD 表。"
            "枚举参照华为云八爪鱼九大类 / 端到端世界模型三级标签 / ISO 34504·SOTIF 场景本体 / ODD 四套实践。"
            "本次并入 tags 子系统的字典治理能力列：tag_depth（层级深度）、tag_description（释义）、"
            "change_request_id（变更工单）、reviewer_secondary（复核人二）——"
            "「字典变更走工单 + 双人复核」这句话里，工单号与第二个人原本都没有落表的地方。"
            "近义异名归一见模块 docstring：applicable_sources→tag_source_type、"
            "mutex_group→mutual_exclusive_group、source_ontology→ontology_ref、reviewer_primary→review_operator"
        ),
        columns=[
            C("tag_id", "STRING", "标准标签 ID（全域唯一，别名一律归一到它）", nullable=False),
            C("tag_name", "STRING", "标签标准名称"),
            C("tag_name_en", "STRING", "标签英文名"),
            C(
                "tag_category",
                "STRING",
                "五大类别：SCENE 场景/ENV 环境/ROAD 道路/PARTICIPANT 参与者/BEHAVIOR 行为事件（另含 CAPTION 特殊类）",
            ),
            C("parent_tag_id", "STRING", "二级分类父标签 ID（类别下支持层级树）"),
            C("tag_path", "STRING", "标签层级路径，如 ENV/天气/大雨"),
            C(
                "tag_depth",
                "INT",
                "层级深度：1 类别 / 2 二级分类 / 3 三级标签。tag_path 已能算出深度，但按深度过滤"
                "（大屏只展开到二级、互斥判定只在同层做）是高频查询，物化成一列免去每次切字符串",
            ),
            C(
                "tag_level",
                "STRING",
                "适用粒度：clip/image/inheritable——clip 标签可自动继承到它抽出的每张图片",
            ),
            C(
                "alias_json",
                "STRING",
                "别名映射（JSON）：「雨天/降雨/rain/下雨天」全部指向同一 tag_id",
            ),
            C(
                "tag_status",
                "STRING",
                "四态状态机：candidate 候选/active 生效/deprecated 废弃/merged 合并",
            ),
            C("merged_into_tag_id", "STRING", "合并目标标签 ID（merged 态时原名保留为别名）"),
            C(
                "tag_source_type",
                "STRING",
                "允许写入该标签的来源：collect 采集/rule 规则/vlm 模型（可多选，逗号分隔；空表示三来源皆可）",
            ),
            C("odd_dimension", "STRING", "对应 ODD 运行设计域要素，支撑覆盖率统计与定向采集"),
            C(
                "ontology_ref",
                "STRING",
                "场景本体参照（ISO 34504 / SOTIF 条目号，或所参照的业界实践名）",
            ),
            C(
                "mutual_exclusive_group",
                "STRING",
                "同层互斥组（ISO 34504 / SOTIF 同层互斥原则：同组标签不可同时命中）",
            ),
            C(
                "tag_description",
                "STRING",
                "标签释义：这个标签到底指什么、边界在哪。受控词表防的是标签爆炸，"
                "而同名不同义是爆炸的第二种形态——没有释义，三个来源会各按各的理解打同一个 tag_id",
            ),
            C(
                "review_status",
                "STRING",
                "审核状态：pending/approved/rejected——字典变更走工单 + 双人复核",
            ),
            C(
                "change_request_id",
                "STRING",
                "引入/变更该标签的审核工单号（平台工单）。原文要求字典变更走工单，"
                "工单号不落表则「这个标签当初是凭什么加进来的」查不到",
            ),
            C(
                "review_operator",
                "STRING",
                "复核人一（原文：审核结论回写 review_status / review_operator）",
            ),
            C(
                "reviewer_secondary",
                "STRING",
                "复核人二。双人复核的前提是记下两个人——只留一个 review_operator，"
                "「谁也不能独自改字典」这条约束就无法事后验证",
            ),
            C("effective_from", "TIMESTAMP(3)", "生效时间"),
            C("deprecated_time", "TIMESTAMP(3)", "废弃时间（历史数据仍可按原标签回溯）"),
        ],
    ),
    TableSpec(
        name="dwd_mining_data_tag_detail",
        layer=Layer.DWD,
        domain=D,
        comment="数据级标签明细（clip 级，三来源标签管道的出口之一）",
        bucket=8,
        primary_key=("data_id", "tag_id", "tag_source"),
        notes=(
            "复合主键表达完整粒度「一个 clip × 一个标准标签 × 一个来源」，幂等去重靠它——"
            "重复写入无副作用，任务重跑不产生重复标签；标签变更只 Upsert 变更行，配合时间旅行可回溯任意时点。"
            "【bucket 裁决：4 → 8】本表行数 ≈ clip 数 × 人均标签数 × 来源数，是不折不扣的大体量明细表，"
            "对应 Bucket 五档的第四档「大体量明细表（DWD）」；原本的 4 是第三档「中等体量 ODS/DWD」，"
            "明显低配——同域按 clip 粒度、且只有一行一 clip 的 dwd_mining_result_detail 就已经是 8，"
            "本表在它之上再乘标签数与来源数，不可能更小。故采信子系统的 8。"
            "本次并入产物血缘（artifact_id/parent_artifact_id/artifact_status）与"
            "字典映射留痕（source_raw_tag/mapping_type/mapping_note/conflict_resolution）——"
            "三来源收口的全部判断依据。"
            "近义异名归一见模块 docstring：tag_time→first_tag_time、valid_flag→tag_status"
        ),
        columns=[
            C("data_id", "STRING", "一级 ID：clip 级终身锚点", nullable=False),
            C("tag_id", "STRING", "标准标签 ID（已过字典映射与别名归一）", nullable=False),
            C("tag_source", "STRING", "标签来源：collect 采集/rule 规则/vlm 模型", nullable=False),
            C("tag_name", "STRING", "标准标签名（冗余自字典，检索与导出免回查字典表）"),
            C("tag_category", "STRING", "标签类别（冗余自字典，免 JOIN 过滤）"),
            C(
                "tag_level",
                "STRING",
                "适用粒度（冗余自字典）：clip/image/inheritable——继承作业据此决定要不要下发到图片级",
            ),
            C("tag_value", "STRING", "标签取值（枚举型标签的具体值，如 rain_level=heavy）"),
            C("confidence", "DOUBLE", "置信度（模型标签必填，人工/规则标签为 1.0）"),
            C("rule_id", "STRING", "血缘：产出该标签的规则 ID"),
            C("rule_version", "STRING", "血缘：规则版本"),
            C("model_name", "STRING", "血缘：VLM 模型名"),
            C("model_version", "STRING", "血缘：模型版本"),
            C("infer_job_id", "STRING", "血缘：推理作业 ID"),
            C("run_id", "STRING", "三级 ID：写入本条标签的运行 ID"),
            C(
                "artifact_id",
                "STRING",
                "二级 ID：本条标签作为产物的 ID（{data_id}_tag_{algo_version}_{hash}）",
            ),
            C(
                "parent_artifact_id",
                "STRING",
                "血缘父产物（被打标的 clip 产物），冗余落表供图库对账兜底",
            ),
            C("artifact_status", "STRING", "产物状态：active/superseded/invalid"),
            # ---- 三来源收口：归一与裁决的判断依据必须留痕，否则「为什么是这个标签」说不清 ----
            C(
                "source_raw_tag",
                "STRING",
                "来源系统的原始写法，字典归一前的原样留痕（如采集端写的「下雨天」）。"
                "没有它，别名归一就是一次不可逆的信息丢失，出错时无从对账",
            ),
            C(
                "mapping_type",
                "STRING",
                "字典映射结果：canonical 直接命中标准名/alias 经别名归一/merged_redirect 经 merged 态重定向。"
                "三种路径的可信度不同，审核抽检按它分层",
            ),
            C("mapping_note", "STRING", "映射判定说明（命中了哪条别名、走了哪次合并），排障用"),
            C(
                "conflict_resolution",
                "STRING",
                "互斥冲突裁决留痕，格式 kind:role:rule:reason。同层互斥组内两个标签同时命中时，"
                "落败方置 tag_status=invalid 但不删除——裁决理由记在本列，可复核可翻案",
            ),
            C(
                "review_status",
                "STRING",
                "审核状态：unreviewed(pending)/approved/rejected/corrected——未审核标签不得进入训练集圈选",
            ),
            C("review_operator", "STRING", "审核人"),
            C("review_time", "TIMESTAMP(3)", "审核时间"),
            C(
                "tag_status",
                "STRING",
                "标签事实状态：active/invalid（互斥裁决落败置 invalid，即 tags 侧的 valid_flag=false）",
            ),
            C("vehicle_code", "STRING", "车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C(
                "first_tag_time",
                "TIMESTAMP(3)",
                "首次打标时间（最近一次打标由系统字段 update_time 承载）",
            ),
        ],
    ),
    TableSpec(
        name="dwd_mining_image_tag_detail",
        layer=Layer.DWD,
        domain=D,
        comment="图片级标签明细（含 VLM caption 特殊标签，三来源标签管道的出口之二）",
        bucket=16,
        primary_key=("image_id", "tag_id", "tag_source"),
        notes=(
            "复合主键表达完整粒度「一张图 × 一个标准标签 × 一个来源」，与 clip 级表同一套幂等去重口径。"
            "caption 以 tag_category=CAPTION 的特殊标签写入本表，并冗余一份到向量表——"
            "结构化过滤与语义检索共用同一份说明，不用两套维护。"
            "【bucket 裁决：8 → 16】行数 ≈ 图片数（= clip 数 × 每 clip 抽帧数）× 标签数，"
            "且 clip 标签会自动继承到它抽出的每一张图片，写入放大全域最严重，"
            "对应 Bucket 五档第五档「超大表 / 高并发写入（DWD）」。"
            "同域两张图片粒度的表（dwd_mining_image_frame_detail、dwd_mining_image_vector_detail）都已取 16，"
            "本表在帧表之上再乘标签数，只可能更大不可能更小，原本的 8 属低配。故采信子系统的 16。"
            "本次并入内容与 clip 级表对齐（产物血缘 + 字典映射留痕 + 冗余维度），"
            "并补齐 project_code/vehicle_code/rule_version——clip 级表有而本表没有，同一套查询在图片级就得多一次 JOIN。"
            "近义异名归一见模块 docstring：tag_time→first_tag_time、valid_flag→tag_status、"
            "inherited_from_clip→inherited_from_data_tag"
        ),
        columns=[
            C("image_id", "STRING", "图片级 ID", nullable=False),
            C("tag_id", "STRING", "标准标签 ID（已过字典映射与别名归一）", nullable=False),
            C("tag_source", "STRING", "标签来源：collect 采集/rule 规则/vlm 模型", nullable=False),
            C(
                "data_id",
                "STRING",
                "一级 ID：所属 clip，让「Badcase 图片 → 原始 clip」是一次主键查询",
            ),
            C("tag_name", "STRING", "标准标签名（冗余自字典，免回查）"),
            C("tag_category", "STRING", "标签类别（五大类别 + CAPTION 特殊类）"),
            C("tag_level", "STRING", "适用粒度（冗余自字典）：clip/image/inheritable"),
            C("caption_text", "STRING", "VLM 生成的关键说明（tag_category=CAPTION 时写入）"),
            C("confidence", "DOUBLE", "置信度"),
            C(
                "inherited_from_data_tag",
                "BOOLEAN",
                "是否由 clip 标签自动继承而来（字典 tag_level=inheritable）",
            ),
            C("bbox_json", "STRING", "目标框（JSON）：目标级标签的画面位置"),
            C("rule_id", "STRING", "血缘：产出该标签的规则 ID"),
            C(
                "rule_version",
                "STRING",
                "血缘：规则版本——规则标签跟着规则版本走，与 clip 级表同口径",
            ),
            C("model_name", "STRING", "血缘：VLM 模型名"),
            C("model_version", "STRING", "血缘：模型版本"),
            C("infer_job_id", "STRING", "血缘：推理作业 ID"),
            C("run_id", "STRING", "三级 ID：写入本条标签的运行 ID"),
            C("artifact_id", "STRING", "二级 ID：本条标签作为产物的 ID"),
            C("parent_artifact_id", "STRING", "血缘父产物（被打标的抽帧图片产物），图库对账兜底"),
            C("artifact_status", "STRING", "产物状态：active/superseded/invalid"),
            C("camera_id", "STRING", "摄像头 ID（检索时可按视角过滤）"),
            C("source_raw_tag", "STRING", "来源系统的原始写法，字典归一前留痕，可回溯"),
            C("mapping_type", "STRING", "字典映射结果：canonical/alias/merged_redirect"),
            C("mapping_note", "STRING", "映射判定说明，排障用"),
            C(
                "conflict_resolution",
                "STRING",
                "互斥冲突裁决留痕：kind:role:rule:reason（落败方置 tag_status=invalid 但不删除）",
            ),
            C(
                "review_status",
                "STRING",
                "审核状态：unreviewed(pending)/approved/rejected/corrected——模型产出先过审再上岗",
            ),
            C("review_operator", "STRING", "审核人"),
            C("review_time", "TIMESTAMP(3)", "审核时间"),
            C("tag_status", "STRING", "标签事实状态：active/invalid（即 tags 侧的 valid_flag）"),
            C(
                "project_code",
                "STRING",
                "所属项目（与 clip 级表对齐，覆盖度统计按项目分组免 JOIN）",
            ),
            C("vehicle_code", "STRING", "车辆编码（与 clip 级表对齐）"),
            C(
                "first_tag_time",
                "TIMESTAMP(3)",
                "首次打标时间（最近一次打标由系统字段 update_time 承载）",
            ),
        ],
    ),
    TableSpec(
        name="dwd_mining_image_vector_detail",
        layer=Layer.DWD,
        domain=D,
        comment="图片向量明细（全湖体量最大：千万~亿级行 × 高维向量；HNSW 索引建在其 StarRocks 外部表上）",
        bucket=16,
        partition_by=("dt",),
        primary_key=("image_id", "embedding_version", "dt"),
        extra_options={
            # a9：含 VARIANT 列（vector_meta）的表，数据文件必须是 Parquet
            "file.format": "parquet",
        },
        notes=(
            "[a12] 原文三处点名本表：第三章分区决策规则一的代表表（千万~亿级，按 dt 分区支撑"
            "降冷与向量索引增量刷新）、第四章 Bucket 五档表 16 档「超大表 / 高并发写入」的代表表"
            "（与 dwd_data_production_chain 并列）、第五章主键原则三的示例"
            "（PK=(image_id, embedding_version, dt)，分区表主键必须含分区字段）。"
            "分区规则一：大体量 + 时间范围查询 → 按 dt 分区。两个目的——生命周期降冷（历史向量归档）"
            "与索引分区级增量刷新（只刷新新增分区，千万级全量索引不用每天重建）。"
            "embedding_version 入主键让模型换代时新旧向量并存，灰度切换与一键回滚都不需要重写数据；"
            "dt 入主键是 Paimon 对分区表的硬要求。向量本体只存一份，Paimon 保持单一事实源，"
            "StarRocks 只提供检索加速（P95 ≤ 2s 不达标则降级为内表冗余，检索 API 零感知）。"
            "本次并入原文第五章第 ② 步「标量预过滤」要用的一整组过滤列（时间/GPS/地理网格/城市/"
            "天气/光照/道路/场景标签/摄像头位置）——检索是「向量召回 + 标量过滤」两条腿，"
            "过滤列不在表里，vector.search 的过滤器白名单就会编译出引用不存在列的 SQL；"
            "另并入四个跨域公共键（project_code/vehicle_code/dataset_id/dataset_version/model_version）"
            "与半结构化元数据列 vector_meta。"
            "vector_meta 为 VARIANT，故 extra_options 固定 file.format=parquet（a9 硬要求）；"
            "热路径的 variant.shreddingSchema 由向量子系统在建表时按 vector.variant 追加——"
            "catalog 是最底层契约，不反向依赖子系统。"
            "近义异名归一见模块 docstring：caption→caption_text、encoded_at→embed_time"
        ),
        columns=[
            C("image_id", "STRING", "图片级 ID", nullable=False),
            C(
                "embedding_version",
                "STRING",
                "Embedding 模型版本，入主键实现新旧向量并存",
                nullable=False,
            ),
            C("dt", "STRING", "分区字段：向量化日期（yyyy-MM-dd）", nullable=False),
            C("data_id", "STRING", "一级 ID：所属 clip，检索命中后回补元数据的关联键"),
            C("image_embedding", "ARRAY<FLOAT>", "图片向量（CLIP 图像塔输出）"),
            C(
                "text_embedding",
                "ARRAY<FLOAT>",
                "文本向量（CLIP 文本塔输出，与图片向量同空间，图文双向量同行）",
            ),
            C(
                "caption_text",
                "STRING",
                "caption 冗余（与图片标签表同一份说明，结构化过滤与语义检索共用），text_embedding 的编码输入",
            ),
            C("embedding_dim", "INT", "向量维度，必须与 HNSW 索引的 dim 属性一致"),
            C("model_name", "STRING", "Embedding 模型名（图文双塔同模型）"),
            C(
                "model_version",
                "STRING",
                "产出向量的 CLIP 模型版本（跨域公共键；与 embedding_version 一一对应，前者对外后者对内）",
            ),
            C("vector_status", "STRING", "向量状态：active/deprecated——检索默认只查 active 版本"),
            C(
                "similarity_metric",
                "STRING",
                "相似度度量：cosine（HNSW 图文双索引均采用余弦相似度）",
            ),
            C("index_refresh_status", "STRING", "当日分区索引刷新状态：pending/refreshing/done"),
            C(
                "cost_tier",
                "STRING",
                "成本分级：full/high_value 高价值全量处理，sample/normal 普通数据按比例抽样",
            ),
            C("rule_priority", "INT", "触发向量化的规则优先级（高优先级命中数据优先进向量化队列）"),
            # ---- 标量预过滤维度（原文第五章第 ② 步：时间 / GPS / 摄像头 / 标签）----
            C(
                "capture_time",
                "TIMESTAMP(3)",
                "图片采集时间——标量预过滤最常用的维度（「上个月的」「最近一周的」）",
            ),
            C("camera_id", "STRING", "摄像头 ID（标量预过滤维度之一）"),
            C(
                "camera_position",
                "STRING",
                "摄像头安装位置：front/rear/left/right——按视角过滤比按 camera_id 更贴近业务问法",
            ),
            C("gps_lat", "DOUBLE", "纬度（标量预过滤：范围框选）"),
            C("gps_lon", "DOUBLE", "经度（标量预过滤：范围框选）"),
            C(
                "geo_grid",
                "STRING",
                "地理网格编码（标量预过滤：区域等值过滤）。经纬度范围过滤要扫两列做双向比较，"
                "网格码一次等值命中，千万级下这点差异直接决定 P95 能不能压进 2 秒",
            ),
            C("city_code", "STRING", "城市编码（标量预过滤：按城市圈选）"),
            C(
                "weather",
                "STRING",
                "天气标签：rain/snow/fog/clear 等（高选择率，从 vector_meta 提升为正式列）",
            ),
            C(
                "light_condition",
                "STRING",
                "光照条件：day/night/dusk/dawn（高选择率，提升为正式列）",
            ),
            C("road_type", "STRING", "道路类型：highway/urban/rural（高选择率，提升为正式列）"),
            C("scene_tag", "STRING", "主场景标签（高选择率，提升为正式列；全集在 vector_meta 里）"),
            C(
                "vector_meta",
                "VARIANT",
                "半结构化向量元数据：场景标签全集 / 感知事件摘要 / 模型调试属性（a9）。"
                "标签维度按项目按车型持续增生，全部提列会让表宽度失控；VARIANT 承接长尾，"
                "热路径再按 shredding schema 物化成带类型子列",
            ),
            # ---- 跨域公共键 ----
            C("project_code", "STRING", "所属项目（跨域公共键，也是标量预过滤维度）"),
            C("vehicle_code", "STRING", "车辆编码（跨域公共键，也是标量预过滤维度）"),
            C("dataset_id", "STRING", "所属数据集（跨域公共键：检索结果直接圈进训练集时按它去重）"),
            C("dataset_version", "STRING", "数据集版本（跨域公共键，与 dataset_id 成对使用）"),
            # ---- 三级 ID 与时间 ----
            C("artifact_id", "STRING", "二级 ID：向量作为处理产物的 ID"),
            C("parent_artifact_id", "STRING", "血缘父产物（抽帧图片产物）"),
            C("artifact_status", "STRING", "产物状态：active/superseded/invalid"),
            C("run_id", "STRING", "三级 ID：向量化运行 ID（批次失败可断点续跑）"),
            C(
                "embed_time",
                "TIMESTAMP(3)",
                "向量化完成时间（T+1 每日凌晨 6 点前完成增量处理），即 vector 侧的 encoded_at",
            ),
        ],
    ),
    # ------------------------------------------------------------------ DWS
    TableSpec(
        name="dws_mining_efficiency_daily",
        layer=Layer.DWS,
        domain=D,
        comment="挖掘效率日指标（按 日期 × 项目 × 任务类型 预聚合挖掘漏斗各级的产出与成本）",
        bucket=2,
        primary_key=("stat_date", "project_code", "task_type"),
        notes="复合主键表达完整粒度：日期 × 项目 × 任务类型，口径在此层固化，下游不再重复计算",
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "所属项目", nullable=False),
            C(
                "task_type",
                "STRING",
                "任务类型：rule_mining/frame_extract/vlm_infer/embedding",
                nullable=False,
            ),
            C("task_count", "BIGINT", "挖掘任务数"),
            C("success_task_count", "BIGINT", "成功任务数"),
            C("fail_task_count", "BIGINT", "失败任务数"),
            C("scan_row_count", "BIGINT", "扫描数据行数（增量水位内）"),
            C("hit_data_count", "BIGINT", "命中 clip 数"),
            C("hit_image_count", "BIGINT", "命中图片数"),
            C("hit_rate", "DOUBLE", "命中率 = 命中数 / 扫描数"),
            C("tag_write_count", "BIGINT", "经统一标签服务写入的标签数"),
            C("frame_extract_count", "BIGINT", "抽帧产出图片数（常规抽帧）"),
            C("event_frame_count", "BIGINT", "事件补抽帧产出图片数"),
            C("vlm_infer_image_count", "BIGINT", "VLM 推理图片数"),
            C("embedding_image_count", "BIGINT", "向量化图片数"),
            C("gpu_hours", "DOUBLE", "GPU 消耗（小时）——推理与向量化的主要成本项"),
            C("avg_task_duration_sec", "DOUBLE", "平均任务耗时（秒）"),
            C("p95_task_duration_sec", "DOUBLE", "P95 任务耗时（秒）"),
            C("vector_search_p95_ms", "DOUBLE", "向量检索 P95 时延（毫秒），验收线 ≤ 2000"),
        ],
    ),
    TableSpec(
        name="dws_mining_tag_coverage_daily",
        layer=Layer.DWS,
        domain=D,
        comment="标签覆盖度日指标（按 日期 × 项目 × 标签类别 统计标签健康度，覆盖率长期走低即定向采集的需求信号）",
        bucket=2,
        primary_key=("stat_date", "project_code", "tag_category"),
        notes=(
            "复合主键表达完整粒度：日期 × 项目 × 标签类别；标签体系由此反过来驱动采集策略。"
            "【主键裁决：维持 (stat_date, project_code, tag_category)，不采信子系统的 (dt, tag_category)】"
            "按主键三原则之二「复合主键表达粒度」：本湖是多项目共用的，覆盖率天然是按项目算的——"
            "去掉 project_code 后，两个项目同一天同一类别的两行会撞成一行，Upsert 静默互相覆盖，"
            "而覆盖率正是定向采集的触发信号，算错方向就错。"
            "旁证是同层同域的三张表口径一致：dws_mining_efficiency_daily 是 (stat_date, project_code, task_type)、"
            "ads_mining_tag_dashboard 是 (stat_date, project_code, tag_id)、"
            "dwd_scene_gap_detail 是 (project_code, tag_id)——缺口本来就下发到项目。"
            "原则三不适用（本表不分区：一天只产出「项目 × 6 类别」量级的行，按 dt 分区没有收益）。"
            "日期列同理采信 registry 的 stat_date(DATE)，子系统的 dt(STRING yyyy-MM-dd) 在接线阶段做类型转换。"
            "bucket 两侧一致取 2（Bucket 五档第二档：DWS 汇总表）。"
            "本次并入 tags 子系统的四项能力指标：标签行数与去重标签数（词表使用广度）、"
            "审核进度三件套（已审/未审/过审率）、互斥裁决落败数、低覆盖标记。"
            "近义异名归一见模块 docstring（*_cnt→*_count、*_rate→*_ratio、clip→data）"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "所属项目", nullable=False),
            C(
                "tag_category",
                "STRING",
                "标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR（+CAPTION）",
                nullable=False,
            ),
            C("active_tag_count", "BIGINT", "该类别下生效（active）标签数"),
            C(
                "candidate_tag_count",
                "BIGINT",
                "候选池待审标签数（字典未匹配的新标签，防爆炸压力表）",
            ),
            C("deprecated_tag_count", "BIGINT", "废弃标签数"),
            C("tagged_data_count", "BIGINT", "已打标 clip 数（该类别下至少有一个有效标签）"),
            C("total_data_count", "BIGINT", "全量 clip 数（clip 覆盖率分母）"),
            C(
                "data_coverage_ratio",
                "DOUBLE",
                "clip 级覆盖率 = tagged_data_count / total_data_count",
            ),
            C("tagged_image_count", "BIGINT", "已打标图片数"),
            C("total_image_count", "BIGINT", "全量图片数"),
            C("image_coverage_ratio", "DOUBLE", "图片级覆盖率"),
            C(
                "tag_record_count",
                "BIGINT",
                "标签事实行数（含三来源重复计数）。与 tagged_data_count 的差值就是「平均每个 clip 打了几个标签」，"
                "这条曲线陡涨往往是标签爆炸的第一个征兆",
            ),
            C(
                "distinct_tag_count",
                "BIGINT",
                "当日实际用到的去重标签数。对照 active_tag_count 即词表使用广度——"
                "字典里挂着一千个标签、实际只有三十个在用，说明词表在空转",
            ),
            C("collect_tag_count", "BIGINT", "采集来源标签条数"),
            C("rule_tag_count", "BIGINT", "规则来源标签条数"),
            C("vlm_tag_count", "BIGINT", "模型来源标签条数"),
            C("reviewed_tag_ratio", "DOUBLE", "已审核标签占比（未审核标签不得进入训练集圈选）"),
            C(
                "reviewed_tag_count",
                "BIGINT",
                "已过审条数（绝对量）。占比能看健康度，绝对量才排得出审核工作量与积压",
            ),
            C(
                "unreviewed_tag_count",
                "BIGINT",
                "未审核条数——这部分明确不得进入训练集圈选，是圈选可用量的直接扣减项",
            ),
            C(
                "review_pass_ratio",
                "DOUBLE",
                "过审率 = approved / (approved + rejected)。注意与 reviewed_tag_ratio 不是一回事："
                "那个答「审了多少」，这个答「审过的里有多少是对的」——模型标签质量下滑先从这条看出来",
            ),
            C(
                "conflict_invalid_count",
                "BIGINT",
                "同层互斥裁决落败被置 invalid 的条数。持续偏高说明互斥组划分或来源优先级有问题，"
                "是字典治理的告警信号",
            ),
            C("avg_confidence", "DOUBLE", "平均置信度（仅带 confidence 的记录参与）"),
            C("coverage_trend_7d", "DOUBLE", "近 7 日覆盖率变化，持续走低即定向采集需求信号"),
            C(
                "low_coverage_flag",
                "BOOLEAN",
                "当日是否低于低覆盖判定线。判定线口径固化在本层，下游大屏与告警直接读结论，"
                "不各自再定一套阈值（口径在 DWS 固化，下游不重复计算）",
            ),
            C("gap_tag_count", "BIGINT", "存在缺口的标签数（对照 dwd_scene_gap_detail）"),
        ],
    ),
    # ------------------------------------------------------------------ ADS
    TableSpec(
        name="ads_mining_tag_dashboard",
        layer=Layer.ADS,
        domain=D,
        comment="挖掘标签分布看板（零 JOIN：标签分布/三来源占比/缺口等级一次 SELECT 取齐）",
        bucket=2,
        primary_key=("stat_date", "project_code", "tag_id"),
        notes="复合主键表达完整粒度：日期 × 项目 × 标签；字段已冗余齐全，前端大屏直接 SELECT，不再做任何关联",
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "所属项目", nullable=False),
            C("tag_id", "STRING", "标准标签 ID", nullable=False),
            C("tag_name", "STRING", "标签名称（冗余，免查字典）"),
            C("tag_category", "STRING", "标签类别：SCENE/ENV/ROAD/PARTICIPANT/BEHAVIOR"),
            C("parent_tag_id", "STRING", "二级分类父标签 ID（大屏下钻用）"),
            C("tag_level", "STRING", "适用粒度：clip/image/inheritable"),
            C("tag_status", "STRING", "标签状态：candidate/active/deprecated/merged"),
            C("data_count", "BIGINT", "命中 clip 数"),
            C("image_count", "BIGINT", "命中图片数"),
            C("data_ratio", "DOUBLE", "clip 数占本类别比重"),
            C("image_ratio", "DOUBLE", "图片数占本类别比重"),
            C("collect_source_count", "BIGINT", "采集来源标签条数"),
            C("rule_source_count", "BIGINT", "规则来源标签条数"),
            C("vlm_source_count", "BIGINT", "模型来源标签条数"),
            C("avg_confidence", "DOUBLE", "平均置信度"),
            C("coverage_ratio", "DOUBLE", "对照 ODD 目标的覆盖率"),
            C("gap_level", "STRING", "缺口等级：critical/high/medium/low"),
            C("rank_in_category", "INT", "类别内数量排名"),
            C("dod_change_ratio", "DOUBLE", "环比昨日变化率"),
        ],
    ),
]
