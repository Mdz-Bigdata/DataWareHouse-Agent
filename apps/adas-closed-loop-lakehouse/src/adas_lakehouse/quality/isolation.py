"""异常隔离：ods_quality_issue 的记录模型与落地存储。

原文第五章第 ② 步：「异常隔离：写入隔离表 ods_quality_issue，原始数据不丢失、可重放」，
以及第三章的提醒：

    ⚠️ 注意 recordRejectedData 这一步：被拒绝的数据连同命中规则一起落表，
      而不是打日志了事。原始数据不丢失是整套门禁可重放、可审计的根基。

因此这里的两条硬约束是：
  1. 原始报文必须随隔离记录一起保存（超大报文退化为对象存储 key + 摘要，见
     thresholds.RAW_PAYLOAD_INLINE_MAX_BYTES）；
  2. issue_id 由「表 + 记录键 + 报文哈希 + 命中规则」派生，重放/重试幂等——
     同一条坏数据重复进门禁不会刷出一堆隔离记录。

⚠️ 原文未明确，本项目设计：隔离表的字段清单在原文里是一张图（未公开文字），
本模块按「可重放、可追责」这两个原文明示的设计目标推断出下列字段。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..ids import content_hash
from .rules import RepairAction, RuleHit
from .severity import Channel, IssueLevel, QualityDimension, Severity
from .tables import QUALITY_ISSUE_TABLE_NAME, timestamp_columns
from .thresholds import RAW_PAYLOAD_INLINE_MAX_BYTES

__all__ = [
    "IssueStatus",
    "IssueRecord",
    "IssueStore",
    "InMemoryIssueStore",
    "JsonlIssueStore",
    "FlinkSqlGatewayIssueStore",
    "QUALITY_ISSUE_TABLE",
    "make_issue_id",
]

#: 隔离表表名（原文第五章点名）。取自 :mod:`adas_lakehouse.quality.tables`，
#: 那边是对 catalog.registry 的一次查表——表名改了这里 import 期就会炸，不会静默漂移。
QUALITY_ISSUE_TABLE = QUALITY_ISSUE_TABLE_NAME

#: 门禁版本号，随隔离记录落表，便于「规则改过之后这条异常是哪版门禁拦的」
#: ⚠️ 原文未明确，本项目设计。
GATE_VERSION = "quality-gate/1.0.0"


class IssueStatus(str, Enum):
    """隔离记录的状态机，对应原文五步闭环的推进过程。

    ISOLATED → ALERTED → DISPATCHED → (REPAIRED|DISCARDED) → RECHECKING
             → REINGESTED（复验通过，回填处理状态）
             ↘ ISOLATED（复验不通过，退回隔离，recheck_count += 1）
    """

    ISOLATED = "isolated"
    ALERTED = "alerted"
    DISPATCHED = "dispatched"
    REPAIRED = "repaired"
    RECHECKING = "rechecking"
    REINGESTED = "reingested"
    DISCARDED = "discarded"

    @property
    def is_terminal(self) -> bool:
        """终态：复验重入湖成功，或弃置归档。"""
        return self in (IssueStatus.REINGESTED, IssueStatus.DISCARDED)


def make_issue_id(
    source_table: str, record_key: str, payload_hash: str, rule_ids: Sequence[str]
) -> str:
    """派生幂等的 issue_id。

    同一张表、同一条记录、同一份报文、命中同一组规则 → 同一个 issue_id，
    重放不会产生重复隔离记录（Paimon 主键 Upsert 会把它们合成一条）。
    """
    seed = "|".join([source_table, record_key, payload_hash, ",".join(sorted(rule_ids))])
    return f"ISSUE_{content_hash(seed, length=16)}"


def _dump(value: Any) -> str:
    """JSON 序列化，兜住 datetime / 集合这类非原生类型。"""

    def _default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (set, frozenset, tuple)):
            return list(obj)
        if isinstance(obj, (bytes, bytearray)):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, Enum):
            return obj.value
        return repr(obj)

    return json.dumps(value, ensure_ascii=False, default=_default, sort_keys=True)


@dataclass(slots=True)
class IssueRecord:
    """一条隔离记录 = ods_quality_issue 的一行。

    字段分三组：
      · 定位：issue_id / dt / source_* / data_id / record_key —— 找得回
      · 判定：rule_ids / severity / issue_level / dimension / message —— 说得清
      · 处置：issue_status / repair_action / recheck_count / SLA 时间戳 —— 修得好
    """

    issue_id: str
    dt: str
    detected_at: datetime
    source_table: str
    source_channel: Channel
    severity: Severity
    issue_level: IssueLevel
    dimension: QualityDimension
    rule_ids: tuple[str, ...]
    message: str
    #: 原始报文（JSON 文本）。可重放的根基，不允许为空——放不下时见 payload_object_key
    raw_payload: str
    payload_hash: str
    record_key: str = ""
    data_id: str = ""
    artifact_id: str = ""
    run_id: str = ""
    parent_artifact_id: str = ""
    project_code: str = ""
    vehicle_code: str = ""
    source_system: str = ""
    quality_layer: str = ""
    hits_json: str = "[]"
    detail: str = ""
    payload_object_key: str = ""
    issue_status: IssueStatus = IssueStatus.ISOLATED
    repair_action: RepairAction | None = None
    recheck_count: int = 0
    escalated: bool = False
    owner: str = ""
    on_duty: str = ""
    response_due_at: datetime | None = None
    closure_due_at: datetime | None = None
    responded_at: datetime | None = None
    resolved_at: datetime | None = None
    sla_met: bool | None = None
    reingested_at: datetime | None = None
    discard_reason: str = ""
    gate_version: str = GATE_VERSION
    replayable: bool = True
    history: list[str] = field(default_factory=list)

    # ---- 构造 ----

    @classmethod
    def from_hits(
        cls,
        *,
        table: str,
        channel: Channel,
        record: Mapping[str, Any],
        hits: Sequence[RuleHit],
        detected_at: datetime,
        record_key: str = "",
        source_system: str = "",
        owner: str = "",
        on_duty: str = "",
        payload_object_key: str = "",
    ) -> IssueRecord:
        """由门禁命中结果构造隔离记录（原文第三章的 recordRejectedData）。

        严重程度取命中里最严重的一条，异常等级取最高的一条（P0 > P1 > P2 > P3），
        处置分支取最严重那条规则声明的 repair_action。
        """
        if not hits:
            raise ValueError("没有命中任何规则，不应产生隔离记录")
        ranked = sorted(hits, key=lambda h: (h.issue_level.value, h.severity.value))
        worst = ranked[0]
        severity = (
            Severity.ERROR if any(h.severity is Severity.ERROR for h in hits) else Severity.WARNING
        )

        payload = _dump(record)
        payload_hash = content_hash(payload, length=16)
        truncated = False
        if len(payload.encode("utf-8")) > RAW_PAYLOAD_INLINE_MAX_BYTES:
            # 超大报文只存摘要 + 对象存储 key，重放时回对象存储取原件
            truncated = True
            payload = _dump(
                {
                    "_truncated": True,
                    "_inline_limit_bytes": RAW_PAYLOAD_INLINE_MAX_BYTES,
                    "_payload_object_key": payload_object_key,
                    "_head": {k: record[k] for k in list(record)[:20]},
                }
            )

        key = record_key or str(record.get("data_id") or record.get("event_id") or payload_hash)
        rule_ids = tuple(h.rule_id for h in hits)
        policy = worst.issue_level.policy
        layer = worst.quality_layer.key if worst.quality_layer else ""
        return cls(
            issue_id=make_issue_id(table, key, payload_hash, rule_ids),
            dt=detected_at.strftime("%Y-%m-%d"),
            detected_at=detected_at,
            source_table=table,
            source_channel=channel,
            severity=severity,
            issue_level=worst.issue_level,
            dimension=worst.dimension,
            rule_ids=rule_ids,
            message=worst.message,
            raw_payload=payload,
            payload_hash=payload_hash,
            record_key=key,
            data_id=str(record.get("data_id") or ""),
            artifact_id=str(record.get("artifact_id") or ""),
            run_id=str(record.get("run_id") or ""),
            parent_artifact_id=str(record.get("parent_artifact_id") or ""),
            project_code=str(record.get("project_code") or ""),
            vehicle_code=str(record.get("vehicle_code") or ""),
            source_system=source_system,
            quality_layer=layer,
            hits_json=_dump([h.to_dict() for h in hits]),
            detail=worst.detail,
            payload_object_key=payload_object_key,
            repair_action=worst.repair_action,
            owner=owner or worst.owner,
            on_duty=on_duty,
            response_due_at=policy.response_due_at(detected_at),
            closure_due_at=policy.closure_due_at(detected_at),
            replayable=not truncated or bool(payload_object_key),
        )

    # ---- 重放 ----

    def replay_payload(self) -> dict[str, Any]:
        """还原原始记录用于复验重入湖（原文第五章第 ⑤ 步）。

        :raises ValueError: 报文被截断且没有对象存储兜底时不可重放——
            这种情况在 :attr:`replayable` 上已经标明，调用方应走「C 弃置归档」。
        """
        if not self.replayable:
            raise ValueError(
                f"隔离记录 {self.issue_id} 不可重放：原始报文超过内联上限且未提供对象存储 key"
            )
        data = json.loads(self.raw_payload)
        if isinstance(data, dict) and data.get("_truncated"):
            raise ValueError(
                f"隔离记录 {self.issue_id} 的报文已截断，请先从对象存储 "
                f"{self.payload_object_key!r} 取回原件"
            )
        if not isinstance(data, dict):
            raise ValueError(f"隔离记录 {self.issue_id} 的原始报文不是对象")
        return data

    def note(self, text: str, at: datetime | None = None) -> None:
        """追加一条处理轨迹（可追责）。"""
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        self.history.append(f"[{stamp}] {text}")

    # ---- 序列化 ----

    def to_row(self) -> dict[str, Any]:
        """转成可直接写 Paimon / 导出的扁平行。"""
        row = asdict(self)
        row["source_channel"] = self.source_channel.value
        row["severity"] = self.severity.value
        row["issue_level"] = self.issue_level.value
        row["dimension"] = self.dimension.key
        row["issue_status"] = self.issue_status.value
        row["repair_action"] = self.repair_action.value if self.repair_action else ""
        row["rule_ids"] = ",".join(self.rule_ids)
        row["history"] = _dump(self.history)
        for key in (
            "detected_at",
            "response_due_at",
            "closure_due_at",
            "responded_at",
            "resolved_at",
            "reingested_at",
        ):
            val = getattr(self, key)
            row[key] = val.isoformat(sep=" ", timespec="milliseconds") if val else None
        return row

    def copy_with(self, **changes: Any) -> IssueRecord:
        return replace(self, **changes)


class IssueStore:
    """隔离表存储的抽象接口。

    真实生产写 Paimon 的 ods_quality_issue；本地与测试用内存 / JSONL 实现。
    子类只需实现 :meth:`upsert` 与 :meth:`iter_all`。
    """

    def upsert(self, issue: IssueRecord) -> IssueRecord:
        raise NotImplementedError

    def iter_all(self) -> Iterator[IssueRecord]:
        raise NotImplementedError

    # ---- 通用查询（基于 iter_all，实现类可按存储特性覆盖优化）----

    def get(self, issue_id: str) -> IssueRecord | None:
        for issue in self.iter_all():
            if issue.issue_id == issue_id:
                return issue
        return None

    def list(
        self,
        *,
        status: IssueStatus | None = None,
        level: IssueLevel | None = None,
        table: str | None = None,
        dt: str | None = None,
    ) -> list[IssueRecord]:
        out: list[IssueRecord] = []
        for issue in self.iter_all():
            if status is not None and issue.issue_status is not status:
                continue
            if level is not None and issue.issue_level is not level:
                continue
            if table is not None and issue.source_table != table:
                continue
            if dt is not None and issue.dt != dt:
                continue
            out.append(issue)
        out.sort(key=lambda i: (i.detected_at, i.issue_id))
        return out

    def pending_recheck(self) -> list[IssueRecord]:
        """等待复验的记录：已修复（自动或人工）但还没重入湖的。"""
        return [
            i
            for i in self.iter_all()
            if i.issue_status in (IssueStatus.REPAIRED, IssueStatus.RECHECKING)
        ]

    def count(self) -> int:
        return sum(1 for _ in self.iter_all())


class InMemoryIssueStore(IssueStore):
    """进程内隔离表。默认实现——单测、本地跑批、干跑门禁都用它。"""

    def __init__(self) -> None:
        self._rows: dict[str, IssueRecord] = {}
        self._lock = threading.Lock()

    def upsert(self, issue: IssueRecord) -> IssueRecord:
        with self._lock:
            existing = self._rows.get(issue.issue_id)
            if existing is not None and existing is not issue:
                # 幂等：同一条坏数据重复进门禁只更新状态与轨迹，不新增行
                issue.history = existing.history + [
                    h for h in issue.history if h not in existing.history
                ]
                issue.recheck_count = max(issue.recheck_count, existing.recheck_count)
            self._rows[issue.issue_id] = issue
            return issue

    def iter_all(self) -> Iterator[IssueRecord]:
        with self._lock:
            return iter(list(self._rows.values()))

    def get(self, issue_id: str) -> IssueRecord | None:
        return self._rows.get(issue_id)

    def count(self) -> int:
        return len(self._rows)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


class JsonlIssueStore(IssueStore):
    """落本地 JSONL 的隔离表。

    用途：Flink/Paimon 不可用时（本地开发、离线回放）也要保证「原始数据不丢失」
    这条底线——宁可落文件，也不能只打日志。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def upsert(self, issue: IssueRecord) -> IssueRecord:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(_dump(issue.to_row()) + "\n")
        return issue

    def iter_all(self) -> Iterator[IssueRecord]:
        if not self.path.exists():
            return iter(())
        latest: dict[str, IssueRecord] = {}
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # 坏行跳过，不让审计文件的一行把整个读取打断
                issue = _row_to_issue(row)
                latest[issue.issue_id] = issue  # 后写的覆盖先写的
        return iter(list(latest.values()))


