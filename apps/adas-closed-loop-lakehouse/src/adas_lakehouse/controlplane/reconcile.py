"""控制面回流数据面：让「平台的每一步操作都进血缘，与湖仓闭环」。

原文第四章第三段，逐字：

  「控制面回流数据面：规则配置经 Flink CDC 同步入湖（ods_mining_rule_config），
    任务与审核动作定期回写 dwd_mining_task_detail——平台的每一步操作都进血缘，
    与湖仓闭环。」

注意方向：回流是**单向**的，控制面 → 数据面。反向不存在——数据面从不往控制面推主数据，
控制面要看明细就走数据面的双路查询出口（dataplane.query）。

两条回流通道：
  · 通道一（准实时）：MySQL ``cp_rule_config`` --Flink CDC--> ``ods_mining_rule_config``
    SQL 见 ``flink/sql/plane_control_cdc.sql``；
  · 通道二（定期批）：控制面任务与审核动作 --批量 INSERT--> ``dwd_mining_task_detail``
    SQL 见 ``flink/sql/plane_task_writeback.sql``，周期
    :data:`~.constants.WRITEBACK_INTERVAL_SECONDS`，单批
    :data:`~.constants.WRITEBACK_BATCH_ROWS`（两者均 ⚠️ 本项目设计，原文只说「定期」）。

还有一条对账：原文「湖仓是唯一对账基准，不存在双写导致的口径分裂」——
:func:`reconcile_tasks` 就是拿控制面的状态去跟湖仓回写结果核对，差异以湖仓为准。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import settings
from . import constants as K
from .contracts import TaskRecord, TaskState
from .rules import RuleRegistry
from .store import ControlPlaneStore

__all__ = [
    "CDC_SQL_PATH",
    "WRITEBACK_SQL_PATH",
    "LakeWriteback",
    "DryRunWriteback",
    "FlinkSqlGatewayWriteback",
    "CdcJobPlan",
    "ReconcileReport",
    "WritebackService",
    "cdc_job_plan",
    "reconcile_tasks",
]

_log = logging.getLogger(__name__)

#: 两个 SQL 文件的仓库内相对路径（相对仓库根 apps/adas-closed-loop-lakehouse/）
CDC_SQL_PATH = "flink/sql/plane_control_cdc.sql"
WRITEBACK_SQL_PATH = "flink/sql/plane_task_writeback.sql"


# --------------------------------------------------------------------------- CDC 通道


@dataclass(frozen=True, slots=True)
class CdcJobPlan:
    """通道一：规则配置 CDC 入湖的作业计划。

    ⚠️ 原文未明确，本项目设计：原文只说「经 Flink CDC 同步入湖」，
    没给并行度、checkpoint 间隔等参数——这里全部取 ``config.settings().flink`` 的值，
    不自创新数字。
    """

    source_table: str
    sink_table: str
    sql_file: str
    parallelism: int
    checkpoint_interval_ms: int
    jobmanager_url: str
    warehouse_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_table": self.source_table,
            "sink_table": self.sink_table,
            "sql_file": self.sql_file,
            "parallelism": self.parallelism,
            "checkpoint_interval_ms": self.checkpoint_interval_ms,
            "jobmanager_url": self.jobmanager_url,
            "warehouse_path": self.warehouse_path,
        }


def cdc_job_plan() -> CdcJobPlan:
    """组装规则配置 CDC 作业计划。连接信息全部取自 ``config.settings()``。"""
    cfg = settings()
    return CdcJobPlan(
        source_table="cp_rule_config",
        sink_table=K.TABLE_RULE_CONFIG,
        sql_file=CDC_SQL_PATH,
        parallelism=cfg.flink.parallelism,
        checkpoint_interval_ms=cfg.flink.checkpoint_interval_ms,
        jobmanager_url=cfg.flink.jobmanager_url,
        warehouse_path=cfg.minio.warehouse_path,
    )


def initial_rule_snapshot(registry: RuleRegistry) -> list[dict[str, Any]]:
    """CDC 作业启动前的全量初始化快照（规则全版本）。"""
    return registry.cdc_rows()


# --------------------------------------------------------------------------- 回写通道


@runtime_checkable
class LakeWriteback(Protocol):
    """把控制面的行写进湖仓表。实现方决定用什么通道（Flink SQL Gateway / Kafka / DLF）。"""

    def write(self, table: str, rows: list[Mapping[str, Any]]) -> int:
        """写入并返回成功行数。"""
        ...


class DryRunWriteback:
    """默认实现：只记账不落盘。

    存在的意义有两个：一是让本模块零外部依赖即可 import 与单测；
    二是演示「控制面即使完全不写湖仓也能正常编排」——回写是血缘需要，不是业务路径依赖。
    """

    def __init__(self) -> None:
        self.written: list[tuple[str, list[Mapping[str, Any]]]] = []

    def write(self, table: str, rows: list[Mapping[str, Any]]) -> int:
        self.written.append((table, list(rows)))
        _log.info("DryRunWriteback: %s <- %d 行", table, len(rows))
        return len(rows)


class FlinkSqlGatewayWriteback:
    """经 Flink SQL Gateway 提交 INSERT 的实现。

    ``requests`` 延迟 import——没装也不影响本模块被 import。
    连接地址取 ``config.settings().flink.sql_gateway_url``。
    """

    def __init__(self, *, session_name: str = "controlplane-writeback") -> None:
        self._session_name = session_name
        self._session_handle: str | None = None

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            import requests  # 延迟 import
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "FlinkSqlGatewayWriteback 需要 requests；请 `pip install requests`，"
                "或改用 DryRunWriteback"
            ) from exc
        url = settings().flink.sql_gateway_url.rstrip("/") + path
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _session(self) -> str:
        if self._session_handle is None:
            data = self._post(
                "/v1/sessions",
                {
                    "sessionName": self._session_name,
                    "properties": {
                        "execution.runtime-mode": "batch",
                        "parallelism.default": str(settings().flink.parallelism),
                    },
                },
            )
            self._session_handle = data["sessionHandle"]
        return self._session_handle

    def write(self, table: str, rows: list[Mapping[str, Any]]) -> int:
        """按 VALUES 批量 INSERT。表的列顺序以传入行的键顺序为准。"""
        if not rows:
            return 0
        cfg = settings()
        columns = list(rows[0].keys())
        values = ", ".join(
            "(" + ", ".join(_sql_literal(row.get(c)) for c in columns) + ")" for row in rows
        )
        cols = ", ".join(f"`{c}`" for c in columns)
        stmt = (
            f"INSERT INTO `{cfg.paimon.catalog}`.`{cfg.paimon.database}`.`{table}` "
            f"({cols}) VALUES {values}"
        )
        self._post(f"/v1/sessions/{self._session()}/statements", {"statement": stmt})
        return len(rows)


def _sql_literal(value: Any) -> str:
    """把 Python 值渲染成 Flink SQL 字面量。字符串里的单引号做转义。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


