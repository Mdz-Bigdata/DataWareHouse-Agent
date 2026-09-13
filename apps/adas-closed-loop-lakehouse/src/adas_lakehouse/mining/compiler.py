"""规则编译：把声明式规则变成可执行的查询。

原文（[S3-04] 三、批流双模）把执行链路说得很清楚，编译器要产出的就是这两条腿：

    T+1 批处理：Spark SQL 直接在 Paimon 表上执行，亿级以下数据 4 小时内跑完；
    复杂规则挂自定义 UDF，表达力不受限；
    准实时流：事件类规则以 Flink 消费触发事件流，近实时打标；
    增量扫描：基于 _ingest_time / update_time 水位做增量，避免每次全表回扫。

因此本模块有两个入口：

* :meth:`RuleCompiler.compile_batch` —— Spark 方言，SELECT 挂增量水位谓词；
* :meth:`RuleCompiler.compile_stream` —— Flink 方言，事件流 / MATCH_RECOGNIZE。

两者都产出 :class:`CompiledQuery`，里面既有取候选的 SELECT，也有直接可提交的
``INSERT INTO dwd_mining_result_detail ... SELECT``（原文「结果双写」的湖仓那一路；
标签那一路走统一标签服务，见 :mod:`adas_lakehouse.mining.backends`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..domains import Layer
from ._sqlfmt import SqlRenderError, ident, indent_sql, join_predicates, literal
from .constants import (
    BATCH_SCAN_ROW_CEILING,
    BATCH_SLA_HOURS,
    EVENT_WINDOW_AFTER_SEC,
    EVENT_WINDOW_BEFORE_SEC,
    HARSH_DECEL_MIN_DURATION_SEC,
)
from .rules import (
    ConditionGroup,
    ConditionKind,
    Dialect,
    EventCondition,
    ExecutionMode,
    RuleDefinition,
    RuleType,
    SignalCondition,
)
from .scoring import (
    DEFAULT_WEIGHTS,
    ScoreWeights,
    sql_score_expression,
    sql_tier_expression,
)
from .tables import (
    DWD_COLLECT_CLIP_DETAIL,
    DWD_MINING_IMAGE_FRAME_DETAIL,
    DWD_MINING_RESULT_DETAIL,
    ODS_VEHICLE_TRIGGER_EVENT,
    RESULT_WRITE_COLUMNS,
    VEHICLE_SIGNAL_STREAM,
    StreamRef,
    TableRef,
    columns_of,
    qualified,
    stream_sql,
)
from .watermark import Watermark, incremental_predicate

__all__ = [
    "ScanSource",
    "ScanPlan",
    "CLIP_COLUMNS",
    "FRAME_COLUMNS",
    "TRIGGER_EVENT_COLUMNS",
    "TRIGGER_EVENT_TIME_COLUMN",
    "SIGNAL_STREAM_COLUMNS",
    "SIGNAL_STREAM_TIME_COLUMN",
    "RULE_HIT_TYPE",
    "RULE_HIT_SCORE",
    "default_batch_plan",
    "frame_batch_plan",
    "CompiledQuery",
    "RuleCompiler",
    "CompileError",
    "harsh_decel_reference_sql",
]


class CompileError(SqlRenderError):
    """规则编译失败。继承 SqlRenderError，上层一把捕获即可。"""


# --------------------------------------------------------------------------- 扫描计划

# 下面三组列集合是 **编译期列存在性检查** 的依据（见 ScanSource.provides）。
# 它们一律从 registry 现取，不在这里抄一份——抄一份的下场是：抄错的列在
# strict 模式下把好规则判死、把坏规则放行，SQL 打到真实 Paimon 上才炸。

#: dwd_collect_clip_detail 的业务字段 + DWD 层系统字段。
CLIP_COLUMNS: frozenset[str] = frozenset(columns_of(DWD_COLLECT_CLIP_DETAIL))

#: dwd_mining_image_frame_detail 的列（三级分层抽帧的唯一产物表）。
FRAME_COLUMNS: frozenset[str] = frozenset(columns_of(DWD_MINING_IMAGE_FRAME_DETAIL))

#: ods_vehicle_trigger_event 的列（回传触发事件流的入湖表）。
TRIGGER_EVENT_COLUMNS: frozenset[str] = frozenset(columns_of(ODS_VEHICLE_TRIGGER_EVENT))

#: CAN / 传感器信号流的列。这一份**不**来自 registry——信号流不是湖仓表，
#: 是 Flink 侧的外部流源，契约声明在 tables.VEHICLE_SIGNAL_STREAM 上。
SIGNAL_STREAM_COLUMNS: frozenset[str] = VEHICLE_SIGNAL_STREAM.columns

#: 回传触发事件的「事件时刻」列名。registry 叫 trigger_time——
#: 事件窗口（前 15 后 5 秒）与时效性打分都读这一列，写成 event_time 会找不到列。
TRIGGER_EVENT_TIME_COLUMN: str = "trigger_time"

#: CAN 信号流的「采样时刻」列名。与上一行**不是**同一个名字：
#: 湖仓表 ods_vehicle_trigger_event 叫 trigger_time，Flink 流源叫 event_time
#: （契约见 tables.VEHICLE_SIGNAL_STREAM.columns）。两条流路各读各的列，
#: 混用的后果是作业提交时才报 column not found。
SIGNAL_STREAM_TIME_COLUMN: str = "event_time"


@dataclass(frozen=True, slots=True)
class ScanSource:
    """扫描计划里的一张表。

    Attributes:
        ref: 表引用。
        alias: SQL 别名。
        layer: 所在层级，决定水位列（见 watermark.watermark_column）。
        provides: 该表提供的列名集合，用于编译期的列存在性检查。
        join_on: JOIN 条件；空字符串表示这是基表。
        join_type: JOIN 类型。
    """

    ref: TableRef
    alias: str
    layer: Layer
    provides: frozenset[str]
    join_on: str = ""
    join_type: str = "LEFT JOIN"

    @property
    def is_base(self) -> bool:
        return not self.join_on

    def render(self) -> str:
        table = qualified(self.ref)
        if self.is_base:
            return f"{table} AS {ident(self.alias)}"
        return f"{self.join_type} {table} AS {ident(self.alias)}\n    ON {self.join_on}"


@dataclass(frozen=True, slots=True)
class ScanPlan:
    """一条规则要扫哪些表、怎么 JOIN。

    基表固定是 dwd_collect_clip_detail——原文（[S3-01] 二）明令「clip 元数据不新建表」，
    规则挖掘必须复用采集域既有的那张，而不是复制一份自己的。
    """

    sources: tuple[ScanSource, ...]

    def __post_init__(self) -> None:
        bases = [s for s in self.sources if s.is_base]
        if len(bases) != 1:
            raise CompileError(f"扫描计划必须有且仅有一张基表，当前 {len(bases)} 张")
        aliases = [s.alias for s in self.sources]
        if len(set(aliases)) != len(aliases):
            raise CompileError(f"扫描计划别名重复: {aliases}")

    @property
    def base(self) -> ScanSource:
        return next(s for s in self.sources if s.is_base)

    def provides(self) -> frozenset[str]:
        out: frozenset[str] = frozenset()
        for s in self.sources:
            out |= s.provides
        return out

    def from_sql(self) -> str:
        parts = [self.base.render()]
        parts.extend(s.render() for s in self.sources if not s.is_base)
        return "\n  ".join(parts)

    def with_source(self, source: ScanSource) -> ScanPlan:
        """追加一张 JOIN 表，返回新计划。"""
        return ScanPlan(self.sources + (source,))


def default_batch_plan(alias: str = "clip") -> ScanPlan:
    """默认批扫描计划：只扫 clip 基表。

    规则命中的粒度是 clip（data_id），所以默认不 JOIN 帧表——帧是一对多，
    JOIN 进来会把 clip 级命中炸成帧级重复行。需要帧级条件时用 :func:`frame_batch_plan`
    并自行在 SELECT 里做 DISTINCT / 聚合。
    """
    return ScanPlan(
        (
            ScanSource(
                ref=DWD_COLLECT_CLIP_DETAIL,
                alias=alias,
                layer=Layer.DWD,
                provides=CLIP_COLUMNS,
            ),
        )
    )


def frame_batch_plan(alias: str = "clip", frame_alias: str = "frm") -> ScanPlan:
    """clip 基表 + 抽帧表的扫描计划。

    原文（[S3-01] 三）：「抽帧结果写入 dwd_mining_image_frame_detail 之后，
    规则挖掘与 VLM 推理各自基于该表独立运行」——规则要用帧级条件时走这个计划。
    """
    return default_batch_plan(alias).with_source(
        ScanSource(
            ref=DWD_MINING_IMAGE_FRAME_DETAIL,
            alias=frame_alias,
            layer=Layer.DWD,
            provides=FRAME_COLUMNS,
            join_on=f"{ident(alias)}.`data_id` = {ident(frame_alias)}.`data_id`",
        )
    )


# --------------------------------------------------------------------------- 编译产物


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    """一条规则编译出来的可执行查询。

    Attributes:
        rule_id / rule_version: 规则身份，命中结果据此携带血缘。
        dialect: spark（批）或 flink（流）。
        execution_mode: 批 / 准实时。
        select_sql: 取候选命中的 SELECT。
        insert_sql: ``INSERT INTO dwd_mining_result_detail ... SELECT``，可直接提交。
        count_sql: 只数命中条数的轻量查询，用于 dry-run 与扫描范围预估。
        scan_tables: 本查询涉及的表名。
        watermark: 本轮增量水位（流模式为 None）。
        unresolved_columns: 条件里引用了、但扫描计划没提供的列。
            非致命——规则可能引用了本项目未建模的宽表列，这里只告警，
            由 :meth:`RuleCompiler.compile_batch` 的 strict 参数决定是否升级为异常。
        notes: 编译期提示（如 SLA 与体量约束）。
    """

    rule_id: str
    rule_version: int
    dialect: Dialect
    execution_mode: ExecutionMode
    select_sql: str
    insert_sql: str
    count_sql: str
    scan_tables: tuple[str, ...]
    watermark: Watermark | None = None
    unresolved_columns: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def summary(self) -> str:
        wm = (
            f" 水位[{self.watermark.low:%Y-%m-%d %H:%M:%S} -> {self.watermark.high:%Y-%m-%d %H:%M:%S}]"
            if self.watermark
            else " 流式无水位"
        )
        return (
            f"{self.rule_id} v{self.rule_version} [{self.dialect.value}/"
            f"{self.execution_mode.value}] 扫 {', '.join(self.scan_tables)}{wm}"
        )


# --------------------------------------------------------------------------- 编译器


#: 结果表的投影列顺序——直接用 tables 里那份经 registry 校验过的写入投影，
#: 不在这里再抄一遍列名。
_RESULT_INSERT_COLUMNS: tuple[str, ...] = RESULT_WRITE_COLUMNS

#: 规则命中的 hit_type。同一张结果表有三个写入方（规则粗筛 / VLM 细筛 / 检索扩散），
#: 不标来源就分不清「这条是谁写的」。本引擎恒为 rule。
RULE_HIT_TYPE = "rule"

#: 规则命中的 hit_score 恒为 1.0——规则是确定性判定，要么中要么不中，
#: 没有置信度可言（registry 的列注释：「规则命中为 1.0，模型命中为模型置信度」）。
#: 命中「值不值钱」由 value_score 回答，两者不是一回事。
RULE_HIT_SCORE = 1.0

#: hit_reason 的长度上限。规则条件的人话描述可能很长，截断以免撑爆一列。
_HIT_REASON_MAX_LEN = 500


def _default_task_id(run_id: str, rule_id: str) -> str:
    """``mining_task_id`` 的确定性派生。

    与 :class:`~adas_lakehouse.mining.executor.RuleRunRecord` 的 task_id 同一口径，
    这样 dwd_mining_result_detail 与 dwd_mining_task_detail 能按 mining_task_id 对上。
    """
    return f"{run_id}_{rule_id}"


def _hit_reason(rule: RuleDefinition) -> str:
    """命中原因：规则条件的人话描述（registry 列注释举的例子就是「CAN 减速度 < -4m/s²…」）。"""
    try:
        return rule.condition().describe()[:_HIT_REASON_MAX_LEN]
    except (SqlRenderError, ValueError):  # pragma: no cover - describe 不该炸，炸了也不该拖垮编译
        return rule.rule_name[:_HIT_REASON_MAX_LEN]


def _rule_identity_columns(rule: RuleDefinition) -> list[str]:
    """批流两路共用的「规则身份」投影片段。

    三处类型口径在这里收口（catalog/tables/_mining.py 的「待接线阶段处理的类型口径」）：

    * ``rule_version`` registry 是 STRING，引擎里是 int —— 显式转成字符串字面量，
      不靠 Flink/Spark 的隐式转换（各版本行为不一致）；
    * ``rule_priority`` registry 是 INT，引擎里是 ``P0``/``P1`` 枚举 —— 写 ``rank``；
    * ``rule_category`` / ``exec_mode`` 用 registry 的列名，取值仍是引擎的枚举值。
    """
    return [
        f"{literal(str(rule.rule_version))} AS `rule_version`",
        f"{literal(rule.rule_type.value)} AS `rule_category`",
        f"{literal(rule.rule_priority.rank)} AS `rule_priority`",
        f"{literal(rule.effective_mode.value)} AS `exec_mode`",
        f"{literal(RULE_HIT_TYPE)} AS `hit_type`",
    ]


@dataclass(slots=True)
class RuleCompiler:
    """规则 → SQL 编译器。

    Args:
        plan: 批扫描计划，默认只扫 clip 基表。
        weights: 评分权重，透传给 scoring.sql_score_expression。
        catalog / database: Paimon catalog 与库名，默认取 config.settings()。
    """

    plan: ScanPlan = field(default_factory=default_batch_plan)
    weights: ScoreWeights = DEFAULT_WEIGHTS
    catalog: str | None = None
    database: str | None = None

    # ---- 公共 ----

    @staticmethod
    def _task_ref(rule: RuleDefinition, run_id: str, task_id: str) -> str:
        """渲染 ``mining_task_id`` 字面量。

        它是 dwd_mining_result_detail 的主键之一且 NOT NULL——不写这一列，
        整批命中在 Paimon 上连主键都拼不出来。
        """
        tid = task_id or (_default_task_id(run_id, rule.rule_id) if run_id else "")
        return literal(tid) if tid else ":mining_task_id"

    # ---- 批 ----

    def compile_batch(
        self,
        rule: RuleDefinition,
        watermark: Watermark,
        *,
        run_id: str = "",
        task_id: str = "",
        existing_hit_count: int = 0,
        now: datetime | None = None,
        strict: bool = False,
        estimated_rows: int | None = None,
    ) -> CompiledQuery:
        """编译 T+1 批查询（Spark SQL on Paimon）。

        Args:
            rule: 待编译规则，必须是批模式。
            watermark: 本轮增量水位，渲染成 WHERE 里的时间区间谓词。
            run_id: 本次执行的三级 ID，写进结果行；留空则 SQL 里用占位符 ``:run_id``。
            task_id: 本次执行的 ``mining_task_id``（结果表主键之一）。留空则按
                ``{run_id}_{rule_id}`` 派生，run_id 也留空时用占位符 ``:mining_task_id``。
            existing_hit_count: 该场景库内已有命中量，喂给稀缺度打分。
            now: 参考时刻。
            strict: True 时，条件引用了扫描计划未提供的列就直接报错。
            estimated_rows: 预估扫描行数，用于对照原文的「亿级以下 4 小时」约束。

        Returns:
            CompiledQuery。

        Raises:
            CompileError: 规则不是批模式、条件非法、或 strict 下有未解析列。
        """
        if rule.effective_mode is not ExecutionMode.BATCH_T_PLUS_1:
            raise CompileError(
                f"规则 {rule.rule_id} 的执行模式是 {rule.effective_mode.value}，不能编译成批查询；"
                f"原文规定「{rule.rule_type.spec.condition_note}」"
            )
        alias = self.plan.base.alias
        try:
            predicate = rule.where_sql(Dialect.SPARK, alias=alias)
        except SqlRenderError as exc:
            raise CompileError(f"规则 {rule.rule_id} 条件编译失败: {exc}") from exc

        unresolved = self._unresolved_columns(rule)
        if unresolved and strict:
            raise CompileError(
                f"规则 {rule.rule_id} 引用了扫描计划未提供的列: {sorted(unresolved)}；"
                f"请扩展 ScanPlan 或修正规则条件"
            )

        wm_pred = incremental_predicate(watermark, alias=alias)
        where = join_predicates([wm_pred, predicate], "AND")

        score_expr = sql_score_expression(
            rule,
            alias=alias,
            existing_hit_count=existing_hit_count,
            now=now,
            weights=self.weights,
        )
        run_ref = literal(run_id) if run_id else ":run_id"
        task_ref = self._task_ref(rule, run_id, task_id)

        select_sql = self._render_batch_select(rule, alias, where, score_expr, run_ref, task_ref)
        insert_sql = self._wrap_insert(select_sql)
        count_sql = f"SELECT COUNT(1) AS hit_count\nFROM {self.plan.from_sql()}\nWHERE {where}"

        notes = self._batch_notes(rule, estimated_rows)
        return CompiledQuery(
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
            dialect=Dialect.SPARK,
            execution_mode=ExecutionMode.BATCH_T_PLUS_1,
            select_sql=select_sql,
            insert_sql=insert_sql,
            count_sql=count_sql,
            scan_tables=tuple(s.ref.resolve() for s in self.plan.sources),
            watermark=watermark,
            unresolved_columns=tuple(sorted(unresolved)),
            notes=notes,
        )

    def _render_batch_select(
        self,
        rule: RuleDefinition,
        alias: str,
        where: str,
        score_expr: str,
        run_ref: str,
        task_ref: str,
    ) -> str:
        a = ident(alias)
        cols = [
            f"{task_ref} AS `mining_task_id`",
            f"{a}.`data_id` AS `data_id`",
            # result_id：run_id + data_id 的确定性拼接，重跑幂等（ids 模块规则一）
            f"CONCAT({run_ref}, '_', {a}.`data_id`) AS `result_id`",
            f"{run_ref} AS `run_id`",
            # artifact_id 由 Python 侧按 ids.derive_artifact_id 生成，SQL 侧留空占位
            "CAST(NULL AS STRING) AS `artifact_id`",
            "CAST(NULL AS STRING) AS `parent_artifact_id`",
            f"{literal('active')} AS `artifact_status`",
            f"{literal(rule.rule_id)} AS `rule_id`",
            *_rule_identity_columns(rule),
            "CURRENT_TIMESTAMP AS `hit_time`",
            f"{literal(_hit_reason(rule))} AS `hit_reason`",
            f"{literal(RULE_HIT_SCORE)} AS `hit_score`",
            # 批模式的规则不是事件驱动的，事件窗口三列为空
            "CAST(NULL AS TIMESTAMP) AS `event_time`",
            "CAST(NULL AS TIMESTAMP) AS `event_window_start_time`",
            "CAST(NULL AS TIMESTAMP) AS `event_window_end_time`",
            f"{literal(rule.scene_label)} AS `matched_tag_id`",
            f"{score_expr} AS `value_score`",
            f"{literal(rule.vectorize_policy.value)} AS `vectorize_policy`",
            "CAST(NULL AS STRING) AS `consumed_dataset_id`",
            f"{a}.`project_code` AS `project_code`",
            f"{a}.`vehicle_code` AS `vehicle_code`",
        ]
        header = (
            f"-- 规则挖掘 · T+1 批（Spark SQL on Paimon）\n"
            f"-- 规则: {rule.rule_id} v{rule.rule_version} 「{rule.rule_name}」 "
            f"{rule.rule_type.name_cn}\n"
            f"-- 原文条件与执行: {rule.rule_type.spec.condition_note}\n"
            f"-- SLA: 亿级（{BATCH_SCAN_ROW_CEILING}）以下数据 {BATCH_SLA_HOURS} 小时内跑完（[S3-04] 三）"
        )
        inner = (
            "SELECT\n"
            + ",\n".join(indent_sql(c) for c in cols)
            + f"\nFROM {self.plan.from_sql()}\nWHERE {where}"
        )
        return self._wrap_tier(inner, header)

    def _wrap_tier(self, inner_sql: str, header: str) -> str:
        """外层套一圈分档投影。

        value_tier 是 value_score 的分段函数。若与 value_score 写在同一层 SELECT 里，
        整个打分表达式要被复制三遍（三个分档阈值各一次）——Spark 能算对，但 SQL 难读，
        Flink 也没有同层列别名引用。所以内层只算分，外层只分档。
        输出列顺序严格对齐 _RESULT_INSERT_COLUMNS，INSERT 才对得上号。
        """
        cols: list[str] = []
        for c in _RESULT_INSERT_COLUMNS:
            if c == "value_tier":
                cols.append(f"{sql_tier_expression('`t`.`value_score`')} AS `value_tier`")
            else:
                cols.append(f"`t`.`{c}` AS `{c}`")
        return (
            f"{header}\nSELECT\n"
            + ",\n".join(indent_sql(c) for c in cols)
            + "\nFROM (\n"
            + indent_sql(inner_sql)
            + "\n) AS `t`"
        )

    def _wrap_insert(self, select_sql: str) -> str:
        target = qualified(DWD_MINING_RESULT_DETAIL, catalog=self.catalog, database=self.database)
        col_list = ", ".join(f"`{c}`" for c in _RESULT_INSERT_COLUMNS)
        return f"INSERT INTO {target}\n  ({col_list})\n{select_sql}"

    def _unresolved_columns(self, rule: RuleDefinition) -> set[str]:
        """条件引用了、但扫描计划没提供的列。

        方言写死成 SPARK 是因为本方法只服务批路（``compile_batch``）——流路的列检查
        在 :meth:`_assert_stream_columns` 里按 FLINK 方言另做。传错方言的代价不是抽象的：
        带持续时长的信号条件在两个方言下读的列根本不是同一组（见
        :meth:`~adas_lakehouse.mining.rules.SignalCondition.referenced_columns`）。
        """
        cond = rule.condition()
        if cond.kind is ConditionKind.RAW_SQL:
            # 手写 SQL 的列名只能靠正则粗提，误报率高，不做检查
            return set()
        return {c for c in cond.referenced_columns(Dialect.SPARK) if c not in self.plan.provides()}

    def _batch_notes(self, rule: RuleDefinition, estimated_rows: int | None) -> tuple[str, ...]:
        notes = [
            f"原文 SLA：亿级（{BATCH_SCAN_ROW_CEILING} 行）以下数据 {BATCH_SLA_HOURS} 小时内跑完",
        ]
        if estimated_rows is not None and estimated_rows > BATCH_SCAN_ROW_CEILING:
            notes.append(
                f"⚠️ 预估扫描 {estimated_rows} 行，已超出原文承诺的亿级上界 "
                f"{BATCH_SCAN_ROW_CEILING}，{BATCH_SLA_HOURS} 小时 SLA 不再适用——"
                "请收紧水位区间或加分区裁剪"
            )
        if rule.rule_type is RuleType.COMPOSITE:
            notes.append("复合规则含信号条件时会下推自定义 UDF（[S3-04] 三：复杂规则挂自定义 UDF）")
        return tuple(notes)

    # ---- 流 ----

    def compile_stream(
        self,
        rule: RuleDefinition,
        *,
        run_id: str = "",
        task_id: str = "",
        existing_hit_count: int = 0,
        now: datetime | None = None,
        source_table: TableRef | StreamRef | None = None,
        source_alias: str = "evt",
    ) -> CompiledQuery:
        """编译准实时流查询（Flink SQL）。

        两种形态，按规则种类分流：

        * 事件触发类 —— 直接消费回传触发事件流，算出前 15 后 5 秒窗口
          （[S3-04] 二、三，窗口秒数取自 constants，不可配）；
        * 车辆信号类（带持续时长）—— MATCH_RECOGNIZE 模式匹配，
          表达「CAN 减速度 < -4m/s² **持续** ≥ 0.5s」里的那个「持续」。

        Args:
            rule: 待编译规则，必须是准实时模式。
            run_id: 三级 ID；留空用占位符。
            task_id: 本次执行的 ``mining_task_id``（结果表主键之一），口径同
                :meth:`compile_batch`。
            existing_hit_count: 稀缺度分母来源。
            now: 参考时刻。
            source_table: 覆盖源；默认按规则种类选 ods_vehicle_trigger_event（湖仓表）
                或 tables.VEHICLE_SIGNAL_STREAM（Flink 流源）。
            source_alias: 源表别名。

        Raises:
            CompileError: 规则不是准实时模式，或种类不受流路支持。
        """
        if rule.effective_mode is not ExecutionMode.NEAR_REALTIME:
            raise CompileError(
                f"规则 {rule.rule_id} 的执行模式是 {rule.effective_mode.value}，不能编译成流查询"
            )
        task_ref = self._task_ref(rule, run_id, task_id)
        signal_cond = self._find_signal_condition(rule)
        # 源的选择跟着**条件来源**走，不跟着「有没有 min_duration_sec」走。
        # 原文（[S3-04] 二）「车辆信号」行给的两个示例是「急减速 / 急变道」：急减速带
        # 「持续 ≥ 0.5s」，急变道是瞬时横向加速度尖峰、并不必然带持续时长。
        # 以前这里按「有无持续时长」分流，于是不带时长的车辆信号规则被送去扫
        # ods_vehicle_trigger_event——那张表里根本没有 signal_name / signal_value，
        # 作业提交时才报 column not found。CAN 信号一律读信号流，持续性只决定**怎么匹配**。
        source: TableRef | StreamRef
        if signal_cond is not None:
            source = source_table or VEHICLE_SIGNAL_STREAM
            if signal_cond.min_duration_sec is not None:
                select_sql = self._render_match_recognize(
                    rule,
                    signal_cond,
                    run_id,
                    task_ref,
                    existing_hit_count,
                    now,
                    source_alias,
                    source,
                )
            else:
                select_sql = self._render_event_select(
                    rule,
                    run_id,
                    task_ref,
                    existing_hit_count,
                    now,
                    source,
                    source_alias,
                    time_column=SIGNAL_STREAM_TIME_COLUMN,
                )
        else:
            source = source_table or ODS_VEHICLE_TRIGGER_EVENT
            select_sql = self._render_event_select(
                rule, run_id, task_ref, existing_hit_count, now, source, source_alias
            )
        self._assert_stream_columns(rule, source)
        scan = source.resolve()

        insert_sql = self._wrap_insert(select_sql)
        return CompiledQuery(
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
            dialect=Dialect.FLINK,
            execution_mode=ExecutionMode.NEAR_REALTIME,
            select_sql=select_sql,
            insert_sql=insert_sql,
            count_sql="-- 流式查询无界，不提供 COUNT",
            scan_tables=(scan,),
            watermark=None,
            notes=(
                f"事件窗口固定前 {EVENT_WINDOW_BEFORE_SEC} 秒、后 {EVENT_WINDOW_AFTER_SEC} 秒"
                "（[S3-04] 二、三），命中后异步触发补抽帧加密采样",
            ),
        )

    @staticmethod
    def _find_signal_condition(rule: RuleDefinition) -> SignalCondition | None:
        cond = rule.condition()
        if isinstance(cond, SignalCondition):
            return cond
        if isinstance(cond, ConditionGroup):
            for node in cond.walk():
                if isinstance(node, SignalCondition):
                    return node
        return None

    @staticmethod
    def _find_event_condition(rule: RuleDefinition) -> EventCondition | None:
        cond = rule.condition()
        if isinstance(cond, EventCondition):
            return cond
        if isinstance(cond, ConditionGroup):
            for node in cond.walk():
                if isinstance(node, EventCondition):
                    return node
        return None

    def _stream_projection(
        self,
        rule: RuleDefinition,
        alias: str,
        *,
        event_time_expr: str,
        window_start_expr: str,
        window_end_expr: str,
        score_expr: str,
        run_ref: str,
        task_ref: str,
    ) -> list[str]:
        """流路的内层投影（不含 value_tier，分档由 _wrap_tier 在外层做）。"""
        a = ident(alias)
        return [
            f"{task_ref} AS `mining_task_id`",
            f"{a}.`data_id` AS `data_id`",
            f"CONCAT({run_ref}, '_', {a}.`data_id`) AS `result_id`",
            f"{run_ref} AS `run_id`",
            "CAST(NULL AS STRING) AS `artifact_id`",
            "CAST(NULL AS STRING) AS `parent_artifact_id`",
            f"{literal('active')} AS `artifact_status`",
            f"{literal(rule.rule_id)} AS `rule_id`",
            *_rule_identity_columns(rule),
            "CURRENT_TIMESTAMP AS `hit_time`",
            f"{literal(_hit_reason(rule))} AS `hit_reason`",
            f"{literal(RULE_HIT_SCORE)} AS `hit_score`",
            f"{event_time_expr} AS `event_time`",
            f"{window_start_expr} AS `event_window_start_time`",
            f"{window_end_expr} AS `event_window_end_time`",
            f"{literal(rule.scene_label)} AS `matched_tag_id`",
            f"{score_expr} AS `value_score`",
            f"{literal(rule.vectorize_policy.value)} AS `vectorize_policy`",
            "CAST(NULL AS STRING) AS `consumed_dataset_id`",
            f"{a}.`project_code` AS `project_code`",
            f"{a}.`vehicle_code` AS `vehicle_code`",
        ]

    def _render_event_select(
        self,
        rule: RuleDefinition,
        run_id: str,
        task_ref: str,
        existing_hit_count: int,
        now: datetime | None,
        source_table: TableRef | StreamRef | None,
        alias: str,
        *,
        time_column: str = "",
    ) -> str:
        evt_cond = self._find_event_condition(rule)
        a = ident(alias)
        # 事件时刻列名取自 registry（ods_vehicle_trigger_event.trigger_time），不是 event_time——
        # 写错这一列，Flink 作业提交时才报「column not found」。
        # ``time_column`` 由调用方在源不是事件表时显式指定（CAN 信号流用 event_time）。
        time_col_name = time_column or (
            evt_cond.event_time_column if evt_cond is not None else TRIGGER_EVENT_TIME_COLUMN
        )
        time_col = f"{a}.{ident(time_col_name)}"
        if evt_cond is not None and not time_column:
            start_expr, end_expr = evt_cond.window_sql(alias=alias)
        else:
            # 两种情况走这里，窗口都按原文的前 15 后 5 秒自行算：
            #   · 模型输出类走准实时时压根没有 EventCondition；
            #   · 调用方指定了 time_column（源是 CAN 信号流），此时即便树里还挂着
            #     EventCondition，也不能用它的 trigger_time——那一列在流里不存在。
            start_expr = f"{time_col} - INTERVAL '{EVENT_WINDOW_BEFORE_SEC}' SECOND"
            end_expr = f"{time_col} + INTERVAL '{EVENT_WINDOW_AFTER_SEC}' SECOND"

        predicate = rule.where_sql(Dialect.FLINK, alias=alias)
        score_expr = sql_score_expression(
            rule,
            alias=alias,
            existing_hit_count=existing_hit_count,
            time_column=time_col_name,
            now=now,
            weights=self.weights,
        )
        run_ref = literal(run_id) if run_id else ":run_id"
        cols = self._stream_projection(
            rule,
            alias,
            event_time_expr=time_col,
            window_start_expr=start_expr,
            window_end_expr=end_expr,
            score_expr=score_expr,
            run_ref=run_ref,
            task_ref=task_ref,
        )
        src = self._source_sql(source_table or ODS_VEHICLE_TRIGGER_EVENT)
        header = (
            f"-- 规则挖掘 · 准实时流（Flink 消费回传触发事件流）\n"
            f"-- 规则: {rule.rule_id} v{rule.rule_version} 「{rule.rule_name}」 {rule.rule_type.name_cn}\n"
            f"-- 原文条件与执行: {rule.rule_type.spec.condition_note}\n"
            f"-- 事件窗口: 前 {EVENT_WINDOW_BEFORE_SEC} 秒 / 后 {EVENT_WINDOW_AFTER_SEC} 秒（补抽帧加密采样区间）"
        )
        inner = (
            "SELECT\n"
            + ",\n".join(indent_sql(c) for c in cols)
            + f"\nFROM {src} AS {a}\nWHERE {predicate}"
        )
        return self._wrap_tier(inner, header)

    def _source_sql(self, ref: TableRef | StreamRef) -> str:
        """渲染 FROM 片段：湖仓表走三段式 Paimon 限定名，流源走单段标识符。"""
        if isinstance(ref, StreamRef):
            return stream_sql(ref)
        return qualified(ref, catalog=self.catalog, database=self.database)

    #: 流路投影恒定要读的三列（见 :meth:`_stream_projection`）。少一列，
    #: 结果行就拼不出主键或补不齐维度。
    _STREAM_REQUIRED_COLUMNS: frozenset[str] = frozenset(
        {"data_id", "project_code", "vehicle_code"}
    )

    def _assert_stream_columns(self, rule: RuleDefinition, source: TableRef | StreamRef) -> None:
        """流路的列存在性检查——批路有（``_unresolved_columns``），流路以前没有。

        没有这道检查，一条车辆信号规则被送去扫事件表这种源选错的 bug，
        要等 Flink 作业真正提交、真正解析 SQL 时才暴露；而准实时作业是长驻的，
        提交失败往往只在网关日志里留一行，没人盯着。

        流路的检查必须是**硬失败**而不是像批路那样默认只告警：批路的候选行拉回来
        还要过 Python 侧打分，列缺了顶多少一个打分因子；流路的 SQL 是直接丢给
        Flink 长跑的，编译期放过去就等于线上静默不产出。

        Raises:
            CompileError: 条件或投影引用了该源没有声明的列。
        """
        if isinstance(source, StreamRef):
            provided = source.columns
        else:
            provided = frozenset(columns_of(source))
        cond = rule.condition()
        needed = set(self._STREAM_REQUIRED_COLUMNS)
        if cond.kind is not ConditionKind.RAW_SQL:
            # 流路一律 FLINK 方言——传 SPARK 会把带持续时长的信号条件算成 UDF 的入参列
            needed |= cond.referenced_columns(Dialect.FLINK)
        missing = sorted(c for c in needed if c not in provided)
        if missing:
            raise CompileError(
                f"规则 {rule.rule_id} 的流源 {source.resolve()!r} 没有这些列 {missing}；"
                f"该源声明的列是 {sorted(provided)}。"
                f"检查规则种类与源是否配对（车辆信号读 CAN 信号流，事件触发读回传事件表）"
            )

    def _render_match_recognize(
        self,
        rule: RuleDefinition,
        cond: SignalCondition,
        run_id: str,
        task_ref: str,
        existing_hit_count: int,
        now: datetime | None,
        alias: str,
        source_table: TableRef | StreamRef | None = None,
    ) -> str:
        """渲染「持续 ≥ N 秒」的 MATCH_RECOGNIZE。

        原文（[S3-04] 二）：「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」。
        Flink 的 ``TIMESTAMPDIFF`` 最小单位是秒，判不了 0.5 秒，因此持续时长用
        毫秒时间戳列 ``event_ts_ms`` 作差，阈值写成 ``0.5 * 1000``——
        原文的 0.5 逐字留在 SQL 里，不预先算成 500。

        ⚠️ 原文未明确，本项目设计：``event_ts_ms`` 列、MATCH_RECOGNIZE 的模式写法
        （``A+ B``，以「首个不再满足条件的样本」收尾）都是本项目的落地方案。
        """
        min_dur = cond.min_duration_sec
        assert min_dur is not None  # 调用方已判
        a = ident(alias)
        run_ref = literal(run_id) if run_id else ":run_id"
        name_lit = literal(cond.signal)
        thr_lit = literal(float(cond.threshold))
        op = cond.op.sql
        # A 满足条件，B 是第一条不再满足的样本，用来封住持续区间
        inverse = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "=": "<>", "<>": "="}[op]

        score_expr = sql_score_expression(
            rule,
            alias="m",
            existing_hit_count=existing_hit_count,
            time_column="anchor_time",
            # MATCH_RECOGNIZE 把实测值聚合成了 peak_value，严重度要读这一列
            signal_value_column="peak_value",
            now=now,
            weights=self.weights,
        )
        cols = self._stream_projection(
            rule,
            "m",
            event_time_expr="`m`.`anchor_time`",
            window_start_expr=f"`m`.`anchor_time` - INTERVAL '{EVENT_WINDOW_BEFORE_SEC}' SECOND",
            window_end_expr=f"`m`.`anchor_time` + INTERVAL '{EVENT_WINDOW_AFTER_SEC}' SECOND",
            score_expr=score_expr,
            run_ref=run_ref,
            task_ref=task_ref,
        )
        src = self._source_sql(source_table or VEHICLE_SIGNAL_STREAM)
        header = (
            f"-- 规则挖掘 · 准实时流（Flink MATCH_RECOGNIZE 持续时长判定）\n"
            f"-- 规则: {rule.rule_id} v{rule.rule_version} 「{rule.rule_name}」 {rule.rule_type.name_cn}\n"
            f"-- 原文条件与执行: {rule.rule_type.spec.condition_note}\n"
            f"-- 阈值 {cond.threshold} 与持续时长 {min_dur}s 逐字取自原文，见 constants.py"
        )
        projection = ",\n".join(indent_sql(c) for c in cols)
        inner = f"""SELECT
{projection}
FROM (
  SELECT *
  FROM {src} AS {a}
  MATCH_RECOGNIZE (
    PARTITION BY `data_id`, `vehicle_code`, `project_code`
    ORDER BY `event_time`
    MEASURES
      FIRST(A.`event_time`)  AS `anchor_time`,
      FIRST(A.`event_ts_ms`) AS `sustain_start_ms`,
      LAST(A.`event_ts_ms`)  AS `sustain_end_ms`,
      MIN(A.`signal_value`)  AS `peak_value`,
      COUNT(A.`event_ts_ms`) AS `sample_count`
    ONE ROW PER MATCH
    AFTER MATCH SKIP PAST LAST ROW
    PATTERN (A+ B)
    DEFINE
      A AS A.`signal_name` = {name_lit} AND A.`signal_value` {op} {thr_lit},
      B AS B.`signal_name` = {name_lit} AND B.`signal_value` {inverse} {thr_lit}
  )
) AS `m`
-- 持续 ≥ {min_dur}s：毫秒作差，阈值保留原文的 {min_dur} 不预乘
WHERE (`m`.`sustain_end_ms` - `m`.`sustain_start_ms`) >= ({min_dur} * 1000)"""
        return self._wrap_tier(inner, header)

    # ---- 批量 ----

    def compile_many(
        self,
        rules: Sequence[RuleDefinition],
        *,
        watermarks: dict[str, Watermark] | None = None,
        run_id: str = "",
        now: datetime | None = None,
    ) -> tuple[list[CompiledQuery], list[tuple[str, str]]]:
        """批量编译，坏规则不拖垮整批。

        Args:
            rules: 规则列表。
            watermarks: ``{rule_id: Watermark}``，批模式规则必须能查到自己的水位。
            run_id: 三级 ID。
            now: 参考时刻。

        Returns:
            ``(编译成功的查询, [(rule_id, 错误原因)])``。
        """
        ok: list[CompiledQuery] = []
        failed: list[tuple[str, str]] = []
        marks = watermarks or {}
        for rule in rules:
            try:
                if rule.effective_mode is ExecutionMode.BATCH_T_PLUS_1:
                    wm = marks.get(rule.rule_id)
                    if wm is None:
                        raise CompileError(f"批规则 {rule.rule_id} 缺少水位，无法做增量扫描")
                    ok.append(self.compile_batch(rule, wm, run_id=run_id, now=now))
                else:
                    ok.append(self.compile_stream(rule, run_id=run_id, now=now))
            except (CompileError, SqlRenderError, ValueError) as exc:
                failed.append((rule.rule_id, str(exc)))
        return ok, failed


def harsh_decel_reference_sql() -> str:
    """把原文那条唯一带数字的规则渲染成参考 SQL，供文档与冒烟测试引用。

    原文（[S3-04] 二）：「CAN 减速度 < -4m/s² 持续 ≥ 0.5s 等，准实时」。
    """
    from .rules import SAMPLE_RULES

    rule = next(r for r in SAMPLE_RULES if r.rule_id == "RULE_SIGNAL_HARSH_DECEL")
    assert rule.condition().to_dict()["min_duration_sec"] == HARSH_DECEL_MIN_DURATION_SEC
    return RuleCompiler().compile_stream(rule, run_id="run_mining_20260912000000_demo").select_sql