def _row_to_issue(row: Mapping[str, Any]) -> IssueRecord:
    """JSONL 行 → IssueRecord（尽力还原，缺字段用默认值）。"""

    def _dt(key: str) -> datetime | None:
        raw = row.get(key)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            return None

    detected = _dt("detected_at") or datetime.now()
    rule_ids = row.get("rule_ids") or ""
    history_raw = row.get("history") or "[]"
    try:
        history = json.loads(history_raw) if isinstance(history_raw, str) else list(history_raw)
    except ValueError:
        history = []
    repair = row.get("repair_action") or ""
    return IssueRecord(
        issue_id=str(row.get("issue_id", "")),
        dt=str(row.get("dt", detected.strftime("%Y-%m-%d"))),
        detected_at=detected,
        source_table=str(row.get("source_table", "")),
        source_channel=Channel(str(row.get("source_channel", Channel.COMMON.value))),
        severity=Severity(str(row.get("severity", Severity.ERROR.value))),
        issue_level=IssueLevel(str(row.get("issue_level", IssueLevel.P2.value))),
        dimension=QualityDimension.by_key(str(row.get("dimension", "validity"))),
        rule_ids=tuple(r for r in str(rule_ids).split(",") if r),
        message=str(row.get("message", "")),
        raw_payload=str(row.get("raw_payload", "{}")),
        payload_hash=str(row.get("payload_hash", "")),
        record_key=str(row.get("record_key", "")),
        data_id=str(row.get("data_id", "")),
        artifact_id=str(row.get("artifact_id", "")),
        run_id=str(row.get("run_id", "")),
        parent_artifact_id=str(row.get("parent_artifact_id", "")),
        project_code=str(row.get("project_code", "")),
        vehicle_code=str(row.get("vehicle_code", "")),
        source_system=str(row.get("source_system", "")),
        quality_layer=str(row.get("quality_layer", "")),
        hits_json=str(row.get("hits_json", "[]")),
        detail=str(row.get("detail", "")),
        payload_object_key=str(row.get("payload_object_key", "")),
        issue_status=IssueStatus(str(row.get("issue_status", IssueStatus.ISOLATED.value))),
        repair_action=RepairAction(repair) if repair else None,
        recheck_count=int(row.get("recheck_count", 0) or 0),
        escalated=bool(row.get("escalated", False)),
        owner=str(row.get("owner", "")),
        on_duty=str(row.get("on_duty", "")),
        response_due_at=_dt("response_due_at"),
        closure_due_at=_dt("closure_due_at"),
        responded_at=_dt("responded_at"),
        resolved_at=_dt("resolved_at"),
        sla_met=row.get("sla_met"),
        reingested_at=_dt("reingested_at"),
        discard_reason=str(row.get("discard_reason", "")),
        gate_version=str(row.get("gate_version", GATE_VERSION)),
        replayable=bool(row.get("replayable", True)),
        history=list(history),
    )