class WritebackService:
    """通道二：任务与审核动作定期回写 ``dwd_mining_task_detail``。

    :param store: 控制面存储
    :param writer: 湖仓写入实现，默认 :class:`DryRunWriteback`
    """

    def __init__(self, store: ControlPlaneStore, writer: LakeWriteback | None = None) -> None:
        self._store = store
        self._writer = writer or DryRunWriteback()

    @property
    def interval_seconds(self) -> int:
        """回写周期。⚠️ 原文未明确，本项目设计（原文只说「定期回写」）。"""
        return K.WRITEBACK_INTERVAL_SECONDS

    def pending_rows(self, limit: int = K.WRITEBACK_BATCH_ROWS) -> list[dict[str, Any]]:
        """待回写的行。只取控制面运行态字段，不含任何主数据。"""
        return [r.as_writeback_row() for r in self._store.pending_writeback(limit)]

    def flush_once(self, limit: int = K.WRITEBACK_BATCH_ROWS) -> int:
        """跑一轮回写。返回成功写入的行数。

        写成功才标记 ``written_back``——写失败下一轮会重来，因此回写是 at-least-once；
        湖仓侧靠 ``dwd_mining_task_detail`` 的主键 Upsert 去重（Paimon 主键表语义）。
        """
        records: list[TaskRecord] = list(self._store.pending_writeback(limit))
        if not records:
            return 0
        rows = [r.as_writeback_row() for r in records]
        written = self._writer.write(K.TABLE_TASK_DETAIL, rows)
        if written:
            self._store.mark_written_back(r.task_id for r in records[:written])
        return written


