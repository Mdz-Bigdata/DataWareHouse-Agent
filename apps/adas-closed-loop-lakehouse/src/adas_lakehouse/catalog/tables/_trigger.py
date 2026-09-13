"""回传域（trigger_）：触发事件/影子模式/热力图。

量产回传是闭环「双驱动供给」的第二条腿，也是车队规模化后的主燃料：
云端下发触发条件 → 车端命中即回传对应场景片段；影子模式在后台捕捉算法与人类
驾驶的分歧，让每一帧回传数据自带长尾场景标签；功能测试则承接量产车的定向验证。

核心落表链路（系列一全景综述 场景④）：
    ods_vehicle_trigger_event → dwd_vehicle_trigger_detail → ads_hard_case_library

结构与注释风格对齐参考实现 _collect.py。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.TRIGGER

TABLES: list[TableSpec] = [
    TableSpec(
        name="ods_vehicle_trigger_event",
        layer=Layer.ODS,
        # 表名第二段是业务实体 vehicle_trigger_event（源文第三章明确列为业务实体），
        # 域段 trigger 被吸收进实体名，属已登记的命名偏离
        name_omits_domain=True,
        domain=D,
        comment="车端触发事件（触发器命中即回传）",
        source_system="车端回传 Kafka",
        bucket=4,
        partition_by=("trigger_type",),
        primary_key=("event_id", "trigger_type"),
        notes=(
            "分区规则二：有明确业务分类过滤 → 按业务字段分区，各触发类型数据量差异大；"
            "原则三：分区表主键必须包含分区字段，故 PK=(event_id, trigger_type)"
        ),
        columns=[
            C(
                "event_id",
                "STRING",
                "触发事件 ID（车端生成，Kafka 消息业务唯一键）",
                nullable=False,
            ),
            C(
                "trigger_type",
                "STRING",
                "触发类型：takeover/aeb/hard_brake/rule_hit/corner_case（分区字段）",
                nullable=False,
            ),
            C(
                "data_id",
                "STRING",
                "回传片段对应 clip 的 data_id，车端随文件生成，回传数据的终身锚点",
            ),
            C("vehicle_code", "STRING", "触发车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C("software_version", "STRING", "触发时车端软件版本（部署域交接标识）"),
            C("trigger_rule_id", "STRING", "命中的云端下发触发规则 ID"),
            C("trigger_time", "TIMESTAMP(3)", "车端触发时刻"),
            C("gps_lat", "DOUBLE", "触发点纬度"),
            C("gps_lon", "DOUBLE", "触发点经度"),
            C("vehicle_speed_kph", "DOUBLE", "触发时车速（km/h）"),
            C("road_type", "STRING", "道路类型"),
            C("weather", "STRING", "天气"),
            C("light_condition", "STRING", "光照条件"),
            C("clip_duration_sec", "DOUBLE", "回传片段时长（秒）"),
            C("upload_status", "STRING", "回传上云状态"),
            C("kafka_offset", "BIGINT", "Kafka 消息位点，支持事件回放不丢数"),
            # ---- 入湖门禁判据（系列二第 6 篇《数据质量门禁设计》4.2 Kafka 消息流 · 事件流查「时空合理」）----
            # 分区字段仍是 trigger_type、主键仍是 (event_id, trigger_type)——下面全是追加的普通列，
            # 不参与分区也不进主键，不动原有物理策略。
            C(
                "pre_trigger_seconds",
                "DOUBLE",
                "回传片段覆盖触发前的秒数。对应 QG-KFK-003 片段截断：覆盖触发前 ≥ N 秒，"
                "不足则标记 truncated_flag 告警放行（N 见 thresholds.TRIGGER_PRE_SECONDS_DEFAULT）",
            ),
            C(
                "post_trigger_seconds",
                "DOUBLE",
                "回传片段覆盖触发后的秒数。对应 QG-KFK-004 片段截断：覆盖触发后 ≥ M 秒，"
                "不足则标记 truncated_flag 告警放行（M 见 thresholds.TRIGGER_POST_SECONDS_DEFAULT）",
            ),
            C(
                "duplicate_rate",
                "DOUBLE",
                "所属批次的事件重复率（0~1，批级统计量，随记录落表便于复盘）。"
                "对应 QG-KFK-006 重复率监控：事件 ID 幂等去重后重复率 > 5% 告警",
            ),
            C(
                "gps_jump_meters",
                "DOUBLE",
                "相对上一采样点的定位跳变距离（米）。对应 QG-KFK-008 定位跳变："
                "六维之准确性「定位无跳变」，超 100 米 P2 告警标记放行。"
                "⚠️ 原文未明确，本项目设计：原文只写「定位无跳变」这一句定性要求，"
                "既没给度量也没给米数；本项目落成「相对上一采样点的跳变距离」这一标量列",
            ),
            # 这一列是规则通过 params 引用的判据列（不是 RuleSpec.field），
            # 并列阶段按 field 并列时漏掉了——列不存在即规则的下界恒为 NULL、只剩上界生效。
            C(
                "vehicle_manufacture_time",
                "TIMESTAMP(3)",
                "车辆出厂时间（随事件冗余落表，避免门禁为一条规则去 join 车辆档案）。"
                "QG-KFK-002 时间戳合理性的下界：trigger_time 早于出厂时间即不合理，"
                "上界是服务器时间 + 车端时钟漂移容忍窗（thresholds.CLOCK_DRIFT_TOLERANCE_SECONDS）",
            ),
        ],
    ),
    TableSpec(
        name="ods_vehicle_function_test",
        layer=Layer.ODS,
        # 同上：实体名为 vehicle_function_test，不含独立域段
        name_omits_domain=True,
        domain=D,
        comment="车端功能测试回传记录",
        source_system="车端回传 Kafka",
        bucket=4,
        primary_key=("test_record_id",),
        notes="分区规则三：主键 Upsert 且无明确分区维度 → 不分区，靠 bucket 打散",
        columns=[
            C("test_record_id", "STRING", "功能测试记录 ID", nullable=False),
            C("data_id", "STRING", "测试片段对应 clip 的 data_id"),
            C("vehicle_code", "STRING", "测试车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C("test_plan_id", "STRING", "测试计划 ID"),
            C("function_code", "STRING", "被测功能编码：acc/lka/noa/apa/aeb"),
            C("function_name", "STRING", "被测功能名称"),
            C("software_version", "STRING", "被测车端软件版本"),
            C("model_version", "STRING", "被测模型版本（训练域交接标识）"),
            C("test_scene", "STRING", "测试场景描述"),
            C("test_result", "STRING", "测试结论：pass/fail/blocked"),
            C("fail_reason", "STRING", "失败原因"),
            C("takeover_count", "INT", "测试过程中的接管次数"),
            C("test_mileage_km", "DOUBLE", "测试里程（公里）"),
            C("test_start_time", "TIMESTAMP(3)", "测试开始时间"),
            C("test_end_time", "TIMESTAMP(3)", "测试结束时间"),
            C("tester", "STRING", "测试员/安全员"),
        ],
    ),
    TableSpec(
        name="ods_shadow_mode_data",
        layer=Layer.ODS,
        domain=D,
        comment="影子模式数据（后台捕捉算法与人类驾驶的分歧）",
        source_system="车端回传 Kafka",
        bucket=4,
        primary_key=("shadow_record_id",),
        notes=(
            "影子模式不接管车辆，只在后台并行推理并与人类驾驶行为比对，"
            "分歧即长尾场景线索——这是回传数据自带长尾标签的来源。"
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区"
        ),
        columns=[
            C("shadow_record_id", "STRING", "影子模式记录 ID（一次分歧一条）", nullable=False),
            C("data_id", "STRING", "分歧片段对应 clip 的 data_id"),
            C("vehicle_code", "STRING", "车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C("software_version", "STRING", "车端软件版本"),
            C("model_version", "STRING", "后台影子运行的模型版本（训练域交接标识）"),
            C("divergence_type", "STRING", "分歧类型：trajectory/speed/lane_change/braking"),
            C("divergence_score", "DOUBLE", "分歧度打分，算法决策与人类驾驶的偏离程度"),
            C("algo_action", "STRING", "算法拟执行动作"),
            C("human_action", "STRING", "人类驾驶实际动作"),
            C("lateral_offset_m", "DOUBLE", "横向轨迹偏差（米）"),
            C("speed_diff_kph", "DOUBLE", "纵向速度差（km/h）"),
            C("occur_time", "TIMESTAMP(3)", "分歧发生时刻"),
            C("gps_lat", "DOUBLE", "分歧点纬度"),
            C("gps_lon", "DOUBLE", "分歧点经度"),
            C("road_type", "STRING", "道路类型"),
            C("upload_status", "STRING", "回传上云状态"),
        ],
    ),
    TableSpec(
        name="dwd_vehicle_trigger_detail",
        layer=Layer.DWD,
        # 实体名 vehicle_trigger 与 ODS 侧保持一致，域段被实体吸收
        name_omits_domain=True,
        domain=D,
        comment="车端触发事件明细（清洗打标后，难例库上游）",
        bucket=8,
        primary_key=("data_id",),
        notes=(
            "一次触发对应一段回传 clip，data_id 与 event_id 一一对应；"
            "以 data_id 为主键，让「从 Badcase / 难例回溯到原始回传 clip」成为一次主键查询。"
            "bucket=8：量产回传随车队规模持续放量，属大体量明细表。不分区（规则三）"
        ),
        columns=[
            C("data_id", "STRING", "一级 ID：回传 clip 级终身锚点", nullable=False),
            C("event_id", "STRING", "源触发事件 ID（ods_vehicle_trigger_event）"),
            C("trigger_type", "STRING", "触发类型（标准化后）"),
            C("trigger_rule_id", "STRING", "命中的触发规则 ID"),
            C("trigger_rule_name", "STRING", "触发规则名称"),
            C("vehicle_code", "STRING", "触发车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C("software_version", "STRING", "触发时车端软件版本（部署域交接标识）"),
            C("model_version", "STRING", "触发时车端模型版本（训练域交接标识）"),
            C("trigger_time", "TIMESTAMP(3)", "车端触发时刻"),
            C("gps_lat", "DOUBLE", "触发点纬度"),
            C("gps_lon", "DOUBLE", "触发点经度"),
            C("city_code", "STRING", "行政区编码，热力图聚合维度"),
            C("road_type", "STRING", "道路类型"),
            C("scene_tag", "STRING", "清洗打标后的场景标签，回补训练集的依据"),
            C("severity_level", "STRING", "严重程度：P0/P1/P2/P3"),
            C("is_hard_case", "BOOLEAN", "是否已沉淀为难例（→ ads_hard_case_library）"),
            C("dataset_id", "STRING", "已回补进的数据集 ID"),
            C("closed_loop_latency_min", "BIGINT", "闭环耗时：触发 → 进训练集（分钟）"),
        ],
    ),
    TableSpec(
        name="dwd_shadow_mode_detail",
        layer=Layer.DWD,
        domain=D,
        comment="影子模式数据明细（算法-人类分歧，长尾场景线索）",
        bucket=8,
        primary_key=("data_id", "shadow_record_id"),
        notes=(
            "原则二：复合主键表达完整粒度——同一段回传 clip 内可能捕捉到多次分歧，"
            "单靠 data_id 无法唯一标识。data_id 置于主键首位，保证按 clip 前缀回溯仍走主键。"
            "bucket=8：影子模式后台全量运行，体量大于触发器回传"
        ),
        columns=[
            C("data_id", "STRING", "一级 ID：分歧片段所属 clip 的终身锚点", nullable=False),
            C(
                "shadow_record_id",
                "STRING",
                "影子模式记录 ID，同一 clip 内多次分歧靠它区分",
                nullable=False,
            ),
            C("vehicle_code", "STRING", "车辆编码"),
            C("project_code", "STRING", "所属项目"),
            C("software_version", "STRING", "车端软件版本（部署域交接标识）"),
            C("model_version", "STRING", "影子模式运行的模型版本（训练域交接标识）"),
            C("divergence_type", "STRING", "分歧类型：trajectory/speed/lane_change/braking"),
            C("divergence_score", "DOUBLE", "分歧度打分（标准化到 0~1）"),
            C("algo_action", "STRING", "算法拟执行动作"),
            C("human_action", "STRING", "人类驾驶实际动作"),
            C("lateral_offset_m", "DOUBLE", "横向轨迹偏差（米）"),
            C("speed_diff_kph", "DOUBLE", "纵向速度差（km/h）"),
            C("occur_time", "TIMESTAMP(3)", "分歧发生时刻"),
            C("city_code", "STRING", "行政区编码，热力图聚合维度"),
            C("road_type", "STRING", "道路类型"),
            C("scene_tag", "STRING", "清洗打标后的场景标签"),
            C("is_long_tail", "BOOLEAN", "是否长尾场景：影子模式的核心产出"),
            C("is_hard_case", "BOOLEAN", "是否已沉淀为难例"),
            C("dataset_id", "STRING", "已回补进的数据集 ID"),
        ],
    ),
    TableSpec(
        name="dws_trigger_statistics",
        layer=Layer.DWS,
        domain=D,
        comment="触发事件统计指标（日期 × 项目 × 触发类型）",
        bucket=2,
        primary_key=("stat_date", "project_code", "trigger_type"),
        notes=(
            "口径固化：回传相关指标全公司只有这一个算法，下游不再重复 JOIN。"
            "原则二：复合主键表达完整聚合粒度。分区规则三：不分区"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "项目编码", nullable=False),
            C("trigger_type", "STRING", "触发类型", nullable=False),
            C("trigger_event_cnt", "BIGINT", "触发事件数"),
            C("trigger_vehicle_cnt", "BIGINT", "发生触发的车辆数"),
            C("uploaded_clip_cnt", "BIGINT", "成功回传的 clip 数"),
            C("upload_success_rate", "DOUBLE", "回传成功率"),
            C("uploaded_size_gb", "DOUBLE", "回传数据量（GB）"),
            C("shadow_divergence_cnt", "BIGINT", "影子模式分歧事件数"),
            C("avg_divergence_score", "DOUBLE", "平均分歧度"),
            C("hard_case_cnt", "BIGINT", "沉淀为难例的数量"),
            C("hard_case_rate", "DOUBLE", "难例转化率"),
            C("into_dataset_cnt", "BIGINT", "已回补进数据集的 clip 数"),
            C("avg_closed_loop_latency_min", "DOUBLE", "闭环平均耗时：触发 → 进训练集（分钟）"),
            C("p95_closed_loop_latency_min", "DOUBLE", "闭环 P95 耗时（分钟）"),
            C("trigger_per_thousand_km", "DOUBLE", "千公里触发次数，按车队里程归一化"),
            C("top_scene_tag", "STRING", "当日触发占比最高的场景标签"),
        ],
    ),
    TableSpec(
        name="ads_trigger_heatmap",
        layer=Layer.ADS,
        domain=D,
        comment="触发事件热力图（零 JOIN，直供大屏地图渲染）",
        bucket=2,
        primary_key=("stat_date", "project_code", "geo_grid_id", "trigger_type"),
        notes=(
            "ADS 零 JOIN：网格中心经纬度、城市名、热力等级、代表样本 data_id 全部打平，"
            "前端 SELECT 即渲染。行数 = 日期 × 项目 × 网格 × 触发类型，属小 ADS → bucket=2。"
            "分区规则三：不分区"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "项目编码", nullable=False),
            C(
                "geo_grid_id",
                "STRING",
                "地理网格 ID（GeoHash），热力图最小渲染单元",
                nullable=False,
            ),
            C("trigger_type", "STRING", "触发类型", nullable=False),
            C("grid_center_lat", "DOUBLE", "网格中心纬度"),
            C("grid_center_lon", "DOUBLE", "网格中心经度"),
            C("city_code", "STRING", "行政区编码"),
            C("city_name", "STRING", "城市名称，打平避免前端查维表"),
            C("road_type", "STRING", "网格内主要道路类型"),
            C("trigger_cnt", "BIGINT", "网格内触发次数（热力值）"),
            C("heat_level", "INT", "热力等级 1~5，前端直接取色，零计算"),
            C("vehicle_cnt", "BIGINT", "网格内触发车辆数"),
            C("avg_vehicle_speed_kph", "DOUBLE", "触发时平均车速（km/h）"),
            C("shadow_divergence_cnt", "BIGINT", "网格内影子模式分歧数"),
            C("hard_case_cnt", "BIGINT", "网格内沉淀的难例数"),
            C("top_scene_tag", "STRING", "该网格最高频场景标签"),
            C("top_scene_tag_cnt", "BIGINT", "该场景标签的触发次数"),
            C("sample_data_id", "STRING", "代表性样本 clip 的 data_id，点击热点即下钻到原始片段"),
        ],
    ),
]
