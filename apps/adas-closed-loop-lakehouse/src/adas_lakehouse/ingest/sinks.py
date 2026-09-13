"""ODS 落地端：三条通道共用的写出口。

[a5] 第六章：「三条通道有一个共同终点：所有入湖数据统一经数据质量门禁校验后
写入 ODS 层。通道可以分，门禁不能分。」——门禁在 gate.py，终点在这里。

四种实现：
  · ``InMemoryOdsSink``       内存，单测与演练；
  · ``JsonlOdsSink``          本地 JSONL，离线补数与故障回放；
  · ``FlinkSqlGatewaySink``   经 Flink SQL Gateway REST 提交 INSERT INTO（仅 urllib，无三方依赖）；
  · ``CompositeOdsSink``      扇出到多个 sink（例如同时落盘 + 提交）。

生产链路的主力是 Flink 流作业本身（见 sql.py 生成的 ``flink/sql/ingest_*.sql``），
Python 侧的 sink 服务于补数、回放、演练与测试——两者写的是同一张表、同一套系统字段。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import settings
from .constants import SQL_GATEWAY_TIMEOUT_SEC
from .errors import SinkError

__all__ = [
    "OdsSink",
    "InMemoryOdsSink",
    "JsonlOdsSink",
    "CompositeOdsSink",
    "FlinkSqlGatewayClient",
    "FlinkSqlGatewaySink",
    "sql_literal",
    "render_insert",
]


@runtime_checkable
class OdsSink(Protocol):
    """ODS 写出口协议。"""

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        """写入若干行，返回实际写入行数。"""


class InMemoryOdsSink:
    """内存 sink：按表名累积行，供断言与演练。"""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        bucket = self.tables.setdefault(table, [])
        bucket.extend(dict(r) for r in rows)
        return len(rows)

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.get(table, [])

    def total(self) -> int:
        """全部表的累计行数。

        刻意不实现 ``__len__``：sink 常以 ``sink or InMemoryOdsSink()`` 的写法传递，
        一个还没写入过的 sink 若是 falsy，就会被静默换成新实例、写入丢失。
        """
        return sum(len(v) for v in self.tables.values())


class JsonlOdsSink:
    """JSONL sink：每张表一个 ``<dir>/<table>.jsonl``，追加写。

    用于 [a5] 第六章提到的「保留回放能力」——消费失败可从上次位点重新消费，
    本地落盘的 JSONL 则保证即便下游湖表不可用，事件也不会丢。
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        path = self.directory / f"{table}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as fp:
                for row in rows:
                    fp.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
        except OSError as exc:
            raise SinkError(f"写入 {path} 失败: {exc}") from exc
        return len(rows)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return repr(value)


class CompositeOdsSink:
    """扇出 sink：把同一批行写给多个下游，任一失败即抛。"""

    def __init__(self, *sinks: OdsSink) -> None:
        if not sinks:
            raise ValueError("CompositeOdsSink 至少需要一个下游 sink")
        self.sinks = sinks

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        for sink in self.sinks:
            sink.write(table, rows)
        return len(rows)


# --------------------------------------------------------------------------- SQL 渲染


