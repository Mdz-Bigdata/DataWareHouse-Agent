"""闭环域（closed_loop_）：追溯/存储生命周期/成本。

闭环域不参与主环单向流转——它是
跨域整合域，没有 ODS 表
，数据全部来自其他 10 个域的 DWD 层加工（系列二第二章）。
它以闭环追溯表为主轴与各域双向交互，和效率、贡献度、成本一起构成
「效率 · 贡献 · 成本」三维闭环治理指标体系（系列一第七篇）。

两条主轴：
  · dwd_closed_loop_trace —— DWD 层的「终极汇总表」，以 data_id 为主键记录
    每个数据单元在闭环中的全局状态快照（被哪些训练任务用过、产出过哪些模型版本、
    在哪些评测任务出现过、是否触发 Badcase、是否被量产车触发过）。
    它让「从 Badcase 回溯到原始采集 clip」成为一次主键查询。
  · dwd_closed_loop_storage_lifecycle —— 存储五级分层（热 H1 / 温 H2 / 冷 C1 /
    归档 C2 / 删除 D）的状态快照，是预热、淘汰、降冷、删除一切治理动作的唯一决策源，
    与 trace 表通过 data_id 关联，形成「闭环追溯 + 存储状态」孪生视图。

⚠️ 命名偏差（非阻断，naming.lint 会提示）：
  dwd_closed_loop_storage_lifecycle / dws_closed_loop_efficiency /
  dws_data_contribution 三张表在源文清单里就没有标准粒度后缀（第四段），
  dws_data_contribution 与 ads_storage_cost_dashboard 连域段都省了。
  表名以源文为准不做改写，域归属以本注册表的显式声明为准。

结构与注释风格对齐采集域参考实现 _collect.py。
"""

from __future__ import annotations

from ...domains import DataDomain, Layer
from ..spec import Column as C
from ..spec import TableSpec

D = DataDomain.CLOSED_LOOP

