"""批流双模执行：把编译好的查询真正跑起来，并把执行追溯写回湖仓。

原文（[S3-04] 三、批流双模：执行与调度链路）：

    T+1 批处理：Spark SQL 直接在 Paimon 表上执行，亿级以下数据 4 小时内跑完；
    复杂规则挂自定义 UDF，表达力不受限；
    准实时流：事件类规则以 Flink 消费触发事件流，近实时打标——接管、AEB 这类事件
    不用等第二天；
    增量扫描：基于 _ingest_time / update_time 水位做增量，避免每次全表回扫；
    结果双写：命中结果一律经统一标签服务写入标签表（完成字典映射与去重），
    同时写 dwd_mining_result_detail 供回补闭环消费。

以及隐藏联动：

    事件命中会异步触发补抽帧……规则识别出接管事件，事件抽帧引擎立刻回头对
    前 15 后 5 秒窗口加密采样，两个引擎经湖仓表解耦协作，谁也不阻塞谁。

以及执行追溯（[S3-04] 一）：

    每次执行记录执行时间、扫描范围、命中数量、写入标签量，回写
    dwd_mining_task_detail——规则的效果可度量，而不是配完就黑盒。

:class:`RuleRunRecord` 的字段就是照这四项逐字对齐的。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..ids import ArtifactStatus, derive_artifact_id, new_run_id
from .backends import (
    BackendError,
    BackfillRequest,
    FrameBackfillDispatcher,
    ResultSink,
    SqlBackend,
    TagService,
    TagWriteRequest,
)
from .compiler import CompiledQuery, CompileError, RuleCompiler
from .constants import (
    BATCH_SCAN_ROW_CEILING,
    BATCH_SLA_HOURS,
    BATCH_SLA_SECONDS,
    EVENT_WINDOW_AFTER_SEC,
    EVENT_WINDOW_BEFORE_SEC,
)
from .rules import (
    ConditionGroup,
    ExecutionMode,
    RuleDefinition,
    RuleType,
    SignalCondition,
    SignalSample,
    rules_by_mode,
    sort_by_priority,
    sustained_matches,
)
from .scoring import (
    DEFAULT_WEIGHTS,
    ScoreBreakdown,
    ScoreWeights,
    resolve_vectorize_policy,
    score_hit,
)
from .tables import (
    DWD_MINING_TASK_DETAIL,
    MINING_TASK_WRITE_COLUMNS,
    qualified,
)
from .watermark import (
    DEFAULT_LOOKBACK_MINUTES,
    InMemoryWatermarkStore,
    WatermarkStore,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FETCH_ROWS",
    "MINING_STAGE",
    "TaskStatus",
    "RuleRunRecord",
    "ExecutionReport",
    "BatchRuleExecutor",
    "StreamRuleExecutor",
    "task_insert_sql",
]

#: 一轮批处理最多往 Python 侧拉多少行做打分与打标。
#: ⚠️ 原文未明确，本项目设计：原文说批处理直接在 Paimon 上跑 Spark SQL，没说命中结果
#: 怎么落到标签服务。本引擎的默认路径是「拉回来算分 + 调标签服务」，因此需要一个上界——
#: 超过就说明规则圈得太宽，应该收紧条件，而不是把几百万行拖进单机内存。
#: 真要处理超大结果集，走 :meth:`BatchRuleExecutor.execute_pushdown`（纯 SQL 落表）。
MAX_FETCH_ROWS = 200_000

#: 三级 ID 里的 stage 段。所有规则挖掘的 run_id 形如 ``run_mining_<ts>_<seq>``。
MINING_STAGE = "mining"

#: artifact_id 里的 stage 段与算法版本。
#: ⚠️ 原文未明确，本项目设计：原文没说规则命中要不要登记成产物。本项目登记——
#: 这样重刷（规则改版重跑）时旧命中标 superseded、新命中另起 artifact_id，
#: 与 ids 模块的规则三保持一致，也让 parent_artifact_id 血缘链不断。
ARTIFACT_STAGE = "mining"
ARTIFACT_ALGO_VERSION = "v1"

#: dwd_mining_task_detail.task_type 的取值。挖掘域四类任务（规则挖掘 / 抽帧 / 推理 /
#: 向量化）共用这张追溯表，本执行器写的永远是规则挖掘那一类。
TASK_TYPE_RULE_MINING = "rule_mining"


class TaskStatus(str, Enum):
    """执行状态。"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class RuleRunRecord:
    """一次规则执行的追溯记录——落 dwd_mining_task_detail 的那一行。

    字段逐字对齐原文（[S3-04] 一、执行追溯）要求记录的四项：

    ==================  ==========================================
    原文要求            本类字段
    ==================  ==========================================
    执行时间            ``executed_at`` / ``finished_at`` / ``elapsed_seconds``
    扫描范围            ``scan_low_watermark`` / ``scan_high_watermark`` / ``scanned_row_count``
    命中数量            ``hit_count``
    写入标签量          ``tag_written_count``
    ==================  ==========================================

    字段名是**引擎侧的领域名**，落表时由 :meth:`to_row` 翻成 registry 的列名
    （``task_id -> mining_task_id``、``executed_at -> start_time`` 等，
    映射表见 catalog/tables/_mining.py 的模块 docstring）。表结构以 registry 为准。
    """

    task_id: str
    run_id: str
    rule_id: str
    rule_version: int
    execution_mode: ExecutionMode
    executed_at: datetime
    finished_at: datetime | None = None
    elapsed_seconds: float = 0.0
    scan_low_watermark: datetime | None = None
    scan_high_watermark: datetime | None = None
    scanned_row_count: int = 0
    hit_count: int = 0
    tag_written_count: int = 0
    task_status: TaskStatus = TaskStatus.RUNNING
    error_message: str = ""
    sla_breached: bool = False
    #: 本次命中里触发了多少条补抽帧（事件类规则才会非零）
    backfill_dispatched: int = 0
    #: 规则画像三列，落 rule_category / rule_priority / project_code。
    #: 有它们，执行追溯表才答得出「哪一档规则、哪个项目跑了多久」，
    #: 而不必每次回 join ods_mining_rule_config。
    rule_category: str = ""
    rule_priority: int | None = None
    project_code: str = ""

    @property
    def engine(self) -> str:
        """执行引擎。原文（[S3-04] 三）：批走 Spark SQL，准实时走 Flink。"""
        return "spark" if self.execution_mode is ExecutionMode.BATCH_T_PLUS_1 else "flink"

    def finish(self, status: TaskStatus, *, at: datetime | None = None, error: str = "") -> None:
        """收尾：打状态、算耗时、判 SLA。"""
        self.finished_at = at or datetime.now()
        self.elapsed_seconds = max(0.0, (self.finished_at - self.executed_at).total_seconds())
        self.task_status = status
        self.error_message = error[:2000]
        # 原文承诺：亿级以下数据 4 小时内跑完。超时即记一笔，供容量规划复盘
        self.sla_breached = (
            self.execution_mode is ExecutionMode.BATCH_T_PLUS_1
            and self.elapsed_seconds > BATCH_SLA_SECONDS
        )
        if self.sla_breached:
            logger.warning(
                "规则 %s 耗时 %.0f 秒，突破原文承诺的 %d 小时 SLA（扫描 %d 行，亿级上界 %d）",
                self.rule_id,
                self.elapsed_seconds,
                BATCH_SLA_HOURS,
                self.scanned_row_count,
                BATCH_SCAN_ROW_CEILING,
            )

    def to_row(self) -> dict[str, Any]:
        """渲染成 dwd_mining_task_detail 的一行。

        列名与顺序取自 :data:`~adas_lakehouse.mining.tables.MINING_TASK_WRITE_COLUMNS`
        （registry 派生并逐列校验过），引擎侧的字段名在这里一次性翻过去。
        ``rule_version`` registry 是 STRING、``rule_priority`` 是 INT，类型口径也在这里对齐。
        """
        row = {
            "mining_task_id": self.task_id,
            "run_id": self.run_id,
            "rule_id": self.rule_id,
            "rule_version": str(self.rule_version),
            "rule_category": self.rule_category,
            "rule_priority": self.rule_priority,
            "task_type": TASK_TYPE_RULE_MINING,
            "exec_mode": self.execution_mode.value,
            "engine": self.engine,
            "project_code": self.project_code,
            "scan_start_time": self.scan_low_watermark,
            "scan_end_time": self.scan_high_watermark,
            "scan_row_count": self.scanned_row_count,
            "hit_data_count": self.hit_count,
            "tag_write_count": self.tag_written_count,
            "frame_supplement_triggered": self.backfill_dispatched > 0,
            "task_status": self.task_status.value,
            "duration_sec": round(self.elapsed_seconds, 3),
            "start_time": self.executed_at,
            "end_time": self.finished_at,
            "error_message": self.error_message,
            "sla_breached": self.sla_breached,
        }
        # 严格按写入投影的顺序输出，避免 INSERT 列错位
        return {c: row[c] for c in MINING_TASK_WRITE_COLUMNS}

    def describe(self) -> str:
        return (
            f"{self.rule_id} v{self.rule_version} [{self.execution_mode.value}] "
            f"{self.task_status.value} 耗时 {self.elapsed_seconds:.1f}s "
            f"扫 {self.scanned_row_count} 行 命中 {self.hit_count} 打标 {self.tag_written_count}"
            + (f" 补抽帧 {self.backfill_dispatched}" if self.backfill_dispatched else "")
            + (" ⚠️SLA超时" if self.sla_breached else "")
        )


