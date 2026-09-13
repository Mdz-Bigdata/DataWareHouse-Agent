"""HNSW 索引：双索引定义、DDL 渲染、分区级增量刷新、POC 五项前置验证。

来源：原文第四章《在外部表上构建 HNSW 索引》。两条关键设计逐字落地：
  · 双索引——对应图文双向量，文搜图走文本向量、图搜图走图片向量、混合检索两个都用；
  · 分区级刷新——每日增量数据只重建当日分区索引，千万级全量索引不用每天重刷一遍。

原文同时给出硬约束：「外部表向量索引是相对新的能力，全量上线前必须先过 POC 验证」，
五个前置验证项见 params.POC_CHECKLIST；其中第 5 项 P95 ≤ 2 秒是整条链路的验收线。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import product

from .client import SqlExecutor, get_executor
from .params import (
    DEFAULT_HNSW_PARAMS,
    DEFAULT_INDEX_BUILD_TIMEOUT_SEC,
    HNSW_POC_SWEEP_GRID,
    POC_CHECKLIST,
    SEARCH_P95_SLA_SECONDS,
    HnswIndexParams,
    PocCheckItem,
    VectorBackend,
)
from .schema import external_table_ref, internal_table_ref

__all__ = [
    "VectorIndexDef",
    "IMAGE_INDEX_NAME",
    "TEXT_INDEX_NAME",
    "dual_indexes",
    "render_create_index_ddl",
    "render_drop_index_ddl",
    "render_partition_refresh_sql",
    "IndexService",
    "PocResult",
    "PocReport",
    "evaluate_poc",
    "sweep_grid",
    "run_poc_sweep",
    "validate_partition_value",
]

_log = logging.getLogger(__name__)

#: 原文第四章：两个 HNSW 索引，图文各一个。
IMAGE_INDEX_NAME: str = "idx_image_embedding_hnsw"
TEXT_INDEX_NAME: str = "idx_text_embedding_hnsw"


@dataclass(frozen=True, slots=True)
class VectorIndexDef:
    """一个 HNSW 向量索引定义。

    :param name: 索引名
    :param column: 索引列（ARRAY<FLOAT>）
    :param params: HNSW 参数，默认取 params.DEFAULT_HNSW_PARAMS
    :param purpose_cn: 服务哪条检索路径（原文第四章「双索引」说明）
    """

    name: str
    column: str
    params: HnswIndexParams = DEFAULT_HNSW_PARAMS
    purpose_cn: str = ""

    def render(self, table_ref: str) -> str:
        """渲染 CREATE INDEX 语句。

        ⚠️ 语法说明：StarRocks 的向量索引以 ``USING VECTOR`` + PROPERTIES 声明，
        参数键名（index_type / dim / metric_type / is_vector_normed / M / efconstruction）
        取自 StarRocks 向量索引约定；原文只给了「HNSW + 余弦相似度 + 图文各一个」三项事实，
        其余键名属本项目按 StarRocks 语法补齐。
        """
        props = ",\n".join(f'    "{k}" = "{v}"' for k, v in self.params.index_properties().items())
        head = f"-- {self.purpose_cn}" if self.purpose_cn else ""
        return (
            (head + "\n" if head else "")
            + f"CREATE INDEX {self.name} ON {table_ref} (`{self.column}`) USING VECTOR\n"
            f"PROPERTIES (\n{props}\n);\n"
        )


def dual_indexes(params: HnswIndexParams = DEFAULT_HNSW_PARAMS) -> tuple[VectorIndexDef, ...]:
    """原文第四章的双索引：图片向量索引 + 文本向量索引。"""
    return (
        VectorIndexDef(
            IMAGE_INDEX_NAME,
            "image_embedding",
            params,
            "图片向量索引：图搜图走它；混合检索两个索引都用",
        ),
        VectorIndexDef(
            TEXT_INDEX_NAME,
            "text_embedding",
            params,
            "文本向量索引：文搜图走它；混合检索两个索引都用",
        ),
    )


def _table_ref(backend: VectorBackend) -> str:
    """按档位取表引用：第一档外部表、第二档内表。"""
    if backend is VectorBackend.EXTERNAL_PAIMON:
        return external_table_ref()
    return internal_table_ref()


def render_create_index_ddl(
    *,
    backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON,
    params: HnswIndexParams = DEFAULT_HNSW_PARAMS,
) -> str:
    """渲染双索引的建索引脚本。"""
    ref = _table_ref(backend)
    body = "\n".join(idx.render(ref) for idx in dual_indexes(params))
    header = (
        f"-- 在 {'Paimon 外部表' if backend is VectorBackend.EXTERNAL_PAIMON else 'StarRocks 内表'}"
        f" {ref} 上构建 HNSW 索引（图文各一个，余弦相似度）\n"
        f"-- ⚠️ 外部表向量索引是相对新的能力，全量上线前必须先过 POC 验证（原文第四章五项）\n"
    )
    return header + body


def render_drop_index_ddl(*, backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON) -> str:
    """渲染删索引脚本（换参重建 / 回滚用）。"""
    ref = _table_ref(backend)
    return "".join(f"DROP INDEX {idx.name} ON {ref};\n" for idx in dual_indexes())


def render_partition_refresh_sql(
    dt: str, *, backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON
) -> tuple[str, ...]:
    """渲染「只刷新当日分区索引」的语句序列。

    原文第四章：「分区级刷新让每日增量数据只重建当日分区索引，千万级全量索引不用每天
    重刷一遍」；原文第三章第 ⑤ 步：「写入完成后通知 StarRocks 增量刷新当日分区索引，
    新数据当日可检索」。

    ⚠️ 原文未明确，本项目设计：原文只说明「刷新能否精确到单个分区」是 POC 验证项之一，
    没有给出具体语法。这里用 StarRocks 的 ``REFRESH EXTERNAL TABLE ... PARTITION (...)``
    刷元数据 + ``ALTER TABLE ... BUILD INDEX ... PARTITION (...)`` 重建分区索引两步表达；
    POC 第 2 项若验证不支持分区级刷新，则退化为全表刷新（代价是每日全量重建）。

    :param dt: 分区值，形如 ``2026-09-06``
    :raises ValueError: dt 不是合法分区值（防注入：只允许 yyyy-MM-dd）
    """
    validate_partition_value(dt)
    ref = _table_ref(backend)
    stmts: list[str] = []
    if backend is VectorBackend.EXTERNAL_PAIMON:
        # ① 先刷外部表元数据，让 StarRocks 看见 Paimon 新写入的当日分区快照
        stmts.append(f"REFRESH EXTERNAL TABLE {ref} PARTITION ('{dt}');")
    for idx in dual_indexes():
        # ② 再对当日分区重建向量索引（图文两个索引各刷一次）
        stmts.append(f"ALTER TABLE {ref} BUILD INDEX {idx.name} PARTITION (`dt` = '{dt}');")
    return tuple(stmts)


def validate_partition_value(dt: str) -> None:
    """分区值白名单校验：只接受 yyyy-MM-dd，杜绝把用户输入拼进 DDL。"""
    import re

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dt):
        raise ValueError(f"分区值必须是 yyyy-MM-dd 格式，收到 {dt!r}")


@dataclass(slots=True)
class IndexService:
    """索引的建 / 删 / 分区级刷新执行入口。

    :param executor: SQL 执行器，默认真实 StarRocks 客户端；单测传 RecordingExecutor
    :param backend: 当前档位，默认第一档外部表
    :param params: HNSW 参数
    :param build_timeout_sec: 建索引超时兜底（⚠️ 本项目设计，见 params）
    """

    executor: SqlExecutor = field(default_factory=lambda: get_executor())
    backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON
    params: HnswIndexParams = DEFAULT_HNSW_PARAMS
    build_timeout_sec: int = DEFAULT_INDEX_BUILD_TIMEOUT_SEC

    def create_dual_indexes(self) -> list[str]:
        """建两个 HNSW 索引，返回实际下发的语句。"""
        ref = _table_ref(self.backend)
        sent: list[str] = []
        for idx in dual_indexes(self.params):
            sql = idx.render(ref)
            self.executor.execute(sql)
            sent.append(sql)
            _log.info("已提交向量索引构建: %s ON %s", idx.name, ref)
        return sent

    def drop_dual_indexes(self) -> list[str]:
        """删两个 HNSW 索引（换参重建前必须先删）。"""
        sent = []
        for stmt in render_drop_index_ddl(backend=self.backend).splitlines():
            if stmt.strip():
                self.executor.execute(stmt)
                sent.append(stmt)
        return sent

    def refresh_partition(self, dt: str) -> list[str]:
        """刷新单个分区的索引——Embedding 流水线第 ⑤ 步调用它。

        :param dt: 当日分区，形如 2026-09-06
        :raises RuntimeError: 任一语句失败时抛出，调度侧据此重试（幂等：重刷无副作用）
        """
        stmts = render_partition_refresh_sql(dt, backend=self.backend)
        for stmt in stmts:
            self.executor.execute(stmt)
        _log.info("分区 %s 索引刷新完成，共 %d 条语句", dt, len(stmts))
        return list(stmts)

    def rebuild_all(self) -> list[str]:
        """全量重建（换 embedding_version 或换索引参数时用，代价高，别当日常操作）。"""
        return self.drop_dual_indexes() + self.create_dual_indexes()


# --------------------------------------------------------------------------- POC


@dataclass(frozen=True, slots=True)
class PocResult:
    """一条 POC 验证项的实测结果。

    :param item: 对应的验证项
    :param passed: 是否通过
    :param measured: 实测值（耗时 / 延迟 / 支持与否），自由文本或数值
    :param note: 备注
    """

    item: PocCheckItem
    passed: bool
    measured: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class PocReport:
    """POC 汇总结论。

    :param results: 五项实测结果
    :param recommended_backend: 推荐档位——硬验收线全过走外部表，否则降级内表
    """

    results: tuple[PocResult, ...]
    recommended_backend: VectorBackend

    @property
    def blocking_failures(self) -> tuple[PocResult, ...]:
        """未通过的硬验收线项。"""
        return tuple(r for r in self.results if r.item.blocking and not r.passed)

    def summary(self) -> str:
        """一段可直接贴进上线评审的中文结论。"""
        lines = [f"POC 五项前置验证（验收线：千万级检索 P95 ≤ {SEARCH_P95_SLA_SECONDS} 秒）"]
        for r in self.results:
            flag = "PASS" if r.passed else "FAIL"
            hard = "硬验收线" if r.item.blocking else "观测项"
            lines.append(
                f"  {r.item.ordinal}. [{flag}][{hard}] {r.item.name_cn}: {r.measured} {r.note}".rstrip()
            )
        lines.append(f"结论：走 {self.recommended_backend.value}")
        if self.blocking_failures:
            lines.append(
                "  原因："
                + "、".join(r.item.name_cn for r in self.blocking_failures)
                + " 未达标 → 启用降级第二档"
            )
        return "\n".join(lines)


def evaluate_poc(results: Iterable[PocResult]) -> PocReport:
    """按原文第四章清单给出档位结论。

    判定规则（原文第六章）：外部表优先；任一硬验收线不达标即降级到 StarRocks 内表冗余。
    硬验收线 = 多向量列同表索引支持度、分区级索引支持度、千万级检索 P95 ≤ 2 秒。

    :raises ValueError: 缺项——五项必须全部给出结果，不允许「没测就上线」
    """
    got = tuple(results)
    covered = {r.item.ordinal for r in got}
    missing = [i.ordinal for i in POC_CHECKLIST if i.ordinal not in covered]
    if missing:
        raise ValueError(
            f"POC 验证项缺失: {missing}（原文要求五项全过才能全量上线，缺项不得默认通过）"
        )
    blocking_failed = any(r.item.blocking and not r.passed for r in got)
    backend = VectorBackend.INTERNAL_STARROCKS if blocking_failed else VectorBackend.EXTERNAL_PAIMON
    return PocReport(got, backend)


def sweep_grid(grid: dict[str, tuple[int, ...]] | None = None) -> tuple[HnswIndexParams, ...]:
    """展开 HNSW 参数压测网格。

    原文第六章：「索引参数调优：M / efConstruction 在 POC 阶段按数据规模压测定参」——
    原文没给数值，本项目给出网格（params.HNSW_POC_SWEEP_GRID）供逐组压测，
    压测结果用 SEARCH_P95_SLA_SECONDS 卡线后再把胜出参数写死。
    """
    g = grid or HNSW_POC_SWEEP_GRID
    keys = sorted(g)
    out: list[HnswIndexParams] = []
    for combo in product(*(g[k] for k in keys)):
        changes = dict(zip(keys, combo, strict=True))
        try:
            out.append(DEFAULT_HNSW_PARAMS.with_(**changes))
        except ValueError:
            continue  # 非法组合（如 efConstruction < M）直接跳过，不进压测队列
    return tuple(out)


def run_poc_sweep(
    measure: Callable[[HnswIndexParams], float],
    *,
    grid: dict[str, tuple[int, ...]] | None = None,
    sla_seconds: float = SEARCH_P95_SLA_SECONDS,
) -> tuple[tuple[HnswIndexParams, float], ...]:
    """逐组压测并按 P95 排序，返回 (参数, 实测 P95 秒) 列表。

    :param measure: 压测回调，输入一组参数、返回实测 P95 秒数（由调用方接压测脚本）
    :param sla_seconds: 验收线，默认原文的 2 秒
    :return: 达标组合按 P95 升序；一组都不达标时返回空元组，调用方据此降级
    """
    scored: list[tuple[HnswIndexParams, float]] = []
    for candidate in sweep_grid(grid):
        p95 = measure(candidate)
        _log.info(
            "HNSW 压测 M=%d efConstruction=%d efSearch=%d -> P95=%.3fs",
            candidate.m,
            candidate.ef_construction,
            candidate.ef_search,
            p95,
        )
        if p95 <= sla_seconds:
            scored.append((candidate, p95))
    scored.sort(key=lambda x: x[1])
    return tuple(scored)