# --------------------------------------------------------------------------- 对账


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """控制面 vs 湖仓的对账报告。

    原文第四章：「湖仓是唯一对账基准，不存在双写导致的口径分裂」——
    因此差异一律以湖仓为准，控制面负责把自己改对，而不是反过来。
    """

    checked_at: datetime
    control_only: tuple[str, ...] = ()
    lake_only: tuple[str, ...] = ()
    state_mismatch: tuple[tuple[str, str, str], ...] = ()  # (task_id, 控制面态, 湖仓态)
    aligned: int = 0
    baseline: str = "湖仓（Paimon）"
    notes: list[str] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return not (self.control_only or self.lake_only or self.state_mismatch)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at.isoformat(timespec="seconds"),
            "baseline": self.baseline,
            "aligned": self.aligned,
            "control_only": list(self.control_only),
            "lake_only": list(self.lake_only),
            "state_mismatch": [
                {"task_id": t, "control_state": c, "lake_state": lake}
                for t, c, lake in self.state_mismatch
            ],
            "is_consistent": self.is_consistent,
            "notes": list(self.notes),
        }


def reconcile_tasks(
    control_rows: Iterable[Mapping[str, Any]],
    lake_rows: Iterable[Mapping[str, Any]],
) -> ReconcileReport:
    """对账：控制面任务状态 vs 湖仓 ``dwd_mining_task_detail``。

    两侧的列名天然不同——控制面内部叫 ``task_id`` / ``task_state``，湖仓那张表的列叫
    ``mining_task_id`` / ``task_status``（catalog 是表结构唯一事实源）。对账要能直接
    吃两边的原始行，所以这里两种拼写都认；状态一律按
    :attr:`~.contracts.TaskState.writeback_value` 的口径归一后再比，否则
    ``queued`` 与 ``pending`` 会被误判成不一致。

    :param control_rows: 控制面行，需含 ``task_id``（或 ``mining_task_id``）
    :param lake_rows: 湖仓回读的行，需含 ``mining_task_id``（或 ``task_id``）
    :return: 差异报告。差异不代表错误——回写有周期，在途任务本就会短暂只存在于控制面；
        ``lake_only`` 才是真问题（控制面清库重建后正常出现，此时以湖仓为准补齐配置）。
    """

    def _id(row: Mapping[str, Any]) -> str:
        for key in ("task_id", "mining_task_id"):
            if row.get(key):
                return str(row[key])
        raise KeyError(f"对账行缺少任务 ID（task_id / mining_task_id 都没有）：{sorted(row)}")

    def _state(row: Mapping[str, Any]) -> str:
        raw = str(row.get("task_status") or row.get("task_state") or "")
        try:
            return TaskState(raw).writeback_value
        except ValueError:
            return raw  # 已经是湖仓口径（pending/running/success…），原样比

    control = {_id(r): _state(r) for r in control_rows}
    lake = {_id(r): _state(r) for r in lake_rows}

    control_only = tuple(sorted(set(control) - set(lake)))
    lake_only = tuple(sorted(set(lake) - set(control)))
    mismatch = tuple(
        sorted(
            (tid, control[tid], lake[tid])
            for tid in set(control) & set(lake)
            if control[tid] != lake[tid]
        )
    )
    aligned = len(set(control) & set(lake)) - len(mismatch)

    notes: list[str] = []
    if control_only:
        notes.append(
            f"{len(control_only)} 条只在控制面：回写周期 {K.WRITEBACK_INTERVAL_SECONDS} 秒内属正常在途"
        )
    if lake_only:
        notes.append(
            f"{len(lake_only)} 条只在湖仓：控制面可能刚清空重建；以湖仓为准，业务数据未受影响"
        )
    if mismatch:
        notes.append(f"{len(mismatch)} 条状态不一致：以湖仓为基准核对回写链路是否滞后")
    return ReconcileReport(
        checked_at=datetime.now(),
        control_only=control_only,
        lake_only=lake_only,
        state_mismatch=mismatch,
        aligned=aligned,
        notes=notes,
    )


def sql_file(path_in_repo: str, *, repo_root: Path | None = None) -> Path:
    """定位仓库内的 SQL 文件（``flink/sql/plane_*.sql`` / ``ddl/starrocks_plane.sql``）。

    默认从本文件位置向上推 4 层到 ``apps/adas-closed-loop-lakehouse/``。
    """
    root = repo_root or Path(__file__).resolve().parents[3]
    return root / path_in_repo