def sql_literal(value: Any) -> str:
    """把 Python 值渲染成 Flink SQL 字面量。

    Raises:
        SinkError: 不支持的类型——宁可早失败，也不要拼出一条语义错误的 INSERT。
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        # 系统字段 _ingest_time / update_time 是 TIMESTAMP(3)，截到毫秒
        millis = value.microsecond // 1000
        return f"TIMESTAMP '{value:%Y-%m-%d %H:%M:%S}.{millis:03d}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    if isinstance(value, (list, tuple, dict)):
        return sql_literal(json.dumps(value, ensure_ascii=False, default=_json_default))
    raise SinkError(f"无法渲染为 SQL 字面量的类型: {type(value).__name__}")


def render_insert(
    table: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    catalog: str | None = None,
    database: str | None = None,
) -> str:
    """渲染 ``INSERT INTO ... VALUES ...``。列顺序以第一行为准，各行必须同构。

    Raises:
        SinkError: rows 为空或各行列集合不一致。
    """
    if not rows:
        raise SinkError("没有可写入的行")
    columns = list(rows[0].keys())
    colset = set(columns)
    for i, row in enumerate(rows[1:], start=1):
        if set(row.keys()) != colset:
            raise SinkError(f"第 {i} 行字段集合与首行不一致，无法拼进同一条 INSERT")
    cfg = settings().paimon
    fq = f"`{catalog or cfg.catalog}`.`{database or cfg.database}`.`{table}`"
    col_sql = ", ".join(f"`{c}`" for c in columns)
    values = ",\n  ".join(
        "(" + ", ".join(sql_literal(row[c]) for c in columns) + ")" for row in rows
    )
    return f"INSERT INTO {fq} ({col_sql}) VALUES\n  {values};"


# --------------------------------------------------------------------------- Flink SQL Gateway


@dataclass(slots=True)
class FlinkSqlGatewayClient:
    """Flink SQL Gateway REST 客户端（只用标准库 urllib，无三方依赖）。

    端点取自 ``config.settings().flink.sql_gateway_url``。协议为 SQL Gateway 的
    ``/v1/sessions`` 与 ``/v1/sessions/{handle}/statements``。

    ⚠️ 原文未明确，本项目设计：原文没有规定作业提交方式（生产多为平台化调度）。
    这里选 REST 而非 PyFlink，是为了避免把 JVM 依赖引入验收阶段的全量 import 检查。
    """

    base_url: str = field(default_factory=lambda: settings().flink.sql_gateway_url.rstrip("/"))
    timeout: int = SQL_GATEWAY_TIMEOUT_SEC
    session_handle: str | None = None
    properties: dict[str, str] = field(default_factory=dict)

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        url = f"{self.base_url}{path}"
        data = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        )
        req = urllib.request.Request(
            url, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as exc:  # pragma: no cover - 取决于服务端
            detail = exc.read().decode("utf-8", "replace")
            raise SinkError(f"SQL Gateway {method} {path} 失败 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            raise SinkError(f"SQL Gateway 不可达 {url}: {exc.reason}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise SinkError(f"SQL Gateway 返回非 JSON: {body[:200]}") from exc

    def open_session(self) -> str:
        """打开会话，返回 sessionHandle。默认带上并行度与 checkpoint 间隔。"""
        flink = settings().flink
        props = {
            "parallelism.default": str(flink.parallelism),
            "execution.checkpointing.interval": f"{flink.checkpoint_interval_ms} ms",
            **self.properties,
        }
        resp = self._request("POST", "/v1/sessions", {"properties": props})
        handle = resp.get("sessionHandle")
        if not handle:
            raise SinkError(f"SQL Gateway 未返回 sessionHandle: {resp}")
        self.session_handle = str(handle)
        return self.session_handle

    def execute(self, statement: str) -> str:
        """提交一条语句，返回 operationHandle。会话未打开时自动打开。"""
        if not self.session_handle:
            self.open_session()
        resp = self._request(
            "POST",
            f"/v1/sessions/{self.session_handle}/statements",
            {"statement": statement},
        )
        handle = resp.get("operationHandle")
        if not handle:
            raise SinkError(f"SQL Gateway 未返回 operationHandle: {resp}")
        return str(handle)

    def execute_many(self, statements: Iterable[str]) -> list[str]:
        return [self.execute(s) for s in statements]

    def close_session(self) -> None:
        if not self.session_handle:
            return
        try:
            self._request("DELETE", f"/v1/sessions/{self.session_handle}")
        finally:
            self.session_handle = None

    def __enter__(self) -> FlinkSqlGatewayClient:
        self.open_session()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close_session()


class FlinkSqlGatewaySink:
    """把行拼成 INSERT INTO 经 SQL Gateway 提交到 Paimon 表。

    适用于补数与小批量回放；常规实时链路请用 sql.py 生成的流作业。
    """

    def __init__(self, client: FlinkSqlGatewayClient | None = None) -> None:
        self.client = client or FlinkSqlGatewayClient()
        self.submitted: list[str] = []

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        statement = render_insert(table, rows)
        self.submitted.append(self.client.execute(statement))
        return len(rows)
