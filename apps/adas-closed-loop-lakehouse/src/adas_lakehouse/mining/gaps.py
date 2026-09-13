"""场景缺口识别——挖掘双出口里「未命中」的那一路。

原文（[S3-01] 一、平台定位：挖掘域的枢纽与双出口）：

    它居于闭环的枢纽，输出有两条路——命中存量数据直接回补训练集（零采集成本），
    未命中则下发定向采集需求，开启新一轮循环。

    ……库内命中场景直接回补训练集，零采集成本；未命中才下发定向采集需求。

规则挖掘跑完之后，每条规则都能回答两个数：**需要多少**（``target_clip_count``）、
**库里有多少**（命中量）。两数一减就是缺口。本模块把这件事形式化，
产出 dwd_scene_gap_detail，并为缺口部分生成定向采集需求。

对外出口是 [S3-01] 六接口表「检索类」行的 ``GET /api/v1/scene/tag-coverage``；
回补那一路凭 ``backfill_dataset_id``（[S3-01] 六「数据集类」行）。

⚠️ 原文未明确，本项目设计：
    原文给了双出口的**语义**，但没给缺口的判定阈值、严重度公式、或采集需求的字段。
    :data:`SATISFIED_COVERAGE_RATIO`、:func:`gap_severity`、:class:`CollectDemand`
    都是本项目的落地方案。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..ids import new_run_id
from ._sqlfmt import literal
from .backends import BackendError, ResultSink, SqlBackend
from .rules import RuleDefinition, RulePriority
from .tables import (
    DWD_MINING_RESULT_DETAIL,
    DWD_SCENE_GAP_DETAIL,
    SCENE_GAP_WRITE_COLUMNS,
    qualified,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SATISFIED_COVERAGE_RATIO",
    "GapStatus",
    "SceneGap",
    "CollectDemand",
    "gap_severity",
    "evaluate_gap",
    "SceneGapDetector",
    "coverage_sql",
]

#: 覆盖率达到多少算「已满足」。
#: ⚠️ 原文未明确，本项目设计：取 1.0，即库内命中量达到需求目标量才算满足。
#: 不打折的理由是原文把双出口说得很干脆——命中就回补、未命中才采集，中间没有模糊地带；
#: 真要放宽，改这个常量一处即可。
SATISFIED_COVERAGE_RATIO = 1.0

#: 严重度公式里优先级与缺口比例的权重。⚠️ 原文未明确，本项目设计。
_SEVERITY_W_PRIORITY = 0.5
_SEVERITY_W_GAP = 0.5

#: 完全未命中（hit=0）的严重度下限。⚠️ 原文未明确，本项目设计：
#: 「一条都没有」和「差一点点」是两种性质的缺口，前者必须排在前面，给个地板分。
_MISSING_SEVERITY_FLOOR = 60.0


class GapStatus(str, Enum):
    """缺口状态——直接对应原文双出口的三种处置。"""

    #: 库内量已达目标：走回补，零采集成本
    SATISFIED = "satisfied"
    #: 库内有但不够：命中部分回补，缺口部分下发采集
    PARTIAL = "partial"
    #: 库内一条都没有：整条走定向采集，开启新一轮循环
    MISSING = "missing"

    @property
    def action_cn(self) -> str:
        return _GAP_ACTION[self]


_GAP_ACTION: dict[GapStatus, str] = {
    GapStatus.SATISFIED: "直接回补训练集（零采集成本）",
    GapStatus.PARTIAL: "命中部分回补 + 缺口部分下发定向采集需求",
    GapStatus.MISSING: "下发定向采集需求，开启新一轮循环",
}


def gap_severity(
    rule_priority: RulePriority, coverage_ratio: float, *, hit_clip_count: int
) -> float:
    """缺口严重度，0-100，越大越该先补。

    公式（⚠️ 本项目设计）::

        severity = 100 * (0.5 * priority.prior_score + 0.5 * (1 - coverage_ratio))
        若 hit_clip_count == 0，则 severity = max(severity, 60.0)

    两个因子的取舍：规则优先级代表「这个场景对业务多重要」（原文明确 rule_priority
    驱动下游），缺口比例代表「还差多少」。完全未命中另给地板分，见
    :data:`_MISSING_SEVERITY_FLOOR` 的说明。

    Args:
        rule_priority: 规则优先级。
        coverage_ratio: 覆盖率 hit/target，已裁剪到 [0, 1]。
        hit_clip_count: 库内命中量，用于判定「完全未命中」。

    Returns:
        [0, 100]。
    """
    ratio = max(0.0, min(1.0, coverage_ratio))
    score = 100.0 * (
        _SEVERITY_W_PRIORITY * rule_priority.prior_score + _SEVERITY_W_GAP * (1.0 - ratio)
    )
    if hit_clip_count == 0:
        score = max(score, _MISSING_SEVERITY_FLOOR)
    return max(0.0, min(100.0, score))


@dataclass(frozen=True, slots=True)
class CollectDemand:
    """一条定向采集需求——缺口那一路的产出。

    ⚠️ 原文未明确，本项目设计：原文只说「未命中则下发定向采集需求」，
    没给需求单的字段。这里保留了下游采集系统排产最少需要的信息：
    要什么场景、要多少、多急、为什么要（血缘回指规则）。
    """

    collect_demand_id: str
    rule_id: str
    scene_label: str
    demand_clip_count: int
    priority: RulePriority
    severity: float
    project_code: str
    created_at: datetime
    rule_condition_summary: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "collect_demand_id": self.collect_demand_id,
            "rule_id": self.rule_id,
            "scene_label": self.scene_label,
            "demand_clip_count": self.demand_clip_count,
            "priority": self.priority.value,
            "severity": round(self.severity, 2),
            "project_code": self.project_code,
            "created_at": self.created_at.isoformat(),
            "rule_condition_summary": self.rule_condition_summary,
        }


@dataclass(frozen=True, slots=True)
class SceneGap:
    """一条场景缺口——落 dwd_scene_gap_detail 的那一行。"""

    gap_id: str
    run_id: str
    rule_id: str
    rule_version: int
    scene_label: str
    target_clip_count: int
    hit_clip_count: int
    gap_clip_count: int
    coverage_ratio: float
    gap_status: GapStatus
    gap_severity: float
    project_code: str
    evaluated_at: datetime
    backfill_dataset_id: str = ""
    collect_demand_id: str = ""

    def to_row(self) -> dict[str, Any]:
        """渲染成 dwd_scene_gap_detail 的一行。

        列名与顺序取自 :data:`~adas_lakehouse.mining.tables.SCENE_GAP_WRITE_COLUMNS`
        （registry 派生并逐列校验过）。本类的字段名是引擎侧的领域名，
        归一映射（``scene_label -> tag_id``、``hit_clip_count -> current_clip_count``、
        ``backfill_dataset_id -> consumed_dataset_id``、``evaluated_at -> last_eval_time``）
        只在这里发生一次；``rule_version`` registry 是 STRING，类型也在这里对齐。
        """
        row = {
            "project_code": self.project_code,
            "tag_id": self.scene_label,
            "gap_id": self.gap_id,
            "run_id": self.run_id,
            "rule_id": self.rule_id,
            "rule_version": str(self.rule_version),
            "target_clip_count": self.target_clip_count,
            "current_clip_count": self.hit_clip_count,
            "gap_clip_count": self.gap_clip_count,
            "coverage_ratio": round(self.coverage_ratio, 6),
            "gap_status": self.gap_status.value,
            "gap_severity": round(self.gap_severity, 4),
            "consumed_dataset_id": self.backfill_dataset_id,
            "collect_demand_id": self.collect_demand_id,
            "last_eval_time": self.evaluated_at,
        }
        # 严格按写入投影的顺序输出，避免 INSERT 列错位
        return {c: row[c] for c in SCENE_GAP_WRITE_COLUMNS}

    def describe(self) -> str:
        return (
            f"{self.scene_label}: 目标 {self.target_clip_count} / 库内 {self.hit_clip_count} "
            f"= 覆盖 {self.coverage_ratio:.1%}，缺 {self.gap_clip_count}，"
            f"{self.gap_status.value}(severity {self.gap_severity:.1f}) → {self.gap_status.action_cn}"
        )

    def to_demand(
        self, *, now: datetime | None = None, condition_summary: str = ""
    ) -> CollectDemand | None:
        """缺口部分转成定向采集需求；已满足则返回 None。"""
        if self.gap_status is GapStatus.SATISFIED or self.gap_clip_count <= 0:
            return None
        return CollectDemand(
            collect_demand_id=self.collect_demand_id or f"DEMAND_{self.gap_id}",
            rule_id=self.rule_id,
            scene_label=self.scene_label,
            demand_clip_count=self.gap_clip_count,
            priority=RulePriority.P0 if self.gap_severity >= 80.0 else RulePriority.P1,
            severity=self.gap_severity,
            project_code=self.project_code,
            created_at=now or self.evaluated_at,
            rule_condition_summary=condition_summary,
        )


def evaluate_gap(
    rule: RuleDefinition,
    hit_clip_count: int,
    *,
    run_id: str,
    now: datetime | None = None,
    backfill_dataset_id: str = "",
) -> SceneGap:
    """对一条规则做缺口判定。

    Args:
        rule: 规则（提供 target_clip_count / scene_label / 优先级）。
        hit_clip_count: 库内命中的 clip 数（去重后的 data_id 数）。
        run_id: 本轮评估的三级 ID。
        now: 评估时刻。
        backfill_dataset_id: 命中部分的回补数据集 ID（[S3-01] 六：凭它回补）。

    Returns:
        SceneGap。

    Raises:
        ValueError: hit_clip_count 为负。
    """
    if hit_clip_count < 0:
        raise ValueError(f"命中量不能为负: {hit_clip_count}")
    moment = now or datetime.now()
    target = int(rule.target_clip_count)

    if target <= 0:
        # 没填目标量的规则不构成缺口——挖多少算多少，视为已满足
        coverage = 1.0
        gap_count = 0
        status = GapStatus.SATISFIED
    else:
        coverage = min(1.0, hit_clip_count / target)
        gap_count = max(0, target - hit_clip_count)
        if hit_clip_count == 0:
            status = GapStatus.MISSING
        elif coverage >= SATISFIED_COVERAGE_RATIO:
            status = GapStatus.SATISFIED
        else:
            status = GapStatus.PARTIAL

    severity = gap_severity(rule.rule_priority, coverage, hit_clip_count=hit_clip_count)
    gap_id = f"{run_id}_{rule.rule_id}"
    return SceneGap(
        gap_id=gap_id,
        run_id=run_id,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        scene_label=rule.scene_label,
        target_clip_count=target,
        hit_clip_count=hit_clip_count,
        gap_clip_count=gap_count,
        coverage_ratio=coverage,
        gap_status=status,
        gap_severity=severity,
        project_code=rule.project_code,
        evaluated_at=moment,
        backfill_dataset_id=backfill_dataset_id,
        collect_demand_id="" if status is GapStatus.SATISFIED else f"DEMAND_{gap_id}",
    )


def coverage_sql(
    rules: Sequence[RuleDefinition],
    *,
    project_code: str = "",
    since: datetime | None = None,
) -> str:
    """渲染场景覆盖度查询——``GET /api/v1/scene/tag-coverage`` 背后的那条 SQL。

    统计每个场景标签在 dwd_mining_result_detail 里有多少个去重 clip。
    只数 ``artifact_status='active'`` 的命中：重刷产生新产物时旧产物被标 superseded
    （见 ids 模块规则三），把 superseded 也数进来会虚高覆盖率。

    Args:
        rules: 要统计的规则（取它们的 scene_label）。
        project_code: 按项目过滤。
        since: 只统计该时刻之后的命中。

    Returns:
        SQL 字符串。

    Raises:
        ValueError: 规则列表为空，或没有一条规则填了 scene_label。
    """
    labels = sorted({r.scene_label for r in rules if r.scene_label})
    if not labels:
        raise ValueError("没有可统计的场景标签——规则都没填 scene_label")

    # 场景标签在结果表里的列名是 matched_tag_id（registry 权威名），
    # 引擎侧叫 scene_label；这里只在 SELECT 的输出别名上保留领域名，
    # WHERE / GROUP BY 一律用真实列名，否则 SQL 在 Paimon 上找不到列。
    preds = [
        "`artifact_status` = " + literal("active"),
        "`matched_tag_id` IN (" + ", ".join(literal(x) for x in labels) + ")",
    ]
    if project_code:
        preds.append(f"`project_code` = {literal(project_code)}")
    if since is not None:
        preds.append(f"`hit_time` >= {literal(since)}")

    return (
        "-- 场景覆盖度（[S3-01] 六：GET /api/v1/scene/tag-coverage）\n"
        "SELECT\n"
        "  `matched_tag_id` AS `scene_label`,\n"
        "  `rule_id`,\n"
        "  COUNT(DISTINCT `data_id`) AS `hit_clip_count`\n"
        f"FROM {qualified(DWD_MINING_RESULT_DETAIL)}\n"
        "WHERE " + "\n  AND ".join(preds) + "\n"
        "GROUP BY `matched_tag_id`, `rule_id`"
    )


@dataclass(slots=True)
class SceneGapDetector:
    """场景缺口识别器：查覆盖度 → 算缺口 → 落 dwd_scene_gap_detail。

    Args:
        backend: 查覆盖度用的 SQL 后端（原文双路查询出口里的 External Catalog 那一路）。
        sink: dwd_scene_gap_detail 的写入口。
    """

    backend: SqlBackend
    sink: ResultSink

    def detect(
        self,
        rules: Sequence[RuleDefinition],
        *,
        run_id: str = "",
        project_code: str = "",
        since: datetime | None = None,
        now: datetime | None = None,
        backfill_dataset_ids: dict[str, str] | None = None,
    ) -> list[SceneGap]:
        """跑一轮缺口识别。

        Args:
            rules: 参与评估的规则。
            run_id: 三级 ID，留空则自动生成（stage=``scenegap``）。
            project_code: 项目过滤。
            since: 只统计该时刻之后的命中。
            now: 评估时刻。
            backfill_dataset_ids: ``{rule_id: backfill_dataset_id}``，命中部分的回补数据集。

        Returns:
            按严重度降序排好的缺口列表。

        Raises:
            BackendError: 覆盖度查询失败。
        """
        scored_rules = [r for r in rules if r.scene_label]
        if not scored_rules:
            logger.warning("没有可评估的规则（都没填 scene_label），本轮跳过缺口识别")
            return []

        rid = run_id or str(new_run_id("scenegap", now))
        try:
            rows = self.backend.query(
                coverage_sql(scored_rules, project_code=project_code, since=since)
            )
        except Exception as exc:
            raise BackendError(f"场景覆盖度查询失败: {exc}") from exc

        hits: dict[str, int] = {}
        for row in rows:
            key = str(row.get("rule_id") or "")
            if key:
                hits[key] = hits.get(key, 0) + int(row.get("hit_clip_count") or 0)

        ids = backfill_dataset_ids or {}
        gaps = [
            evaluate_gap(
                r,
                hits.get(r.rule_id, 0),
                run_id=rid,
                now=now,
                backfill_dataset_id=ids.get(r.rule_id, ""),
            )
            for r in scored_rules
        ]
        gaps.sort(key=lambda g: (-g.gap_severity, g.rule_id))

        written = self.sink.write_results([g.to_row() for g in gaps])
        logger.info(
            "场景缺口识别完成：评估 %d 个场景，写入 %d 行；未命中 %d，部分命中 %d，已满足 %d",
            len(gaps),
            written,
            sum(1 for g in gaps if g.gap_status is GapStatus.MISSING),
            sum(1 for g in gaps if g.gap_status is GapStatus.PARTIAL),
            sum(1 for g in gaps if g.gap_status is GapStatus.SATISFIED),
        )
        return gaps

    @staticmethod
    def to_demands(
        gaps: Sequence[SceneGap], rules: Sequence[RuleDefinition]
    ) -> list[CollectDemand]:
        """把缺口转成定向采集需求，按严重度降序。

        原文（[S3-01] 一）：「未命中则下发定向采集需求，开启新一轮循环」——
        这就是那个「下发」的入参。需求单本身由采集系统消费，本引擎只负责产出。
        """
        by_id = {r.rule_id: r for r in rules}
        out: list[CollectDemand] = []
        for gap in gaps:
            rule = by_id.get(gap.rule_id)
            summary = rule.condition().describe() if rule else ""
            demand = gap.to_demand(condition_summary=summary)
            if demand is not None:
                out.append(demand)
        out.sort(key=lambda d: (-d.severity, d.rule_id))
        return out


def gap_insert_sql(gaps: Sequence[SceneGap]) -> str:
    """把缺口渲染成一条 INSERT，给不方便走 Sink 的场合（如导出 SQL 脚本）用。

    Raises:
        ValueError: 缺口列表为空。
    """
    if not gaps:
        raise ValueError("没有缺口可写入")
    target = qualified(DWD_SCENE_GAP_DETAIL)
    cols = ", ".join(f"`{c}`" for c in SCENE_GAP_WRITE_COLUMNS)
    values = [
        "  (" + ", ".join(literal(g.to_row()[c]) for c in SCENE_GAP_WRITE_COLUMNS) + ")"
        for g in gaps
    ]
    return f"INSERT INTO {target}\n  ({cols})\nVALUES\n" + ",\n".join(values)