TABLES: list[TableSpec] = [
    # ---- DWD：两张主轴表，data_id 孪生视图 ----
    TableSpec(
        name="dwd_closed_loop_trace",
        layer=Layer.DWD,
        domain=D,
        comment="闭环全链路追溯表（终极汇总表，data_id 级全局状态快照）",
        bucket=16,
        primary_key=("data_id",),
        notes=(
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区。"
            "bucket 取最高档 16：全湖每个 clip 一行，且被采集/生产/资产/训练/评测/"
            "部署/回传/挖掘 8 个域的 Flink 任务并发 Upsert，属超大表 + 高并发写入。"
            "只记全局状态快照、不记过程细节——过程细节在各域自己的明细表里，"
            "本表的职责是让「Badcase → 原始 clip」退化成一次主键查询。"
            "list 类字段落 JSON 数组字符串（一个 clip 会被多个训练/评测任务复用），"
            "图库（Neo4j）存完整血缘，本表冗余落表作对账兜底"
        ),
        columns=[
            C("data_id", "STRING", "一级 ID：clip 级终身锚点，闭环唯一入口", nullable=False),
            C("project_code", "STRING", "所属项目"),
            C("vehicle_code", "STRING", "采集车辆编码"),
            C("collect_time", "TIMESTAMP(3)", "采集时间，闭环计时起点"),
            C("latest_artifact_id", "STRING", "二级 ID：当前生效的处理产物"),
            C("parent_artifact_id", "STRING", "血缘父产物，冗余落表作图库对账兜底"),
            C(
                "artifact_status",
                "STRING",
                "产物状态：active/superseded/invalid（重刷不覆盖，旧产物标 superseded）",
            ),
            C("latest_run_id", "STRING", "三级 ID：产出当前产物的那次处理运行，支撑可重放"),
            C("dataset_id", "STRING", "最近一次入集的数据集 ID"),
            C("dataset_version", "STRING", "对应数据集版本"),
            C("training_task_id_list", "STRING", "被哪些训练任务用过（JSON 数组），元素数即复用度"),
            C("model_version", "STRING", "参与产出的最新模型版本"),
            C("evaluation_task_id_list", "STRING", "在哪些评测任务中出现过（JSON 数组）"),
            C("evaluation_type", "STRING", "最近一次评测类型：offline/simulation/real_vehicle"),
            C("badcase_flag", "BOOLEAN", "是否触发过 Badcase"),
            C("badcase_count", "INT", "关联 Badcase 数量"),
            C("trigger_flag", "BOOLEAN", "是否被量产车触发回传过"),
            C("trigger_type", "STRING", "量产车触发类型（回传域口径）"),
            C(
                "closed_loop_stage",
                "STRING",
                "当前闭环环节：collect/produce/dataset/train/evaluate/deploy/trigger",
            ),
            C(
                "closed_loop_duration_hours",
                "DOUBLE",
                "完整闭环耗时（采集 → OTA 部署），DWS 效率口径输入",
            ),
        ],
    ),
    TableSpec(
        name="dwd_closed_loop_storage_lifecycle",
        layer=Layer.DWD,
        name_omits_suffix=True,  # 源文给定表名，不带标准粒度后缀
        domain=D,
        comment="存储生命周期状态明细（五级分层快照，治理动作唯一决策源）",
        bucket=16,
        primary_key=("data_id", "file_path"),
        notes=(
            "分区规则三：主键 Upsert 且无明确分区维度 → 不分区。"
            "主键原则二：(data_id, file_path) 复合主键表达完整粒度——一个 clip 对应"
            "多个文件（视频/点云/中间产物），治理动作以文件为最小单位。"
            "bucket 取 16：行数是 clip 级的数倍（文件级），且日级治理批量回写并发高。"
            "五级分层：hot(NAS) / warm(OSS 标准) / cold(OSS 低频) / archive(OSS 归档) /"
            "pending_delete / deleted；铁律「淘汰 ≠ 删除」——淘汰只清 NAS 副本，"
            "OSS 永远是唯一事实源；删除需三重确认（过保留期 + 血缘零引用 + 白名单校验）"
        ),
        columns=[
            C(
                "data_id",
                "STRING",
                "一级 ID：关联 dwd_closed_loop_trace 的 clip 锚点",
                nullable=False,
            ),
            C("file_path", "STRING", "文件路径（OSS object key / NAS 路径）", nullable=False),
            C("artifact_id", "STRING", "二级 ID：该文件所属处理产物（原始文件为空）"),
            C("project_code", "STRING", "所属项目"),
            C(
                "source_domain",
                "STRING",
                "来源数据域：collect/production/dataset/training/simulation/trigger",
            ),
            C(
                "data_type",
                "STRING",
                "数据类型（保留期策略维度）：raw/intermediate/dataset/model/temp",
            ),
            C(
                "storage_media",
                "STRING",
                "当前介质：oss_standard/oss_ia/oss_archive/oss_deep_archive/nas",
            ),
            C(
                "lifecycle_stage",
                "STRING",
                "分层状态：hot/warm/cold/archive/pending_delete/deleted",
            ),
            C("file_size_bytes", "BIGINT", "文件大小（字节），容量与成本折算基数"),
            C(
                "create_time",
                "TIMESTAMP(3)",
                "数据创建时间——温层条件「创建 30 天内」与全部保留期的计时起点",
            ),
            C("stage_entered_at", "TIMESTAMP(3)", "进入当前 lifecycle_stage 的时间，判定停留时长"),
            C("last_access_time", "TIMESTAMP(3)", "最后访问时间，降冷驱动依据"),
            C("access_count_30d", "INT", "近 30 天访问次数，NAS LRU 淘汰依据"),
            C("lineage_ref_count", "INT", "下游血缘引用数，删除保护依据（>0 拦截删除）"),
            C("whitelist_flag", "BOOLEAN", "白名单豁免标志：合规留存/长期回归数据跳过分层流转"),
            C(
                "expire_policy",
                "STRING",
                "保留策略：raw_365d/intermediate_365d/dataset_forever/model_top_n/temp_7d",
            ),
            C("preheat_task_id", "STRING", "最近一次预热任务 ID，预热归因"),
            C("preheat_time", "TIMESTAMP(3)", "最近一次预热至 NAS 的时间"),
            C("evict_status", "STRING", "淘汰状态：none/pending/done/skipped"),
            C("checksum_md5", "STRING", "对象校验和，NAS 副本与 OSS 一致才允许清除"),
            C("tier_down_time", "TIMESTAMP(3)", "最近一次降冷/归档流转时间"),
            C("monthly_cost_yuan", "DOUBLE", "当前介质下的月成本折算（容量 × 介质单价）"),
        ],
    ),
    # ---- DWS：效率 · 贡献 · 成本 三维治理指标 ----
    TableSpec(
        name="dws_closed_loop_efficiency",
        layer=Layer.DWS,
        name_omits_suffix=True,  # 源文给定表名，不带标准粒度后缀
        domain=D,
        comment="闭环效率指标（按日期 × 项目预聚合，口径在此层固化）",
        bucket=2,
        primary_key=("stat_date", "project_code"),
        notes=(
            "分区规则三：不分区；bucket 取 2（DWS 汇总表档）。"
            "主键原则二：(stat_date, project_code) 复合主键表达「日期 × 项目」聚合粒度。"
            "口径固化的价值是「同一个指标全公司只有一个算法」——下游大盘与瓶颈分析"
            "直接读本表，不再重复 JOIN dwd_closed_loop_trace"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "项目代码", nullable=False),
            C("clip_total_count", "BIGINT", "当日纳入统计的数据单元（clip）数"),
            C("delivered_clip_count", "BIGINT", "当日完成交付的数据单元数"),
            C("trained_clip_count", "BIGINT", "当日进入训练集的数据单元数"),
            C("collect_to_delivery_hours", "DOUBLE", "采集到交付平均耗时（小时）"),
            C("delivery_to_dataset_hours", "DOUBLE", "交付到入数据集平均耗时（小时）"),
            C("training_duration_hours", "DOUBLE", "训练平均耗时（小时）"),
            C("evaluation_duration_hours", "DOUBLE", "评测平均耗时（小时）"),
            C("deployment_duration_hours", "DOUBLE", "评测到 OTA 部署平均耗时（小时）"),
            C(
                "closed_loop_duration_hours",
                "DOUBLE",
                "完整闭环平均耗时（采集 → 部署），核心北极星指标",
            ),
            C("closed_loop_p90_hours", "DOUBLE", "闭环耗时 P90（小时），看长尾而非只看均值"),
            C("bottleneck_stage", "STRING", "瓶颈环节：耗时占比超阈值或环比恶化的环节"),
            C("badcase_total_count", "BIGINT", "当日关联 Badcase 总数"),
            C("badcase_resolved_count", "BIGINT", "已解决 Badcase 数"),
            C("badcase_resolve_rate", "DOUBLE", "Badcase 解决率 = 已解决 / 总数"),
            C("badcase_avg_resolve_hours", "DOUBLE", "Badcase 平均解决耗时（小时）"),
            C("model_version_count", "INT", "当日产出模型版本数，闭环转动圈数"),
            C("efficiency_improve_rate", "DOUBLE", "效率提升率（闭环耗时环比改善幅度）"),
        ],
    ),
    TableSpec(
        name="dws_data_contribution",
        layer=Layer.DWS,
        name_omits_suffix=True,  # 源文给定表名，不带标准粒度后缀
        name_omits_domain=True,
        domain=D,
        comment="数据贡献度指标（按日期 × 项目 × 数据来源，衡量每类数据的模型增益）",
        bucket=2,
        primary_key=("stat_date", "project_code", "data_source"),
        notes=(
            "分区规则三：不分区；bucket 取 2（DWS 汇总表档）。"
            "主键原则二：三段复合主键表达「日期 × 项目 × 来源」粒度——双驱动供给"
            "（主动采集 + 量产回传）与挖掘回补的贡献必须能分开算账，"
            "才能回答「下一轮该往哪类数据投钱」。贡献与成本同表对齐，"
            "配合 dws_closed_loop_storage_cost_daily 得出单条有效数据成本"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "项目代码", nullable=False),
            C(
                "data_source",
                "STRING",
                "数据来源：active_collect/vehicle_trigger/shadow_mode/mining_recall/simulation",
                nullable=False,
            ),
            C("supply_clip_count", "BIGINT", "当日供给的数据单元（clip）数"),
            C("into_dataset_count", "BIGINT", "入数据集的数据单元数"),
            C("into_dataset_rate", "DOUBLE", "入集率 = 入集数 / 供给数，供给质量"),
            C("train_used_count", "BIGINT", "被训练任务引用次数（含复用）"),
            C("model_version_count", "INT", "参与产出的模型版本数"),
            C("hard_case_count", "BIGINT", "沉淀为难例的数据量"),
            C("badcase_related_count", "BIGINT", "关联 Badcase 数"),
            C("badcase_fixed_count", "BIGINT", "补数重训后修复的 Badcase 数"),
            C("scene_coverage_gain_pp", "DOUBLE", "场景覆盖度提升（百分点）"),
            C("metric_gain_pp", "DOUBLE", "模型指标提升（百分点，如夜间行人漏检率改善）"),
            C("contribution_score", "DOUBLE", "综合贡献度评分（入集率 × 复用度 × 指标增益加权）"),
            C("contribution_rank", "INT", "项目内该来源的贡献度排名"),
            C("storage_cost_yuan", "DOUBLE", "该来源当日存储成本（元）"),
            C("cost_per_valid_clip", "DOUBLE", "单条有效数据成本 = 存储成本 / 入集数"),
            C("dataset_id", "STRING", "贡献最大的数据集 ID（TOP1，便于下钻）"),
        ],
    ),
    TableSpec(
        name="dws_closed_loop_storage_cost_daily",
        layer=Layer.DWS,
        domain=D,
        comment="存储成本日指标（日期 × 介质 × 分层 × 数据类型 × 来源域）",
        bucket=2,
        primary_key=("stat_date", "storage_media", "lifecycle_stage", "data_type", "source_domain"),
        notes=(
            "分区规则三：不分区；bucket 取 2（DWS 汇总表档）。"
            "主键原则二：五段复合主键即原文给定的聚合维度，缺一维就没法回答"
            "「哪类数据在哪个介质上烧钱」。治理动作量（预热/淘汰/降冷/删除）"
            "全量登记，既支撑成本看板，也作为规则调优的反馈信号"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C(
                "storage_media",
                "STRING",
                "存储介质：oss_standard/oss_ia/oss_archive/oss_deep_archive/nas",
                nullable=False,
            ),
            C(
                "lifecycle_stage",
                "STRING",
                "生命周期分层：hot/warm/cold/archive/pending_delete",
                nullable=False,
            ),
            C(
                "data_type",
                "STRING",
                "数据类型：raw/intermediate/dataset/model/temp",
                nullable=False,
            ),
            C(
                "source_domain",
                "STRING",
                "来源数据域：collect/production/dataset/training/simulation/trigger",
                nullable=False,
            ),
            C("file_count", "BIGINT", "文件数"),
            C("total_capacity_tb", "DOUBLE", "容量合计（TB）"),
            C("daily_cost_yuan", "DOUBLE", "当日折算成本（元，容量 × 介质单价）"),
            C("preheat_volume_tb", "DOUBLE", "当日预热至 NAS 数据量（TB）"),
            C("evict_volume_tb", "DOUBLE", "当日 NAS 淘汰数据量（TB，淘汰 ≠ 删除）"),
            C("tier_down_volume_tb", "DOUBLE", "当日降冷/归档流转数据量（TB）"),
            C("delete_volume_tb", "DOUBLE", "当日删除数据量（TB，过三重确认）"),
            C("nas_peak_usage", "DOUBLE", "NAS 峰值使用率（>80% 触发水位淘汰与告警）"),
            C("preheat_hit_rate", "DOUBLE", "预热命中率 = 训练命中预热 / 总预热请求"),
            C("archive_restore_count", "INT", "归档取回次数，反哺保留期与降冷阈值调优"),
            C("baseline_cost_yuan", "DOUBLE", "无治理基线成本（元），节省额对照基准"),
            C("saved_cost_yuan", "DOUBLE", "治理释放成本 = 基线成本 − 实际成本"),
            C("cost_mom_rate", "DOUBLE", "成本环比增长率（>10% 触发预算告警）"),
        ],
    ),
    # ---- ADS：零 JOIN，开箱即用 ----
    TableSpec(
        name="ads_closed_loop_dashboard",
        layer=Layer.ADS,
        domain=D,
        comment="闭环大盘指标（监控大屏 · 数据管理平台，按日期 × 项目 T+1 物化）",
        bucket=2,
        primary_key=("stat_date", "project_code"),
        notes=(
            "分区规则三：不分区；bucket 取 2（小 ADS 档，天数 × 项目数缓慢累积）。"
            "ADS 零 JOIN：项目名称等展示字段一并冗余，大屏 SELECT 即渲染。"
            "两级下钻的上层——大盘看「闭环慢不慢」，"
            "bottleneck_stage 指向 ads_production_bottleneck_analysis 定位「慢在哪一环」"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C("project_code", "STRING", "项目代码", nullable=False),
            C("project_name", "STRING", "项目名称（零 JOIN 冗余）"),
            C("total_data_count", "BIGINT", "累计数据总量（clip 数）"),
            C("month_new_data_count", "BIGINT", "本月新增数据量"),
            C("total_capacity_tb", "DOUBLE", "累计数据容量（TB）"),
            C("avg_closed_loop_hours", "DOUBLE", "平均闭环耗时（车端触发 → OTA 部署各环节汇总）"),
            C("collect_to_delivery_hours", "DOUBLE", "采集到交付耗时（小时）"),
            C("training_duration_hours", "DOUBLE", "训练耗时（小时）"),
            C("evaluation_duration_hours", "DOUBLE", "评测耗时（小时）"),
            C("collecting_data_count", "BIGINT", "状态分布：采集/上云中"),
            C("producing_data_count", "BIGINT", "状态分布：产线处理中"),
            C("delivered_data_count", "BIGINT", "状态分布：已交付"),
            C("trained_data_count", "BIGINT", "状态分布：已进入训练"),
            C("badcase_total_count", "BIGINT", "Badcase 总数"),
            C("badcase_resolve_rate", "DOUBLE", "Badcase 解决率"),
            C("data_growth_rate", "DOUBLE", "数据增长率（环比）"),
            C("efficiency_improve_rate", "DOUBLE", "效率提升率（闭环耗时环比改善）"),
            C("health_score", "DOUBLE", "闭环健康度综合评分"),
            C("bottleneck_stage", "STRING", "当前瓶颈环节，下钻产线瓶颈分析的入口"),
        ],
    ),
    TableSpec(
        name="ads_storage_cost_dashboard",
        layer=Layer.ADS,
        name_omits_domain=True,
        domain=D,
        comment="存储成本看板（监控大屏 · 数据管理平台，介质 × 分层 × 数据类型）",
        bucket=1,
        primary_key=("stat_date", "storage_media", "lifecycle_stage", "data_type"),
        notes=(
            "分区规则三：不分区；bucket 取 1——原文 Bucket 五档表就以本表作"
            "「字典表/极小表」的代表，行数 = 天数 × 4 档介质 × 5 类数据，量级极小。"
            "口径见系列一第七篇第六章：容量与成本、分层占比、治理动作量、成本节省额、"
            "NAS 峰值使用率、预热命中率、归档取回次数"
        ),
        columns=[
            C("stat_date", "DATE", "统计日期", nullable=False),
            C(
                "storage_media",
                "STRING",
                "存储介质：nas/oss_standard/oss_ia/oss_archive",
                nullable=False,
            ),
            C("lifecycle_stage", "STRING", "生命周期分层：hot/warm/cold/archive", nullable=False),
            C(
                "data_type",
                "STRING",
                "数据类型：raw/intermediate/dataset/model/temp",
                nullable=False,
            ),
            C("total_capacity_tb", "DOUBLE", "总存储容量（TB）"),
            C("capacity_ratio", "DOUBLE", "分层占比（该档容量 / 全湖容量）"),
            C("month_cost_yuan", "DOUBLE", "月存储成本（元）"),
            C("unit_price_yuan_gb_month", "DOUBLE", "介质单价（元/GB·月）"),
            C("preheat_volume_tb", "DOUBLE", "治理动作量：日预热量（TB）"),
            C("tier_down_volume_tb", "DOUBLE", "治理动作量：日降冷量（TB）"),
            C("evict_volume_tb", "DOUBLE", "治理动作量：日淘汰量（TB）"),
            C("delete_volume_tb", "DOUBLE", "治理动作量：日删除量（TB）"),
            C("baseline_cost_yuan", "DOUBLE", "无治理基线成本（元）"),
            C("saved_cost_yuan", "DOUBLE", "成本节省额 = 基线成本 − 实际成本"),
            C("nas_peak_usage", "DOUBLE", "NAS 峰值使用率（持续 >80% 告警）"),
            C("preheat_hit_rate", "DOUBLE", "预热命中率"),
            C("archive_restore_count", "INT", "归档取回次数（标准恢复 ≤ 4 小时）"),
            C("cost_mom_rate", "DOUBLE", "成本环比增长率（>10% 触发预算告警）"),
            C("capacity_mom_rate", "DOUBLE", "容量环比增长率，与成本增速对照看治理是否稳态"),
            C("budget_alert_flag", "BOOLEAN", "预算告警标识（成本环比 >10% 或 NAS 使用率 >80%）"),
        ],
    ),
]