class FlinkSqlGatewayIssueStore(IssueStore):
    """把隔离记录写进 Paimon 的 ods_quality_issue（经 Flink SQL Gateway REST）。

    连接信息全部取自 :func:`adas_lakehouse.config.settings`，本类**不在导入期**
    连接任何外部服务；HTTP 也只用标准库 urllib，不引入第三方客户端，
    保证「客户端库没装也能 import 本模块」。

    ⚠️ 原文未明确，本项目设计：原文只说写入隔离表，没规定写入方式。
    生产建议用 Flink 作业直接 sink（见 flink/sql/quality_issue_sink.sql），
    本类用于运维补录、单条重放这类低频写入场景。
    """

    def __init__(
        self,
        *,
        table: str = QUALITY_ISSUE_TABLE,
        catalog: str | None = None,
        database: str | None = None,
        timeout_seconds: float = 30.0,
        mirror: IssueStore | None = None,
    ) -> None:
        from ..config import settings

        cfg = settings()
        self.table = table
        self.catalog = catalog or cfg.paimon.catalog
        self.database = database or cfg.paimon.database
        self.gateway_url = cfg.flink.sql_gateway_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        #: 双写兜底：网关不可用时至少保证原始数据不丢失
        self.mirror = mirror
        self._session_handle: str | None = None

    # ---- SQL ----

    def render_insert(self, issue: IssueRecord) -> str:
        """渲染一条 INSERT INTO（也可供人工在 SQL Client 里执行）。

        末尾补两个系统字段：``_ingest_time``（NOT NULL）与 ``_source_system``——
        它们由 catalog.spec 按 ODS 层自动追加到表上，写入时必须显式给值。
        """
        row = issue.to_row()
        cols = [c for c in row if c != "history"] + ["history"]
        values = []
        for col in cols:
            val = row[col]
            if val is None:
                values.append(
                    "CAST(NULL AS STRING)"
                    if col not in _TIMESTAMP_COLUMNS
                    else "CAST(NULL AS TIMESTAMP(3))"
                )
            elif isinstance(val, bool):
                values.append("TRUE" if val else "FALSE")
            elif isinstance(val, (int, float)):
                values.append(str(val))
            elif col in _TIMESTAMP_COLUMNS:
                values.append(f"TIMESTAMP '{val}'")
            else:
                values.append("'" + str(val).replace("'", "''") + "'")

        cols.append("_ingest_time")
        values.append("CURRENT_TIMESTAMP")
        cols.append("_source_system")
        values.append("'" + (issue.source_system or GATE_VERSION).replace("'", "''") + "'")

        col_sql = ", ".join(f"`{c}`" for c in cols)
        return (
            f"INSERT INTO `{self.catalog}`.`{self.database}`.`{self.table}` "
            f"({col_sql}) VALUES ({', '.join(values)})"
        )

    # ---- 写入 ----

    def upsert(self, issue: IssueRecord) -> IssueRecord:
        """提交写入。失败时退回 mirror（若配置），并抛出原异常的说明。"""
        sql = self.render_insert(issue)
        try:
            self._execute(sql)
        except Exception as exc:
            if self.mirror is not None:
                self.mirror.upsert(issue)
                issue.note(f"SQL Gateway 写入失败，已落兜底存储: {exc}")
                return issue
            raise RuntimeError(
                f"隔离记录 {issue.issue_id} 写入 {self.table} 失败，且未配置兜底存储: {exc}"
            ) from exc
        return issue

    def iter_all(self) -> Iterator[IssueRecord]:
        """读取隔离表不走这个类——请用 StarRocks 外部表查（ddl/starrocks_quality.sql）。"""
        raise NotImplementedError(
            "FlinkSqlGatewayIssueStore 只负责写入；查询隔离表请走 StarRocks 外部表 "
            "或 Paimon 批读，见 ddl/starrocks_quality.sql"
        )

    def _execute(self, statement: str) -> dict[str, Any]:
        """经 SQL Gateway REST 执行一条语句（标准库 urllib，无第三方依赖）。"""
        import urllib.error
        import urllib.request

        handle = self._ensure_session()
        url = f"{self.gateway_url}/v1/sessions/{handle}/statements"
        body = json.dumps({"statement": statement}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            self._session_handle = None  # 下次重建会话
            raise RuntimeError(f"Flink SQL Gateway 不可用（{self.gateway_url}）: {exc}") from exc

    def _ensure_session(self) -> str:
        import urllib.error
        import urllib.request

        if self._session_handle:
            return self._session_handle
        url = f"{self.gateway_url}/v1/sessions"
        req = urllib.request.Request(
            url,
            data=json.dumps({"properties": {"execution.runtime-mode": "batch"}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Flink SQL Gateway 建会话失败（{self.gateway_url}）: {exc}"
            ) from exc
        handle = payload.get("sessionHandle")
        if not handle:
            raise RuntimeError(f"Flink SQL Gateway 未返回 sessionHandle: {payload}")
        self._session_handle = str(handle)
        return self._session_handle


#: 隔离表里的时间戳列，渲染 SQL 时要加 TIMESTAMP 字面量前缀、置空要 CAST。
#: 判据取自 catalog.registry 登记的列类型，不再手抄一份名单——注册表给这张表
#: 加一列 TIMESTAMP，这里自动跟上，不会出现「新列按 STRING 拼字面量」的静默错写。
_TIMESTAMP_COLUMNS: frozenset[str] = timestamp_columns()


def bulk_upsert(store: IssueStore, issues: Iterable[IssueRecord]) -> int:
    """批量写入，返回成功条数。单条失败不影响其余（可审计优先于一致性）。"""
    ok = 0
    for issue in issues:
        try:
            store.upsert(issue)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 隔离写入失败要继续，不能连锁中断
            issue.note(f"隔离写入失败: {exc}")
    return ok