@dataclass(slots=True)
class ExecutionReport:
    """一轮调度的汇总。"""

    run_id: str
    started_at: datetime
    records: list[RuleRunRecord] = field(default_factory=list)
    compile_failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_hits(self) -> int:
        return sum(r.hit_count for r in self.records)

    @property
    def total_tags(self) -> int:
        return sum(r.tag_written_count for r in self.records)

    @property
    def total_backfills(self) -> int:
        return sum(r.backfill_dispatched for r in self.records)

    @property
    def failed(self) -> list[RuleRunRecord]:
        return [r for r in self.records if r.task_status is TaskStatus.FAILED]

    @property
    def sla_breaches(self) -> list[RuleRunRecord]:
        return [r for r in self.records if r.sla_breached]

    def summary(self) -> str:
        return (
            f"run={self.run_id} 规则 {len(self.records)} 条"
            f"（失败 {len(self.failed)}，编译失败 {len(self.compile_failures)}）"
            f" 命中 {self.total_hits} 打标 {self.total_tags} 补抽帧 {self.total_backfills}"
            + (f" ⚠️ SLA 超时 {len(self.sla_breaches)} 条" if self.sla_breaches else "")
        )


# --------------------------------------------------------------------------- 批执行


@dataclass(slots=True)
class BatchRuleExecutor:
    """T+1 批执行器：Spark SQL 在 Paimon 表上跑，增量水位防全表回扫。

    Args:
        backend: SQL 后端（Spark / StarRocks External Catalog）。
        result_sink: dwd_mining_result_detail 的写入口。
        task_sink: dwd_mining_task_detail 的写入口（执行追溯）。
        tag_service: 统一标签服务——命中一律经它打标，本执行器不碰标签表。
        watermarks: 增量水位存储。
        compiler: 规则编译器。
        weights: 评分权重。
        max_fetch_rows: 单条规则最多拉回多少行做打分打标。
        lookback_minutes: 水位回退分钟数。
    """

    backend: SqlBackend
    result_sink: ResultSink
    task_sink: ResultSink
    tag_service: TagService
    watermarks: WatermarkStore = field(default_factory=InMemoryWatermarkStore)
    compiler: RuleCompiler = field(default_factory=RuleCompiler)
    weights: ScoreWeights = DEFAULT_WEIGHTS
    max_fetch_rows: int = MAX_FETCH_ROWS
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES

    # ---- 单条规则 ----

    def execute_rule(
        self,
        rule: RuleDefinition,
        *,
        run_id: str,
        now: datetime | None = None,
        existing_hit_count: int = 0,
        dry_run: bool = False,
    ) -> RuleRunRecord:
        """跑一条批规则。

        流程与原文的执行链路一一对应：
        取水位（增量扫描）→ 编译 → 数命中（扫描范围）→ 拉候选 → 打分 →
        结果双写（标签服务 + dwd_mining_result_detail）→ 提交水位 → 回写执行追溯。

        **水位只在结果成功落表之后才提交**——中途失败宁可下一轮重扫，也不能丢命中。

        Args:
            rule: 批模式规则。
            run_id: 本轮三级 ID。
            now: 扫描上界与评估时刻。
            existing_hit_count: 该场景库内已有量，喂给稀缺度打分。
            dry_run: 只编译与计数，不写任何东西。

        Returns:
            RuleRunRecord（无论成败都返回，失败信息在 error_message 里）。
        """
        started = now or datetime.now()
        record = _new_record(
            rule,
            task_id=f"{run_id}_{rule.rule_id}",
            run_id=run_id,
            mode=ExecutionMode.BATCH_T_PLUS_1,
            started=started,
        )
        wall_start = time.monotonic()
        finish = _monotonic_finisher(record, started, wall_start)
        try:
            if not rule.is_runnable:
                finish(TaskStatus.SKIPPED, error=f"规则状态为 {rule.rule_status.value}，不调度")
                return record

            base = self.compiler.plan.base
            wm = self.watermarks.next_window(  # type: ignore[attr-defined]
                rule.rule_id,
                base.ref.resolve(),
                base.layer,
                now=started,
                lookback_minutes=self.lookback_minutes,
            )
            record.scan_low_watermark = wm.low
            record.scan_high_watermark = wm.high

            query = self.compiler.compile_batch(
                rule,
                wm,
                run_id=run_id,
                task_id=record.task_id,
                existing_hit_count=existing_hit_count,
                now=started,
            )
            if query.unresolved_columns:
                logger.warning(
                    "规则 %s 引用了扫描计划未提供的列 %s，SQL 仍会提交，执行期可能报错",
                    rule.rule_id,
                    list(query.unresolved_columns),
                )

            hit_count = self._count(query)
            record.scanned_row_count = hit_count
            record.hit_count = hit_count

            if dry_run or hit_count == 0:
                if not dry_run:
                    self.watermarks.commit(wm)
                finish(TaskStatus.SUCCESS, error="" if not dry_run else "dry-run，未写入")
                self._record_task(record, skip=dry_run)
                return record

            if hit_count > self.max_fetch_rows:
                raise BackendError(
                    f"规则 {rule.rule_id} 命中 {hit_count} 行，超过单轮拉取上界 {self.max_fetch_rows}；"
                    "请收紧条件或改用 execute_pushdown()（纯 SQL 落表，不经 Python）"
                )

            rows = self.backend.query(query.select_sql)
            scored = self._score_rows(rule, rows, existing_hit_count, started)

            written = self.result_sink.write_results([r for r, _ in scored])
            record.hit_count = written

            tag_reqs = [
                TagWriteRequest(
                    data_id=str(row["data_id"]),
                    scene_label=rule.scene_label,
                    rule_id=rule.rule_id,
                    rule_version=rule.rule_version,
                    run_id=run_id,
                    value_score=breakdown.value_score,
                )
                for row, breakdown in scored
                if row.get("data_id")
            ]
            record.tag_written_count = self.tag_service.write_tags(tag_reqs) if tag_reqs else 0

            # 结果都落了才推进水位
            self.watermarks.commit(wm)
            finish(TaskStatus.SUCCESS)
        except (BackendError, CompileError, ValueError, KeyError) as exc:
            finish(TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            logger.exception("规则 %s 执行失败", rule.rule_id)
        finally:
            if record.finished_at is None:
                finish(TaskStatus.FAILED, error="未知错误")
        self._record_task(record, skip=dry_run)
        return record

    def execute_pushdown(
        self,
        rule: RuleDefinition,
        *,
        run_id: str,
        now: datetime | None = None,
        existing_hit_count: int = 0,
    ) -> RuleRunRecord:
        """纯 SQL 落表：直接提交 ``INSERT INTO ... SELECT``，不把命中拉进 Python。

        用于命中量极大的规则。代价是这一路**不经统一标签服务**，所以打标必须由
        下游从 dwd_mining_result_detail 另起一个批次补上——原文要求「所有命中统一经
        标签服务打标」，这条路只是把打标推迟，不是绕过。调用方务必安排补打标。
        """
        started = now or datetime.now()
        wall_start = time.monotonic()
        record = _new_record(
            rule,
            task_id=f"{run_id}_{rule.rule_id}_pushdown",
            run_id=run_id,
            mode=ExecutionMode.BATCH_T_PLUS_1,
            started=started,
        )
        try:
            base = self.compiler.plan.base
            wm = self.watermarks.next_window(  # type: ignore[attr-defined]
                rule.rule_id,
                base.ref.resolve(),
                base.layer,
                now=started,
                lookback_minutes=self.lookback_minutes,
            )
            record.scan_low_watermark, record.scan_high_watermark = wm.low, wm.high
            query = self.compiler.compile_batch(
                rule,
                wm,
                run_id=run_id,
                task_id=record.task_id,
                existing_hit_count=existing_hit_count,
                now=started,
            )
            affected = self.backend.execute(query.insert_sql)
            record.hit_count = max(0, affected)
            record.scanned_row_count = record.hit_count
            record.tag_written_count = 0  # 打标推迟到下游批次
            self.watermarks.commit(wm)
            finish = _monotonic_finisher(record, started, wall_start)
            finish(TaskStatus.SUCCESS, error="pushdown 模式：打标由下游批次补")
        except (BackendError, CompileError, ValueError) as exc:
            _monotonic_finisher(record, started, wall_start)(
                TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            logger.exception("规则 %s pushdown 执行失败", rule.rule_id)
        self._record_task(record)
        return record

    # ---- 一轮调度 ----

    def execute_all(
        self,
        rules: Sequence[RuleDefinition],
        *,
        run_id: str = "",
        now: datetime | None = None,
        existing_hit_counts: dict[str, int] | None = None,
        dry_run: bool = False,
    ) -> ExecutionReport:
        """跑一轮 T+1 批：按优先级顺序执行全部批模式规则。

        按优先级排序不只是为了整齐——高优先级规则的命中会更早进入向量化队列
        （[S3-04] 一），早跑完早排队。

        Args:
            rules: 候选规则（会自动筛出批模式且 ENABLED 的）。
            run_id: 三级 ID，留空自动生成。
            now: 参考时刻。
            existing_hit_counts: ``{rule_id: 库内已有命中量}``，喂给稀缺度打分。
            dry_run: 只编译计数，不写入。

        Returns:
            ExecutionReport。
        """
        started = now or datetime.now()
        rid = run_id or str(new_run_id(MINING_STAGE, started))
        report = ExecutionReport(run_id=rid, started_at=started)
        counts = existing_hit_counts or {}

        batch_rules = sort_by_priority(rules_by_mode(rules, ExecutionMode.BATCH_T_PLUS_1))
        logger.info("T+1 批开跑：run=%s，规则 %d 条", rid, len(batch_rules))
        for rule in batch_rules:
            report.records.append(
                self.execute_rule(
                    rule,
                    run_id=rid,
                    now=started,
                    existing_hit_count=counts.get(rule.rule_id, 0),
                    dry_run=dry_run,
                )
            )
        logger.info("T+1 批结束：%s", report.summary())
        return report

    # ---- 内部 ----

    def _count(self, query: CompiledQuery) -> int:
        """先数一遍命中量。

        原文把「扫描范围」「命中数量」列为必须记录的执行追溯项，而且先数一遍能在
        真正拉数据之前就发现「这条规则圈得太宽」——比拉回来撑爆内存便宜得多。
        """
        rows = self.backend.query(query.count_sql)
        if not rows:
            return 0
        first = rows[0]
        for key in ("hit_count", "HIT_COUNT", "count(1)", "cnt"):
            if key in first:
                return int(first[key] or 0)
        return int(next(iter(first.values())) or 0)

    def _score_rows(
        self,
        rule: RuleDefinition,
        rows: Sequence[dict[str, Any]],
        existing_hit_count: int,
        now: datetime,
    ) -> list[tuple[dict[str, Any], ScoreBreakdown]]:
        """给候选行补上评分、分档、产物 ID 与向量化分级。

        SQL 侧已经算了一版 value_score（见 scoring.sql_score_expression），
        这里用 Python 侧的同一套公式重算并覆盖——因为 Python 侧能拿到 SQL 拿不到的
        实测信号值等上下文，两边公式一致，结果只会更准不会打架。
        """
        out: list[tuple[dict[str, Any], ScoreBreakdown]] = []
        policy = resolve_vectorize_policy(rule)
        for i, row in enumerate(rows):
            data = dict(row)
            breakdown = score_hit(
                rule,
                existing_hit_count=existing_hit_count + i,
                observed_signal_value=_as_float(_first_present(data, "signal_value", "peak_value")),
                collected_at=_as_datetime(_first_present(data, "collect_start_time", "event_time")),
                now=now,
                weights=self.weights,
            )
            data["value_score"] = round(breakdown.value_score, 4)
            data["value_tier"] = breakdown.tier.value
            data["vectorize_policy"] = policy.value
            data["artifact_status"] = ArtifactStatus.ACTIVE.value
            data["artifact_id"] = _artifact_id_for(rule, data)
            out.append((data, breakdown))
        return out

    def _record_task(self, record: RuleRunRecord, *, skip: bool = False) -> None:
        """把执行追溯写回 dwd_mining_task_detail。

        写失败只记日志，不往上抛——追溯写不进去是运维问题，不该让已经成功的挖掘
        任务被判定为失败。
        """
        if skip:
            return
        try:
            self.task_sink.write_results([record.to_row()])
        except Exception as exc:  # noqa: BLE001 - 追溯失败不影响主流程
            logger.error("执行追溯回写 %s 失败: %s", DWD_MINING_TASK_DETAIL.resolve(), exc)


# --------------------------------------------------------------------------- 流执行


@dataclass(slots=True)
class StreamRuleExecutor:
    """准实时流执行器：Flink 消费触发事件流，命中即打标，事件命中异步触发补抽帧。

    原文（[S3-04] 三）：「接管、AEB 这类事件不用等第二天」。

    流作业是长驻的，所以本执行器有两类方法：

    * :meth:`submit_rule` / :meth:`submit_all` —— 把规则编译成 Flink SQL 并提交，
      一次提交长期运行；
    * :meth:`handle_hits` —— 供消费侧（Flink sink / 回调）逐批处理已产生的命中：
      打标 + 派发补抽帧。两件事都不阻塞流作业本身。
    """

    backend: SqlBackend
    task_sink: ResultSink
    tag_service: TagService
    backfill: FrameBackfillDispatcher | None = None
    compiler: RuleCompiler = field(default_factory=RuleCompiler)
    weights: ScoreWeights = DEFAULT_WEIGHTS

    def submit_rule(
        self, rule: RuleDefinition, *, run_id: str, now: datetime | None = None
    ) -> RuleRunRecord:
        """编译并提交一条准实时规则的 Flink 作业。

        Returns:
            RuleRunRecord。流作业无界，hit_count 恒为 0——真实命中量由消费侧经
            :meth:`handle_hits` 累计，或直接查 dwd_mining_result_detail。
        """
        started = now or datetime.now()
        wall_start = time.monotonic()
        record = _new_record(
            rule,
            task_id=f"{run_id}_{rule.rule_id}",
            run_id=run_id,
            mode=ExecutionMode.NEAR_REALTIME,
            started=started,
        )
        finish = _monotonic_finisher(record, started, wall_start)
        try:
            if not rule.is_runnable:
                finish(TaskStatus.SKIPPED, error=f"规则状态为 {rule.rule_status.value}，不调度")
            else:
                query = self.compiler.compile_stream(
                    rule, run_id=run_id, task_id=record.task_id, now=started
                )
                self.backend.execute(query.insert_sql)
                finish(
                    TaskStatus.SUCCESS,
                    error=f"流作业已提交，窗口 前{EVENT_WINDOW_BEFORE_SEC}秒/后{EVENT_WINDOW_AFTER_SEC}秒",
                )
        except (BackendError, CompileError, ValueError) as exc:
            finish(TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            logger.exception("规则 %s 流作业提交失败", rule.rule_id)
        self._record_task(record)
        return record

    def submit_all(
        self, rules: Sequence[RuleDefinition], *, run_id: str = "", now: datetime | None = None
    ) -> ExecutionReport:
        """提交全部准实时规则。"""
        started = now or datetime.now()
        rid = run_id or str(new_run_id(MINING_STAGE, started))
        report = ExecutionReport(run_id=rid, started_at=started)
        stream_rules = sort_by_priority(rules_by_mode(rules, ExecutionMode.NEAR_REALTIME))
        logger.info("准实时流开跑：run=%s，规则 %d 条", rid, len(stream_rules))
        for rule in stream_rules:
            report.records.append(self.submit_rule(rule, run_id=rid, now=started))
        return report

    def handle_hits(
        self,
        rule: RuleDefinition,
        hits: Sequence[dict[str, Any]],
        *,
        run_id: str,
        existing_hit_count: int = 0,
        now: datetime | None = None,
        record: RuleRunRecord | None = None,
    ) -> tuple[int, int]:
        """处理一批流命中：统一打标 + 事件类异步触发补抽帧。

        补抽帧的窗口由 :meth:`BackfillRequest.for_event` 按原文的前 15 后 5 秒生成，
        派发走 :class:`~adas_lakehouse.mining.backends.LakehouseBackfillDispatcher`——
        写湖仓表就返回，不等抽帧引擎做完，「谁也不阻塞谁」。

        Args:
            rule: 产生这批命中的规则。
            hits: 命中行，至少要有 ``data_id``；事件类还要有 ``event_time``。
            run_id: 三级 ID。
            existing_hit_count: 稀缺度分母的起点。
            now: 参考时刻。
            record: 可选的执行追溯记录。传了就把本批的命中数 / 打标数 / 补抽帧数
                累加上去——流作业无界，这些量只有消费侧数得出来，不回填的话
                dwd_mining_task_detail 里流规则那几行永远是 0，
                ``frame_supplement_triggered`` 也永远为 false。

        Returns:
            ``(打标条数, 派发的补抽帧条数)``。
        """
        moment = now or datetime.now()
        tag_reqs: list[TagWriteRequest] = []
        backfills: list[BackfillRequest] = []

        for i, hit in enumerate(hits):
            data_id = str(hit.get("data_id") or "")
            if not data_id:
                logger.warning("规则 %s 的流命中缺少 data_id，跳过: %r", rule.rule_id, hit)
                continue
            event_time = _as_datetime(hit.get("event_time"))
            breakdown = score_hit(
                rule,
                existing_hit_count=existing_hit_count + i,
                observed_signal_value=_as_float(_first_present(hit, "peak_value", "signal_value")),
                collected_at=event_time,
                now=moment,
                weights=self.weights,
            )
            tag_reqs.append(
                TagWriteRequest(
                    data_id=data_id,
                    scene_label=rule.scene_label,
                    rule_id=rule.rule_id,
                    rule_version=rule.rule_version,
                    run_id=run_id,
                    value_score=breakdown.value_score,
                    event_time=event_time,
                )
            )
            if self._needs_backfill(rule) and event_time is not None:
                backfills.append(
                    BackfillRequest.for_event(
                        data_id=data_id,
                        rule_id=rule.rule_id,
                        run_id=run_id,
                        event_time=event_time,
                        reason=f"规则命中 {rule.scene_label}",
                    )
                )

        tagged = self.tag_service.write_tags(tag_reqs) if tag_reqs else 0
        dispatched = 0
        if backfills and self.backfill is not None:
            try:
                dispatched = self.backfill.dispatch(backfills)
            except BackendError as exc:
                # 补抽帧派发失败不该回滚已经打好的标签——两个引擎本来就是解耦的
                logger.error(
                    "规则 %s 的补抽帧派发失败（标签已写入，不回滚）: %s", rule.rule_id, exc
                )
        elif backfills:
            logger.warning(
                "规则 %s 产生 %d 条事件命中需要补抽帧，但未配置 dispatcher，已跳过",
                rule.rule_id,
                len(backfills),
            )
        if record is not None:
            record.hit_count += len(tag_reqs)
            record.tag_written_count += tagged
            record.backfill_dispatched += dispatched
        return tagged, dispatched

    # ---- CAN 信号规则的 Python 侧求值 ----

    @staticmethod
    def signal_condition_of(rule: RuleDefinition) -> SignalCondition | None:
        """摘出规则条件树里的第一个信号条件；没有就返回 None。

        公开出来是因为调用方（网关、回放工具）要先问「这条规则是不是信号类、
        阈值和持续时长各是多少」，再决定喂什么采样进来。
        """
        cond = rule.condition()
        if isinstance(cond, SignalCondition):
            return cond
        if isinstance(cond, ConditionGroup):
            for node in cond.walk():
                if isinstance(node, SignalCondition):
                    return node
        return None

    def evaluate_signal_samples(
        self,
        rule: RuleDefinition,
        samples: Sequence[SignalSample],
        *,
        emit_open_run: bool = False,
    ) -> list[dict[str, Any]]:
        """在一批 CAN 采样上直接求值规则，产出 :meth:`handle_hits` 认识的命中行。

        为什么执行器要有这条不经 Flink 的路：准实时规则平时由长驻 Flink 作业产出命中，
        但有三种场合拿不到 Flink——本地干跑、故障后按采样回放补命中、以及把
        「阈值 -4m/s²、持续 ≥ 0.5s」这组原文数字钉进单测。没有这条路，
        原文唯一给了数字的那条规则在 Python 侧就**只能渲染 SQL、不能求值**，
        对不对只有把作业提交到真实 Flink 才知道。

        语义与编译产物同源：两边都走
        :func:`~adas_lakehouse.mining.rules.sustained_matches`
        所描述的那套（先按 signal_name 收窄 → 连续满足段 → 首个不满足的采样收尾 →
        毫秒作差 ≥ 阈值）。

        Args:
            rule: 规则。必须是准实时模式且条件树里有信号条件。
            samples: CAN 采样序列。
            emit_open_run: 末段还没等到「不再满足」的采样时要不要出结果，
                见 :func:`~adas_lakehouse.mining.rules.sustained_matches`。

        Returns:
            命中行列表，可直接喂给 :meth:`handle_hits`（含 ``data_id`` / ``event_time``
            / ``peak_value``，打分与补抽帧都读得到）。

        Raises:
            CompileError: 规则不是准实时模式，或条件树里压根没有信号条件。
        """
        if rule.effective_mode is not ExecutionMode.NEAR_REALTIME:
            raise CompileError(
                f"规则 {rule.rule_id} 的执行模式是 {rule.effective_mode.value}，"
                "信号采样求值只服务准实时规则"
            )
        cond = self.signal_condition_of(rule)
        if cond is None:
            raise CompileError(
                f"规则 {rule.rule_id} 的条件树里没有信号条件，喂 CAN 采样没有意义；"
                f"原文规定：{rule.rule_type.spec.condition_note}"
            )
        hits = sustained_matches(cond, samples, emit_open_run=emit_open_run)
        logger.info(
            "规则 %s 在 %d 条采样上求值：命中 %d 段（阈值 %s %s，持续 ≥ %ss）",
            rule.rule_id,
            len(samples),
            len(hits),
            cond.op.sql,
            cond.threshold,
            cond.min_duration_sec,
        )
        return [h.to_hit_row() for h in hits]

    @staticmethod
    def _needs_backfill(rule: RuleDefinition) -> bool:
        """哪些规则的命中要触发补抽帧。

        原文（[S3-04] 三）点名的是事件类：「规则识别出接管事件，事件抽帧引擎立刻
        回头对前 15 后 5 秒窗口加密采样」。

        ⚠️ 原文未明确，本项目设计：车辆信号类（急减速/急变道）同样是有明确时刻的
        瞬时事件，同样适用该窗口，因此本项目把它也纳入补抽帧范围。
        标签组合、时空地理这类没有「时刻」的规则不触发。
        """
        return rule.rule_type in (RuleType.EVENT_TRIGGER, RuleType.VEHICLE_SIGNAL)

    def _record_task(self, record: RuleRunRecord) -> None:
        try:
            self.task_sink.write_results([record.to_row()])
        except Exception as exc:  # noqa: BLE001
            logger.error("执行追溯回写 %s 失败: %s", DWD_MINING_TASK_DETAIL.resolve(), exc)


# --------------------------------------------------------------------------- 工具


def _monotonic_finisher(record: RuleRunRecord, started: datetime, wall_start: float):
    """返回一个「用单调钟收尾」的闭包。

    为什么不直接 ``record.finish(status)``：那样 finished_at 取的是挂钟当下，而
    ``executed_at`` 是调用方传进来的 ``now``。回刷与单测为了可重放常常把 now 定在
    过去某一刻，两者一减就是几万秒——``duration_sec`` 变成垃圾还不算，
    ``sla_breached`` 会被这几万秒顶成 true，执行追溯表上凭空多出一片「突破 4 小时
    SLA」的假记录（[S3-04] 三承诺的是「亿级以下 4 小时内跑完」，假超时等于把这条
    承诺的度量搞坏）。所以耗时一律用单调钟量，结束时刻 = 起始时刻 + 实测耗时。
    VLM 引擎（vlm.VlmInferenceEngine.run）早就是这个写法，这里与它对齐。
    """

    def finish(status: TaskStatus, *, error: str = "") -> None:
        elapsed = max(0.0, time.monotonic() - wall_start)
        record.finish(status, at=started + timedelta(seconds=elapsed), error=error)

    return finish


def _new_record(
    rule: RuleDefinition,
    *,
    task_id: str,
    run_id: str,
    mode: ExecutionMode,
    started: datetime,
) -> RuleRunRecord:
    """建一条执行追溯记录，并把规则画像（种类/优先级/项目）一并带上。

    三条路径（批、pushdown、流）建记录的方式必须一致，否则追溯表里会出现
    「有的行有 rule_category、有的行没有」这种半截数据。
    """
    return RuleRunRecord(
        task_id=task_id,
        run_id=run_id,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        execution_mode=mode,
        executed_at=started,
        rule_category=rule.rule_type.value,
        rule_priority=rule.rule_priority.rank,
        project_code=rule.project_code,
    )


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    """按顺序取第一个**存在且非 None** 的值。

    刻意不用 ``row.get(a) or row.get(b)``：实测值 0.0 是合法信号值，
    用 ``or`` 会把它当成缺失往后找，于是严重度按「没观测到」算成 0 分。
    """
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _artifact_id_for(rule: RuleDefinition, row: dict[str, Any]) -> str:
    """给一次命中派生 artifact_id。

    payload 用「规则指纹 + 场景标签 + data_id」——规则语义不变时重跑得到同一个
    artifact_id（幂等），规则改版后指纹变、artifact_id 变，旧命中另行标 superseded，
    正好对上 ids 模块的规则三（重刷不覆盖）。

    data_id 不合法时返回空串而不是抛异常：一行脏数据不该让整批命中写不进去。
    """
    data_id = str(row.get("data_id") or "")
    if not data_id:
        return ""
    payload = f"{rule.fingerprint()}|{rule.scene_label}|{data_id}"
    try:
        return str(derive_artifact_id(data_id, ARTIFACT_STAGE, ARTIFACT_ALGO_VERSION, payload))
    except ValueError:
        logger.debug("data_id %r 不是合法的一级 ID，跳过 artifact_id 派生", data_id)
        return ""


def task_insert_sql(records: Sequence[RuleRunRecord]) -> str:
    """把执行追溯渲染成一条 INSERT，供导出脚本使用。

    供外部编排/导出脚本调用的公开 API，两个执行器自己走的是
    :class:`~adas_lakehouse.mining.backends.ResultSink`（见 ``_record_task``）。

    Raises:
        ValueError: 记录列表为空。
    """
    if not records:
        raise ValueError("没有执行记录可写入")
    from ._sqlfmt import literal

    target = qualified(DWD_MINING_TASK_DETAIL)
    cols = ", ".join(f"`{c}`" for c in MINING_TASK_WRITE_COLUMNS)
    values = [
        "  (" + ", ".join(literal(r.to_row()[c]) for c in MINING_TASK_WRITE_COLUMNS) + ")"
        for r in records
    ]
    return f"INSERT INTO {target}\n  ({cols})\nVALUES\n" + ",\n".join(values)
