"""规则挖掘引擎读写的湖仓表引用与列契约。

本模块【只引用】表名与列名，不注册 TableSpec、也不手写列名清单——
表结构的唯一事实源是 :mod:`adas_lakehouse.catalog.registry`（即 catalog/tables/_*.py）。
这里存在的意义有三个：

1. 把「表名」这件事收口到一处，避免 SQL 字符串里到处散落硬编码；
2. 诚实标注每个表名的出处——哪些是原文白纸黑字写过的，哪些是本项目推断的；
3. 允许用环境变量覆盖，方便在部署环境改名，而不用改 SQL。

「平台不持有主数据」（[S3-01] 二）落到本引擎上就是：规则引擎不建任何私有副本表，
读的是采集域既有的 dwd_collect_clip_detail，写的是挖掘域既有的明细表，
标签一律经统一标签服务写入——不自建标签写入逻辑（[S3-04] 二）。

--------------------------------------------------------------------------------
列契约：只派生，不手写
--------------------------------------------------------------------------------
四组 ``*_COLUMNS`` 常量一律由 :func:`columns_of` 从 registry 现取，本模块
**没有任何一处手写列名清单**。引擎实际写入的列子集由 :func:`projection` 声明，
它会在 import 期逐列核对 registry——写错一个列名，进程起不来，
而不是等 SQL 打到真实 Paimon 上才炸，更不是静默写进一列永远没人读的影子列。

近义异名的归一方向（``rule_type -> rule_category`` 一类）见
:mod:`adas_lakehouse.catalog.tables._mining` 的模块 docstring，registry 侧是权威，
本模块与其余 mining/ 子模块按那张表对齐。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from ..catalog import registry
from ..config import settings

__all__ = [
    "TableRef",
    "StreamRef",
    "ODS_MINING_RULE_CONFIG",
    "DWD_MINING_RESULT_DETAIL",
    "DWD_MINING_TASK_DETAIL",
    "DWD_SCENE_GAP_DETAIL",
    "DWD_MINING_IMAGE_FRAME_DETAIL",
    "DWD_MINING_IMAGE_VECTOR_DETAIL",
    "DWD_COLLECT_CLIP_DETAIL",
    "DWD_MINING_TAG_DETAIL",
    "DWD_MINING_IMAGE_TAG_DETAIL",
    "ODS_VEHICLE_TRIGGER_EVENT",
    "VEHICLE_SIGNAL_STREAM",
    "ALL_REFS",
    "qualified",
    "stream_sql",
    "columns_of",
    "business_columns_of",
    "projection",
    "MINING_RESULT_COLUMNS",
    "SCENE_GAP_COLUMNS",
    "MINING_TASK_COLUMNS",
    "RULE_CONFIG_COLUMNS",
    "RESULT_WRITE_COLUMNS",
    "SCENE_GAP_WRITE_COLUMNS",
    "MINING_TASK_WRITE_COLUMNS",
    "RULE_CONFIG_CDC_COLUMNS",
    "VLM_TASK_WRITE_COLUMNS",
    "IMAGE_TAG_WRITE_COLUMNS",
    "KEYFRAME_READ_COLUMNS",
]


@dataclass(frozen=True, slots=True)
class TableRef:
    """一个湖仓表引用。

    Attributes:
        name: 表名（必须是 registry 里登记过的表；可被同名环境变量覆盖，
            变量名为表名的大写形式）。
        source: 出处说明——原文明确写过的表名会注明篇目与章节。
        inferred: True 表示表名由本项目推断，原文未出现过该字面表名。
        role: 本引擎对这张表做什么（read / write / read-write）。
    """

    name: str
    source: str
    role: str
    inferred: bool = False

    def __str__(self) -> str:
        return self.name

    @property
    def env_key(self) -> str:
        """允许覆盖表名的环境变量名，如 ``ADAS_TABLE_DWD_MINING_RESULT_DETAIL``。"""
        return f"ADAS_TABLE_{self.name.upper()}"

    def resolve(self) -> str:
        """解析最终表名：环境变量优先，否则用默认名。"""
        return os.environ.get(self.env_key, self.name)

    def spec(self):
        """registry 里这张表的 TableSpec。

        注意查的是 :attr:`name` 而不是 :meth:`resolve`——环境变量改的是部署时的
        物理表名，表结构仍然由 registry 那张同名表定义。

        Raises:
            KeyError: 表没登记进 registry。本模块的所有 TableRef 都必须登记，
                这个异常等于一条 import 期的断言。
        """
        return registry.by_name(self.name)


@dataclass(frozen=True, slots=True)
class StreamRef:
    """一个**流**源引用——不是湖仓表，因此不在 registry 的 88 张表里。

    区分的理由很实在：湖仓表是 Paimon 上有 DDL、有主键、有分区策略的持久化对象，
    由 catalog 统一登记；而 CAN 信号这类按采样率来的原始流是 Flink 侧的
    Kafka source / 临时视图，落湖的是**命中结果**而不是全量信号。
    把它伪造成一个 ``dwd_*`` 表名塞进 SQL，只会让「表名是否登记」这条体检失效。

    Attributes:
        name: Flink 侧的源表/视图名，可用 :attr:`env_key` 指定的环境变量覆盖。
        source: 出处说明。
        columns: 本引擎依赖该流提供的列（外部契约，registry 管不到，故在此声明）。
    """

    name: str
    source: str
    columns: frozenset[str]

    def __str__(self) -> str:
        return self.name

    @property
    def env_key(self) -> str:
        """如 ``ADAS_STREAM_VEHICLE_SIGNAL_STREAM``。"""
        return f"ADAS_STREAM_{self.name.upper()}"

    def resolve(self) -> str:
        return os.environ.get(self.env_key, self.name)


# --------------------------------------------------------------------------- 原文明确的表

#: 规则配置入湖表。[S3-04] 一：「规则配置存在挖掘平台的 MySQL，经 Flink CDC 实时同步入湖
#: （ods_mining_rule_config）」；[S3-01] 四同表名。
ODS_MINING_RULE_CONFIG = TableRef(
    "ods_mining_rule_config",
    source="[S3-04] 一、规则即数据 / [S3-01] 四、控制面回流数据面",
    role="read",
)

#: 规则命中结果明细。[S3-04] 三、结果双写：「同时写 dwd_mining_result_detail 供回补闭环消费」。
DWD_MINING_RESULT_DETAIL = TableRef(
    "dwd_mining_result_detail",
    source="[S3-04] 三、批流双模·结果双写",
    role="read-write",
)

#: 执行追溯表。[S3-04] 一：「每次执行记录执行时间、扫描范围、命中数量、写入标签量，
#: 回写 dwd_mining_task_detail」；[S3-01] 四：「任务与审核动作定期回写 dwd_mining_task_detail」。
DWD_MINING_TASK_DETAIL = TableRef(
    "dwd_mining_task_detail",
    source="[S3-04] 一、执行追溯 / [S3-01] 四、控制面回流数据面",
    role="write",
)

#: 场景缺口表。承接 [S3-01] 一的「挖掘双出口」：
#: 「库内命中场景直接回补训练集，零采集成本；未命中才下发定向采集需求」。
#: 表名由本项目任务书给定（dwd_scene_gap_detail），原文正文未出现该字面表名。
DWD_SCENE_GAP_DETAIL = TableRef(
    "dwd_scene_gap_detail",
    source="[S3-01] 一、挖掘双出口（表名取自本项目任务书，原文正文未出现）",
    role="write",
    inferred=True,
)

#: 抽帧结果表。[S3-01] 三：「抽帧结果写入 dwd_mining_image_frame_detail 之后，
#: 规则挖掘与 VLM 推理各自基于该表独立运行」。
DWD_MINING_IMAGE_FRAME_DETAIL = TableRef(
    "dwd_mining_image_frame_detail",
    source="[S3-01] 三、五层应用架构",
    role="read",
)

#: 图片向量表。见本项目共享契约的分区全景（该表按 dt 分区）。
#: rule_priority 驱动的向量化分级（[S3-04] 一）最终落在这张表上。
DWD_MINING_IMAGE_VECTOR_DETAIL = TableRef(
    "dwd_mining_image_vector_detail",
    source="本项目共享契约·分区全景（全湖 6 张分区表之一，按 dt 分区）",
    role="read",
)

#: clip 元信息。[S3-01] 二明确要求复用，不新建：
#: 「平台直接复用采集域既有的 dwd_collect_clip_detail，而不是复制一份自己的」。
DWD_COLLECT_CLIP_DETAIL = TableRef(
    "dwd_collect_clip_detail",
    source="[S3-01] 二、clip 元数据不新建表",
    role="read",
)

#: 车端回传触发事件。[S3-04] 二「事件触发」行：「消费回传触发事件流」。
#: 表名来自本项目共享契约的分区全景（ods_vehicle_trigger_event 按 trigger_type 分区）。
ODS_VEHICLE_TRIGGER_EVENT = TableRef(
    "ods_vehicle_trigger_event",
    source="[S3-04] 二、事件触发 + 本项目共享契约·分区全景（按 trigger_type 分区）",
    role="read",
)

# --------------------------------------------------------------------------- 推断的表

#: 统一标签明细表（clip 级）。⚠️ 原文未给出标签表的字面名字，只说「所有命中统一经
#: 标签服务打标」「经统一标签服务写入标签表」（[S3-04] 二、三）。本项目不另造表名：
#: 标签体系的表归标签域所有，catalog 已登记 clip 级的 dwd_mining_data_tag_detail 与
#: image 级的 dwd_mining_image_tag_detail；规则命中是 clip 粒度，故指向前者。
#: 本引擎【不直接写这张表】——写入一律经 TagService 接口（见 backends.TagService），
#: 这个常量仅供 SQL 校验/对账查询使用。
DWD_MINING_TAG_DETAIL = TableRef(
    "dwd_mining_data_tag_detail",
    source="[S3-04] 二、三「统一标签服务写入标签表」+ 本项目共享契约（标签域 clip 级事实表）",
    role="read",
    inferred=True,
)

#: 图片级标签明细表——VLM 推理双输出（标签 + caption）的落点。
#: [S3-03] 二原文：「管道出口是两张标签事实表：dwd_mining_data_tag_detail（clip 级）
#: 与 dwd_mining_image_tag_detail（image 级）……VLM 生成的关键说明（caption）以
#: tag_category=CAPTION 的特殊标签写入图片标签表」。
#: 规则命中是 clip 粒度落上面那张，VLM 推理是 image 粒度落这一张。
#: 与 clip 级同理：本引擎【不直接写这张表】，写入一律经统一标签服务
#: （见 vlm.ModelTagSink）；这个常量只供列名对账与 SQL 校验用。
DWD_MINING_IMAGE_TAG_DETAIL = TableRef(
    "dwd_mining_image_tag_detail",
    source="[S3-03] 二、管道出口两张标签事实表（image 级）",
    role="read",
)


ALL_REFS: Final[tuple[TableRef, ...]] = (
    ODS_MINING_RULE_CONFIG,
    DWD_MINING_RESULT_DETAIL,
    DWD_MINING_TASK_DETAIL,
    DWD_SCENE_GAP_DETAIL,
    DWD_MINING_IMAGE_FRAME_DETAIL,
    DWD_MINING_IMAGE_VECTOR_DETAIL,
    DWD_COLLECT_CLIP_DETAIL,
    ODS_VEHICLE_TRIGGER_EVENT,
    DWD_MINING_TAG_DETAIL,
    DWD_MINING_IMAGE_TAG_DETAIL,
)

# --------------------------------------------------------------------------- 流源（非湖仓表）

#: CAN / 传感器信号流。[S3-04] 二「车辆信号」行：「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」。
#:
#: ⚠️ 原文未给这条流的名字，本项目取 ``vehicle_signal_stream``，可用环境变量
#: ``ADAS_STREAM_VEHICLE_SIGNAL_STREAM`` 覆盖成部署环境里真实的 Flink 源表名。
#: 它**刻意不是湖仓表**：按采样率来的原始 CAN 信号量级远超命中结果，
#: 全量落湖既无人消费也不在原文的表清单里；规则引擎用 MATCH_RECOGNIZE 直接消费流，
#: 只把命中写进 dwd_mining_result_detail。
#:
#: ``event_ts_ms`` 是本项目为「持续 ≥ 0.5s」这个亚秒级判定专门要求的毫秒时间戳列——
#: Flink 的 TIMESTAMPDIFF 只到秒，用秒去判 0.5s 会恒为 0。
VEHICLE_SIGNAL_STREAM = StreamRef(
    "vehicle_signal_stream",
    source="[S3-04] 二、车辆信号（⚠️ 原文未给流名，本项目推断；非湖仓表，故不在 registry）",
    columns=frozenset(
        {
            "data_id",
            "vehicle_code",
            "project_code",
            "signal_name",
            "signal_value",
            "event_time",
            "event_ts_ms",
        }
    ),
)


def qualified(
    ref: TableRef | str, *, catalog: str | None = None, database: str | None = None
) -> str:
    """渲染反引号限定的三段式表名 ``` `catalog`.`database`.`table` ```。

    catalog/database 默认取 :func:`adas_lakehouse.config.settings` 里的 Paimon 配置，
    这样本地 compose 起来就能直接跑，生产环境走环境变量覆盖。

    Args:
        ref: TableRef 或裸表名。
        catalog: 覆盖 Paimon catalog 名。
        database: 覆盖数据库名。

    Returns:
        形如 ``` `paimon`.`adas_lakehouse`.`dwd_mining_result_detail` ``` 的字符串。
    """
    cfg = settings().paimon
    cat = catalog or cfg.catalog
    db = database or cfg.database
    name = ref.resolve() if isinstance(ref, TableRef) else ref
    return f"`{cat}`.`{db}`.`{name}`"


def stream_sql(ref: StreamRef | str) -> str:
    """渲染流源的 FROM 片段——单段反引号标识符，**不**加 Paimon catalog/database 前缀。

    流源注册在 Flink 的默认 catalog 里（临时表 / Kafka 源），
    套上 Paimon 的三段式前缀反而找不到。
    """
    return f"`{ref.resolve() if isinstance(ref, StreamRef) else ref}`"


# --------------------------------------------------------------------------- 列契约（全部派生自 registry）


def columns_of(ref: TableRef | str) -> tuple[str, ...]:
    """registry 里这张表的全部列名（业务字段 + 该层系统字段），顺序即 DDL 顺序。

    Raises:
        KeyError: 表没登记进 registry。
    """
    spec = ref.spec() if isinstance(ref, TableRef) else registry.by_name(ref)
    return tuple(c.name for c in spec.all_columns())


def business_columns_of(ref: TableRef | str) -> tuple[str, ...]:
    """只要业务字段，不含 ``_ingest_time`` / ``update_time`` / ``_source_system``。

    系统字段由入湖作业自己补（见 config_sync.render_cdc_sync_job），
    业务侧的 SELECT / INSERT 列表不该把它们混进来。
    """
    spec = ref.spec() if isinstance(ref, TableRef) else registry.by_name(ref)
    return tuple(c.name for c in spec.columns)


def projection(ref: TableRef | str, *names: str) -> tuple[str, ...]:
    """声明「本引擎对这张表写/读哪几列、按什么顺序」，并逐列核对 registry。

    这是本模块对「列名清单」唯一允许的写法：顺序由调用方定（INSERT 列表要对位），
    列名的**存在性**由 registry 裁决。写错一个字，import 期就炸。

    Raises:
        KeyError: 表没登记进 registry。
        ValueError: 有列名不在 registry 的该表里，或列名重复。
    """
    known = set(columns_of(ref))
    table = ref.name if isinstance(ref, TableRef) else ref
    missing = [n for n in names if n not in known]
    if missing:
        raise ValueError(
            f"{table} 没有这些列 {missing}——registry（catalog/tables/_*.py）是表结构的唯一事实源，"
            f"要用就先在那里补列，不要在子系统里另立一份"
        )
    if len(set(names)) != len(names):
        raise ValueError(f"{table} 的列投影里有重复列: {names}")
    return tuple(names)


#: 规则命中结果明细的**全部**列（含系统字段）。
MINING_RESULT_COLUMNS: Final[tuple[str, ...]] = columns_of(DWD_MINING_RESULT_DETAIL)

#: 场景缺口明细的全部列。
SCENE_GAP_COLUMNS: Final[tuple[str, ...]] = columns_of(DWD_SCENE_GAP_DETAIL)

#: 执行追溯的全部列。
MINING_TASK_COLUMNS: Final[tuple[str, ...]] = columns_of(DWD_MINING_TASK_DETAIL)

#: 规则配置入湖表的全部列。
RULE_CONFIG_COLUMNS: Final[tuple[str, ...]] = columns_of(ODS_MINING_RULE_CONFIG)


#: 规则命中写入 dwd_mining_result_detail 的列与顺序（编译器的 INSERT 列表逐列对位）。
#: ``mining_task_id`` 与 ``data_id`` 是该表主键，缺一行就 Upsert 不进去。
RESULT_WRITE_COLUMNS: Final[tuple[str, ...]] = projection(
    DWD_MINING_RESULT_DETAIL,
    "mining_task_id",  # 主键之一：本次执行的任务 ID
    "data_id",  # 主键之一：一级 ID，clip 级终身锚点
    "result_id",  # CONCAT(run_id, '_', data_id)，确定性派生故重跑幂等
    "run_id",  # 三级 ID：产出该命中的那次执行
    "artifact_id",  # 二级 ID：命中产物
    "parent_artifact_id",  # 血缘父产物（冗余落表，图库对账兜底）
    "artifact_status",  # active / superseded / invalid
    "rule_id",  # [S3-04] 二：「携带 rule_id 血缘」
    "rule_version",  # 规则版本，命中结果与规则版本绑定
    "rule_category",  # 六大种类之一
    "rule_priority",  # [S3-04] 一：优先级驱动向量化与存储分级
    "exec_mode",  # batch_t_plus_1 / near_realtime
    "hit_type",  # rule 规则粗筛（本引擎恒为 rule）
    "hit_time",  # 命中时刻
    "hit_reason",  # 命中原因（规则条件的人话描述）
    "hit_score",  # 规则命中恒为 1.0（模型细筛才是置信度）
    "event_time",  # 事件规则的事件时刻（非事件规则为 NULL）
    "event_window_start_time",  # 事件窗口起点 = event_time - 15s
    "event_window_end_time",  # 事件窗口终点 = event_time + 5s
    "matched_tag_id",  # 命中的场景标签（经统一标签服务做字典映射）
    "value_score",  # 高价值评分，见 scoring.py
    "value_tier",  # 评分分档，驱动向量化队列
    "vectorize_policy",  # 优先向量化 / 抽样处理（[S3-01] 五）
    "consumed_dataset_id",  # [S3-01] 六：命中结果凭它回补训练集
    "project_code",
    "vehicle_code",
)

#: 场景缺口写入 dwd_scene_gap_detail 的列与顺序。
#: ``project_code`` + ``tag_id`` 是该表主键。
SCENE_GAP_WRITE_COLUMNS: Final[tuple[str, ...]] = projection(
    DWD_SCENE_GAP_DETAIL,
    "project_code",  # 主键之一
    "tag_id",  # 主键之一：场景标签（引擎侧的 scene_label）
    "gap_id",
    "run_id",
    "rule_id",
    "rule_version",
    "target_clip_count",  # 需求侧目标量
    "current_clip_count",  # 库内命中量（零采集成本可直接回补的部分）
    "gap_clip_count",  # 缺口量 = max(0, target - current)
    "coverage_ratio",  # current / target
    "gap_status",  # satisfied / partial / missing
    "gap_severity",  # 缺口严重度评分，驱动定向采集排序
    "consumed_dataset_id",  # 命中部分的回补数据集
    "collect_demand_id",  # 缺口部分下发的定向采集需求 ID
    "last_eval_time",  # 本次评估时刻
)

#: 执行追溯写入 dwd_mining_task_detail 的列与顺序。
#: 四项核心信息逐字对应 [S3-04] 一「执行追溯」：执行时间 / 扫描范围 / 命中数量 / 写入标签量。
MINING_TASK_WRITE_COLUMNS: Final[tuple[str, ...]] = projection(
    DWD_MINING_TASK_DETAIL,
    "mining_task_id",  # 主键
    "run_id",
    "rule_id",
    "rule_version",
    "rule_category",
    "rule_priority",
    "task_type",  # 本引擎恒为 rule_mining
    "exec_mode",
    "engine",  # 批走 spark / 流走 flink
    "project_code",
    "scan_start_time",  # 「扫描范围」——增量水位下界
    "scan_end_time",  # 「扫描范围」——增量水位上界
    "scan_row_count",  # 「扫描范围」——实际扫描行数
    "hit_data_count",  # 「命中数量」
    "tag_write_count",  # 「写入标签量」
    "frame_supplement_triggered",  # 本轮是否异步触发过补抽帧
    "task_status",
    "duration_sec",  # 「执行时间」——耗时
    "start_time",  # 「执行时间」——起
    "end_time",  # 「执行时间」——止
    "error_message",
    "sla_breached",  # 是否突破 4 小时 SLA
)

#: 规则配置从控制面 MySQL 经 CDC 入湖的列（= registry 的全部业务列）。
#: 读规则（RuleConfigLoader）与渲染 CDC 作业（render_cdc_sync_job）共用这一份，
#: 系统字段 ``_ingest_time`` / ``_source_system`` 由 CDC 作业另行补齐。
RULE_CONFIG_CDC_COLUMNS: Final[tuple[str, ...]] = business_columns_of(ODS_MINING_RULE_CONFIG)

# --------------------------------------------------------------------------- VLM 推理侧投影

#: VLM 推理的执行追溯写入列与顺序。
#:
#: 与规则挖掘的 :data:`MINING_TASK_WRITE_COLUMNS` **刻意不是同一份**：推理的产出单位是
#: 图片而不是 clip，所以这里多了 ``hit_image_count``（registry 早有此列），而规则侧特有的
#: ``frame_supplement_triggered`` / ``sla_breached`` 不写——原文的 4 小时 SLA
#: （[S3-04] 三）承诺的是 T+1 批扫描，不是 GPU 推理，拿它去判推理超时是张冠李戴。
#: ``rule_id`` / ``rule_version`` 仍然写：推理候选是规则粗筛的产物，
#: 三级漏斗第一层的血缘不能在第二层断掉（[S3-04] 四）。
VLM_TASK_WRITE_COLUMNS: Final[tuple[str, ...]] = projection(
    DWD_MINING_TASK_DETAIL,
    "mining_task_id",  # 主键
    "run_id",
    "rule_id",  # 把候选圈出来的那条规则（漏斗第一层的血缘）
    "rule_version",
    "rule_category",
    "rule_priority",
    "task_type",  # 恒为 vlm_infer
    "exec_mode",
    "engine",  # 恒为 ray（[S3-01] 三、五）
    "project_code",
    "scan_start_time",  # 候选帧的增量水位下界
    "scan_end_time",  # 上界
    "scan_row_count",  # 扫了多少张候选帧
    "hit_data_count",  # 涉及多少个 clip
    "hit_image_count",  # 成功推理了多少张图（推理的产出单位）
    "tag_write_count",  # 经统一标签服务写入的标签数（含 CAPTION）
    "task_status",
    "duration_sec",
    "start_time",
    "end_time",
    "error_message",
)

#: VLM 双输出经统一标签服务落 dwd_mining_image_tag_detail 的列。
#:
#: 本引擎不拼这张表的 INSERT（写入是标签服务的事），但 payload 的字段名必须**逐字**
#: 落在这份投影里——字段名一旦与 registry 分叉，标签服务那边要么映射失败、
#: 要么写进一列没人读的影子列，而这种错在 Python 侧是完全静默的。
#: 所以 :meth:`~adas_lakehouse.mining.vlm.ImageTagWriteRequest.to_payload`
#: 的键集合由本常量在 import 期校验。
IMAGE_TAG_WRITE_COLUMNS: Final[tuple[str, ...]] = projection(
    DWD_MINING_IMAGE_TAG_DETAIL,
    "image_id",  # 主键之一
    "tag_id",  # 主键之一（归一前先放原始写法，字典映射由标签服务做）
    "tag_source",  # 主键之一：恒为 vlm
    "data_id",  # 所属 clip，让「Badcase 图片 → 原始 clip」是一次主键查询
    "tag_category",  # 结构化标签为业务类别；caption 恒为 CAPTION
    "caption_text",  # 双输出之二（[S3-03] 二）
    "confidence",  # 血缘之一（[S3-03] 二③）
    "rule_id",  # 把这张图圈进推理范围的规则（漏斗第一层血缘）
    "rule_version",
    "model_name",  # 血缘之二
    "model_version",  # 血缘之三
    "infer_job_id",  # 血缘之四
    "run_id",
    "parent_artifact_id",  # 被打标的抽帧图片产物
    "camera_id",
    "source_raw_tag",  # 模型的原始写法，字典归一前留痕
    "review_status",  # 恒为待审：未审核标签不得进入训练集圈选（[S3-03] 四）
    "project_code",
    "vehicle_code",
    "first_tag_time",
)

#: 选帧时从 dwd_mining_image_frame_detail 读的列。
#: [S3-02] 二「推理抽帧」行：「进入 VLM 推理范围的 clip｜每 clip 打分选 1~5 关键帧｜
#: 按清晰度/目标丰富度/时间位置选帧，控制推理成本」——三个分项与总分都读出来，
#: 因为原文要求选帧「可重算复盘」，只读总分就复盘不了。
KEYFRAME_READ_COLUMNS: Final[tuple[str, ...]] = projection(
    DWD_MINING_IMAGE_FRAME_DETAIL,
    "image_id",
    "data_id",
    "artifact_id",  # 它是标签行的 parent_artifact_id
    "camera_id",
    "image_object_key",  # 送进 VLM 的那张图在对象存储里的 key
    "frame_quality_score",  # 选帧三维之一：清晰度
    "object_richness_score",  # 之二：目标丰富度
    "temporal_position_score",  # 之三：时间位置
    "keyframe_score",  # 三维加权综合分
    "is_keyframe",  # 是否被选进 VLM 范围
    "desensitize_status",  # 未脱敏一律不许送进模型
    "frame_timestamp",
    "project_code",
    "vehicle_code",
)
