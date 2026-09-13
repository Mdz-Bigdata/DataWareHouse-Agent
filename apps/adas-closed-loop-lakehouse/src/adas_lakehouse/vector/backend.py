"""档位选型与降级：外部表优先，内表兜底，Paimon 始终是单一事实源与对账基准。

来源：原文第六章《性能目标与降级路径：外部表优先，内表兜底》。

    外部表 HNSW 索引（优先，免冗余）  →  P95 不达标  →  StarRocks 内表冗余（降级，定时同步 + 对账）

原文的三条判断：
  · 第一档为什么优先——索引建在 Paimon 外部表上，向量本体只存一份，Paimon 仍是单一事实源
    与血缘基准，不存在「向量库和湖仓谁说了算」的问题；
  · 什么时候降级——POC 验证外部表索引性能不达标（唯一验收线：千万级检索 P95 ≤ 2 秒）；
  · 降级怎么做——向量数据冗余写入 StarRocks 内表（定时同步 + 主键对账），
    检索链路与 API 完全不变，切换对上层应用零感知。

原文还点明这与系列二第三篇「ADS 内表物化」是同一个模式：外部表解决覆盖面，内表解决确定性。
区别只在于，物化的是聚合结果还是向量数据。降级不是架构妥协，而是提前设计好的第二档。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .client import SqlExecutor, get_executor
from .index import PocReport, evaluate_poc, validate_partition_value
from .params import SEARCH_P95_SLA_SECONDS, VectorBackend
from .schema import PRIMARY_KEY, external_table_ref, internal_table_ref
from .versioning import ACTIVE_FILTER_CLAUSE

__all__ = [
    "BackendDecision",
    "BackendSelector",
    "InternalTableSync",
    "ReconcileReport",
    "render_sync_sql",
    "render_reconcile_sql",
    "MODE_NOTE_CN",
]

_log = logging.getLogger(__name__)

#: 原文第六章原话，留在代码里防止后人把降级误读成「架构妥协」。
MODE_NOTE_CN: str = (
    "外部表解决覆盖面，内表解决确定性；两种形态共用同一条检索 API，"
    "切换对上层应用零感知；Paimon 始终是单一事实源与对账基准"
)


@dataclass(frozen=True, slots=True)
class BackendDecision:
    """档位结论。

    :param backend: 选定档位
    :param reason_cn: 中文理由，直接可贴进上线评审记录
    :param measured_p95_sec: 实测 P95（秒），没测到则为 None
    :param poc_report: POC 五项报告（如有）
    """

    backend: VectorBackend
    reason_cn: str
    measured_p95_sec: float | None = None
    poc_report: PocReport | None = None

    @property
    def is_downgraded(self) -> bool:
        return self.backend is VectorBackend.INTERNAL_STARROCKS


@dataclass(slots=True)
class BackendSelector:
    """按 POC 结果与线上 P95 决定走哪一档。

    上线前用 ``decide_from_poc``；上线后用 ``decide_from_runtime`` 做持续守护——
    原文强调「上线前就确定好档位切换的条件，比事后救火从容得多」，所以切换条件是
    一个明确的数字（SEARCH_P95_SLA_SECONDS = 2 秒），不是拍脑袋。

    :param sla_seconds: 验收线，默认原文的 2 秒
    :param consecutive_breaches_to_downgrade: 连续多少个观测窗口超线才降级
        （⚠️ 原文未明确，本项目设计：防抖，避免一次抖动就切档）
    """

    sla_seconds: float = SEARCH_P95_SLA_SECONDS
    consecutive_breaches_to_downgrade: int = 3
    _breaches: int = field(default=0, init=False)

    def decide_from_poc(self, results: Iterable[Any]) -> BackendDecision:
        """按 POC 五项前置验证的结果定档。

        硬验收线（多向量列同表索引支持度 / 分区级索引支持度 / 千万级检索 P95 ≤ 2 秒）
        任一不过即降级。
        """
        report = evaluate_poc(results)
        if report.recommended_backend is VectorBackend.EXTERNAL_PAIMON:
            return BackendDecision(
                VectorBackend.EXTERNAL_PAIMON,
                "POC 五项全过，走第一档外部表 HNSW 索引：向量只存一份，Paimon 保持单一事实源",
                poc_report=report,
            )
        failed = "、".join(r.item.name_cn for r in report.blocking_failures)
        return BackendDecision(
            VectorBackend.INTERNAL_STARROCKS,
            f"POC 硬验收线未通过（{failed}），启用第二档：向量冗余写入 StarRocks 内表，"
            f"定时同步 + 主键对账；{MODE_NOTE_CN}",
            poc_report=report,
        )

    def decide_from_runtime(self, observed_p95_sec: float) -> BackendDecision:
        """按线上实测 P95 做持续守护。连续超线才降级，恢复一次即清零。"""
        if observed_p95_sec <= self.sla_seconds:
            self._breaches = 0
            return BackendDecision(
                VectorBackend.EXTERNAL_PAIMON,
                f"实测 P95 {observed_p95_sec:.3f}s ≤ 验收线 {self.sla_seconds}s，维持第一档",
                observed_p95_sec,
            )
        self._breaches += 1
        if self._breaches < self.consecutive_breaches_to_downgrade:
            return BackendDecision(
                VectorBackend.EXTERNAL_PAIMON,
                f"实测 P95 {observed_p95_sec:.3f}s 超线，但连续超线 {self._breaches} 次 "
                f"< 阈值 {self.consecutive_breaches_to_downgrade}，先观察不切档",
                observed_p95_sec,
            )
        _log.warning(
            "连续 %d 个窗口 P95 超过 %.1fs，触发降级到 StarRocks 内表",
            self._breaches,
            self.sla_seconds,
        )
        return BackendDecision(
            VectorBackend.INTERNAL_STARROCKS,
            f"连续 {self._breaches} 次实测 P95 超过 {self.sla_seconds}s（最近一次 "
            f"{observed_p95_sec:.3f}s），降级到第二档；{MODE_NOTE_CN}",
            observed_p95_sec,
        )


# ------------------------------------------------------------------ 定时同步 + 对账


def render_sync_sql(dt: str, *, only_active: bool = True) -> str:
    """渲染「外部表 → 内表」的定时同步 SQL（按分区增量，可重放）。

    走 StarRocks 内表的主键模型 INSERT INTO：主键相同即覆盖，重跑无副作用，
    与 Paimon 侧的 Upsert 语义对齐。

    向量列在内表必须 NOT NULL（向量索引不接受 NULL），因此源侧显式过滤空向量行。

    :param dt: 同步分区，形如 2026-09-06
    :param only_active: 只同步 active 版本——deprecated 的历史向量不必占内表空间
    """
    validate_partition_value(dt)
    ext, internal = external_table_ref(), internal_table_ref()
    conds = [f"`dt` = '{dt}'", "`image_embedding` IS NOT NULL", "`text_embedding` IS NOT NULL"]
    if only_active:
        conds.append(ACTIVE_FILTER_CLAUSE)
    where = "\n  AND ".join(conds)
    return (
        f"-- 第二档定时同步：{ext} -> {internal}（分区 {dt}）\n"
        f"-- 主键模型 INSERT 即 Upsert，重跑无副作用；Paimon 仍是单一事实源\n"
        f"INSERT INTO {internal}\n"
        f"SELECT * FROM {ext}\n"
        f"WHERE {where};\n"
    )


def render_reconcile_sql(dt: str) -> str:
    """渲染主键对账 SQL：以 Paimon 外部表为基准，查内表的缺失 / 多余 / 不一致。

    原文只说「定时同步 + 主键对账」，没给对账口径。
    ⚠️ 原文未明确，本项目设计：对账口径取三项——
      1. 行数差（内表少了就是漏同步）；
      2. 主键差集（外部表有、内表没有的主键）；
      3. 版本漂移（同一主键两侧 artifact_id 不一致，说明内表是旧快照）。
    """
    validate_partition_value(dt)
    ext, internal = external_table_ref(), internal_table_ref()
    pk_join = " AND ".join(f"e.`{k}` = i.`{k}`" for k in PRIMARY_KEY)
    pk_cols = ", ".join(f"e.`{k}`" for k in PRIMARY_KEY)
    return (
        f"-- 主键对账（分区 {dt}）：以 Paimon 外部表为基准\n"
        f"SELECT\n"
        f"    COUNT(*)                                              AS lake_rows,\n"
        f"    COUNT(i.`image_id`)                                   AS internal_rows,\n"
        f"    COUNT(*) - COUNT(i.`image_id`)                        AS missing_in_internal,\n"
        f"    SUM(CASE WHEN i.`image_id` IS NOT NULL\n"
        f"              AND i.`artifact_id` <> e.`artifact_id` THEN 1 ELSE 0 END) AS artifact_drift\n"
        f"FROM {ext} e\n"
        f"LEFT JOIN {internal} i ON {pk_join}\n"
        f"WHERE e.`dt` = '{dt}' AND e.{ACTIVE_FILTER_CLAUSE};\n"
        f"\n"
        f"-- 缺失主键明细（最多 1000 行，用于补数）\n"
        f"SELECT {pk_cols}\n"
        f"FROM {ext} e\n"
        f"LEFT JOIN {internal} i ON {pk_join}\n"
        f"WHERE e.`dt` = '{dt}' AND e.{ACTIVE_FILTER_CLAUSE} AND i.`image_id` IS NULL\n"
        f"LIMIT 1000;\n"
    )


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """一次对账的结论。"""

    dt: str
    lake_rows: int
    internal_rows: int
    missing_in_internal: int
    artifact_drift: int

    @property
    def consistent(self) -> bool:
        """完全一致才算通过——冗余副本的唯一价值就是和事实源一致。"""
        return self.missing_in_internal == 0 and self.artifact_drift == 0

    def summary(self) -> str:
        status = "一致" if self.consistent else "不一致"
        return (
            f"分区 {self.dt} 对账{status}：湖仓 {self.lake_rows} 行 / 内表 {self.internal_rows} 行，"
            f"缺失 {self.missing_in_internal} 行，版本漂移 {self.artifact_drift} 行"
        )


@dataclass(slots=True)
class InternalTableSync:
    """第二档的定时同步与对账执行器。

    :param executor: SQL 执行器
    :param auto_repair: 对账不一致时是否自动重跑当日同步（幂等，重跑安全）
    """

    executor: SqlExecutor = field(default_factory=lambda: get_executor())
    auto_repair: bool = True

    def sync_partition(self, dt: str, *, only_active: bool = True) -> int:
        """同步一个分区，返回影响行数。"""
        sql = render_sync_sql(dt, only_active=only_active)
        rows = self.executor.execute(sql)
        _log.info("分区 %s 同步完成，影响 %d 行", dt, rows)
        return rows

    def reconcile(self, dt: str) -> ReconcileReport:
        """对账一个分区。不一致且开了 auto_repair 就重跑同步再复核一次。

        :raises RuntimeError: 对账 SQL 没返回结果（分区不存在或权限不足）
        """
        validate_partition_value(dt)
        ext, internal = external_table_ref(), internal_table_ref()
        pk_join = " AND ".join(f"e.`{k}` = i.`{k}`" for k in PRIMARY_KEY)
        sql = (
            "SELECT COUNT(*) AS lake_rows, COUNT(i.`image_id`) AS internal_rows, "
            "COUNT(*) - COUNT(i.`image_id`) AS missing_in_internal, "
            "SUM(CASE WHEN i.`image_id` IS NOT NULL AND i.`artifact_id` <> e.`artifact_id` "
            "THEN 1 ELSE 0 END) AS artifact_drift "
            f"FROM {ext} e LEFT JOIN {internal} i ON {pk_join} "
            f"WHERE e.`dt` = %s AND e.{ACTIVE_FILTER_CLAUSE}"
        )
        result = self.executor.query(sql, [dt])
        if not len(result):
            raise RuntimeError(f"对账无结果：分区 {dt} 可能不存在，或没有查询权限")
        row = result.dicts()[0]
        report = ReconcileReport(
            dt,
            int(row.get("lake_rows") or 0),
            int(row.get("internal_rows") or 0),
            int(row.get("missing_in_internal") or 0),
            int(row.get("artifact_drift") or 0),
        )
        if not report.consistent:
            _log.warning("对账不一致: %s", report.summary())
            if self.auto_repair:
                self.sync_partition(dt)
        return report
