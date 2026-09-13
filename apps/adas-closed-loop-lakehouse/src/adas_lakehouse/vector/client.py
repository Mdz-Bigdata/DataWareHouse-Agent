"""StarRocks 连接与执行封装。

外部依赖（MySQL 协议客户端）一律**延迟导入**：验收阶段会做全量 import 检查，
没装 pymysql / mysql-connector 时 ``import adas_lakehouse.vector`` 也必须能跑通。

连接信息全部来自 config.settings().starrocks，不在本模块重复定义默认值。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import StarRocksConfig, settings

__all__ = [
    "SqlExecutor",
    "StarRocksClient",
    "RecordingExecutor",
    "QueryResult",
    "MissingDriverError",
    "get_executor",
]

_log = logging.getLogger(__name__)


class MissingDriverError(RuntimeError):
    """缺少 StarRocks 客户端驱动。消息里直接给出安装命令，别让调用方去猜。"""


@dataclass(frozen=True, slots=True)
class QueryResult:
    """一次查询的结果集。

    :param columns: 列名，顺序与 rows 内元组一致
    :param rows: 数据行
    :param elapsed_sec: 端到端耗时（秒），用于对照 P95 ≤ 2 秒的验收线
    :param sql: 实际下发的 SQL（脱敏后），排障用
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    elapsed_sec: float
    sql: str = ""

    def dicts(self) -> list[dict[str, Any]]:
        """行转 dict，便于回补元数据后直接序列化给上层 API。"""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]

    def __len__(self) -> int:
        return len(self.rows)


class SqlExecutor(Protocol):
    """SQL 执行器协议。检索 / 索引 / 同步三条链路都只依赖这个接口。

    这样做的直接好处：没有 StarRocks 也能跑全套单测——注入 RecordingExecutor 即可。
    """

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """执行 DDL / DML，返回影响行数。"""
        ...

    def query(self, sql: str, params: Sequence[Any] | None = None) -> QueryResult:
        """执行查询，返回结果集。"""
        ...


@dataclass(slots=True)
class RecordingExecutor:
    """只记录 SQL、不真连库的执行器（dry-run / 单测用）。

    :param responses: 预置的查询返回值，按调用顺序弹出；空则返回空结果集
    """

    statements: list[str] = field(default_factory=list)
    responses: list[QueryResult] = field(default_factory=list)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        self.statements.append(_inline(sql, params))
        return 0

    def query(self, sql: str, params: Sequence[Any] | None = None) -> QueryResult:
        rendered = _inline(sql, params)
        self.statements.append(rendered)
        if self.responses:
            got = self.responses.pop(0)
            return QueryResult(got.columns, got.rows, got.elapsed_sec, rendered)
        return QueryResult((), (), 0.0, rendered)


def _inline(sql: str, params: Sequence[Any] | None) -> str:
    """把参数内联进 SQL，仅用于 dry-run 记录与日志，绝不用于真实下发。"""
    if not params:
        return sql
    out = sql
    for p in params:
        literal = "NULL" if p is None else (f"'{p}'" if isinstance(p, str) else str(p))
        out = out.replace("%s", literal, 1)
    return out


@dataclass(slots=True)
class StarRocksClient:
    """真实的 StarRocks 客户端（MySQL 协议，宿主机默认 18630 端口）。

    驱动按 pymysql -> mysql.connector 的顺序探测，两个都没有就抛 MissingDriverError。
    连接是懒建的：构造对象本身不会碰网络，方便在没有集群的环境里做 SQL 渲染。
    """

    config: StarRocksConfig = field(default_factory=lambda: settings().starrocks)
    #: 连接到哪个库。外部表走 External Catalog 三段式引用，这里保持内库即可。
    database: str | None = None
    #: 会话级变量，连接建立后逐条 SET（如 ANN 参数 efsearch）
    session_variables: dict[str, str] = field(default_factory=dict)
    _conn: Any = field(default=None, repr=False)

    # ---- 连接 ----

    def _connect(self) -> Any:
        if self._conn is not None:
            return self._conn
        cfg = self.config
        conn = None
        try:  # 延迟导入 ①
            import pymysql  # type: ignore[import-not-found]

            conn = pymysql.connect(
                host=cfg.fe_host,
                port=cfg.query_port,
                user=cfg.user,
                password=cfg.password,
                database=self.database or cfg.internal_database,
                charset="utf8mb4",
                autocommit=True,
            )
        except ImportError:
            try:  # 延迟导入 ②
                import mysql.connector  # type: ignore[import-not-found]

                conn = mysql.connector.connect(
                    host=cfg.fe_host,
                    port=cfg.query_port,
                    user=cfg.user,
                    password=cfg.password,
                    database=self.database or cfg.internal_database,
                    autocommit=True,
                )
            except ImportError as exc:  # pragma: no cover - 取决于环境
                raise MissingDriverError(
                    "未安装 StarRocks 客户端驱动，请执行 `pip install pymysql` "
                    "或 `pip install mysql-connector-python`；"
                    "若只想渲染 SQL 不连库，请改用 RecordingExecutor"
                ) from exc
        self._conn = conn
        for name, value in self.session_variables.items():
            self._raw_execute(f"SET {name} = %s", (value,))
        return conn

    def close(self) -> None:
        """关闭连接。重复调用安全。"""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> StarRocksClient:
        self._connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---- 执行 ----

    def _raw_execute(self, sql: str, params: Sequence[Any] | None) -> Any:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params) if params else None)
        return cursor

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """执行 DDL / DML，返回影响行数。失败时补上 SQL 片段再抛出，方便定位。"""
        try:
            cursor = self._raw_execute(sql, params)
        except Exception as exc:  # noqa: BLE001 - 驱动异常类型不统一，统一包一层
            raise RuntimeError(f"StarRocks 执行失败: {exc}\nSQL: {sql[:500]}") from exc
        try:
            return int(getattr(cursor, "rowcount", 0) or 0)
        finally:
            cursor.close()

    def query(self, sql: str, params: Sequence[Any] | None = None) -> QueryResult:
        """执行查询并计时。耗时会被 search 层拿去和 P95 ≤ 2 秒的验收线对照。"""
        started = time.perf_counter()
        try:
            cursor = self._raw_execute(sql, params)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"StarRocks 查询失败: {exc}\nSQL: {sql[:500]}") from exc
        try:
            rows = tuple(tuple(r) for r in cursor.fetchall())
            desc = cursor.description or ()
            columns = tuple(d[0] for d in desc)
        finally:
            cursor.close()
        elapsed = time.perf_counter() - started
        _log.debug("StarRocks 查询完成 rows=%d elapsed=%.3fs", len(rows), elapsed)
        return QueryResult(columns, rows, elapsed, sql)


def get_executor(*, dry_run: bool = False, **kwargs: Any) -> SqlExecutor:
    """工厂：dry_run=True 给 RecordingExecutor，否则给真实客户端。

    没装驱动时不会在这里炸——StarRocksClient 是懒连接的，真正执行才会抛
    MissingDriverError。
    """
    if dry_run:
        return RecordingExecutor()
    return StarRocksClient(**kwargs)
