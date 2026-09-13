"""两张表的读写：Paimon（Flink SQL Gateway）+ StarRocks，外加一个内存实现。

原文第五章把读写职责分得很清楚：

* ① 扫描决策由 **StarRocks 定时任务（T+1）** 扫生命周期状态表；
* ④ 回写由 **存储执行服务** 把结果写回状态表、更新成本日表。

因此本模块给三种仓储：

==========================  ==========================================
实现                         用途
==========================  ==========================================
``InMemoryRepository``       单测与 dry-run；不需要任何集群就能跑通全流程
``PaimonRepository``         经 Flink SQL Gateway REST 读写 Paimon 明细表
``StarRocksRepository``      经 MySQL 协议扫描候选、写成本日表
==========================  ==========================================

外部依赖全部**延迟 import**：``PaimonRepository`` 只用标准库 urllib（不需要装任何东西），
``StarRocksRepository`` 需要 pymysql 或 mysql-connector-python，但只在真正连库时才 import——
``import adas_lakehouse.lifecycle`` 本身在任何环境下都不会炸。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from ..config import settings
from .records import COST_DAILY_COLUMNS, LIFECYCLE_COLUMNS, CostDailyRow, LifecycleRecord
from .tables import COST_DAILY_TABLE, LIFECYCLE_TABLE

__all__ = [
    "LifecycleRepository",
    "InMemoryRepository",
    "PaimonRepository",
    "StarRocksRepository",
    "FlinkSqlGatewayClient",
    "sql_literal",
]


# --------------------------------------------------------------------------- SQL 工具


def sql_literal(value: Any) -> str:
    """把 Python 值渲染成 SQL 字面量。

    治理作业写的是自己算出来的元信息，不是用户输入；但 file_path 里带单引号或
    反斜杠是完全可能的（对象 key 什么字符都有），所以一律转义，不留注入面。

    :raises TypeError: 不支持的类型——宁可报错，也不要把对象 repr 拼进 SQL。
    """
    if value is None:
        return "CAST(NULL AS STRING)"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "''")
        return f"'{escaped}'"
    raise TypeError(f"无法渲染为 SQL 字面量的类型: {type(value).__name__}")


def _values_clause(row: dict[str, Any], columns: Sequence[str]) -> str:
    return "(" + ", ".join(sql_literal(row.get(c)) for c in columns) + ")"


# --------------------------------------------------------------------------- 抽象


class LifecycleRepository(ABC):
    """生命周期两张表的读写接口。上层（decision / scheduler）只认这个接口。"""

    @abstractmethod
    def load_records(self, *, limit: int | None = None) -> list[LifecycleRecord]:
        """读取生命周期状态明细（扫描决策步的输入）。"""

    @abstractmethod
    def upsert_records(self, records: Iterable[LifecycleRecord]) -> int:
        """Upsert 明细表（回写步）。返回写入行数。"""

    @abstractmethod
    def write_cost_daily(self, rows: Iterable[CostDailyRow]) -> int:
        """写入成本日表（回写步）。返回写入行数。"""

    @abstractmethod
    def read_cost_daily(self, stat_date: date) -> list[CostDailyRow]:
        """读取某日的成本日表（看板与告警的输入）。"""


# --------------------------------------------------------------------------- 内存实现


class InMemoryRepository(LifecycleRepository):
    """内存仓储：单测与 dry-run 用，语义与真实仓储一致（按主键 Upsert）。

    用它可以在完全没有 Flink / StarRocks 的机器上跑通「扫描 → 演练 → 执行 → 回写」全流程。
    """

    def __init__(self, records: Iterable[LifecycleRecord] = ()) -> None:
        self._records: dict[tuple[str, str], LifecycleRecord] = {r.pk: r for r in records}
        self._cost: dict[tuple[Any, ...], CostDailyRow] = {}

    def load_records(self, *, limit: int | None = None) -> list[LifecycleRecord]:
        out = list(self._records.values())
        return out[:limit] if limit is not None else out

    def upsert_records(self, records: Iterable[LifecycleRecord]) -> int:
        n = 0
        for r in records:
            self._records[r.pk] = r
            n += 1
        return n

    def write_cost_daily(self, rows: Iterable[CostDailyRow]) -> int:
        n = 0
        for row in rows:
            self._cost[row.pk] = row
            n += 1
        return n

    def read_cost_daily(self, stat_date: date) -> list[CostDailyRow]:
        return [r for r in self._cost.values() if r.stat_date == stat_date]


# --------------------------------------------------------------------------- Flink


class FlinkSqlGatewayClient:
    """Flink SQL Gateway REST 客户端（只用标准库 urllib，无第三方依赖）。

    会话生命周期：``open()`` → ``execute()`` * N → ``close()``，也支持 with 语句。
    地址取自 ``config.settings().flink.sql_gateway_url``。
    """

    def __init__(self, base_url: str | None = None, *, timeout: float = 60.0) -> None:
        self.base_url = (base_url or settings().flink.sql_gateway_url).rstrip("/")
        self.timeout = timeout
        self._session: str | None = None

    # ---- 底层 HTTP ----

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # 4xx/5xx：把 Gateway 的错误原文带出来
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Flink SQL Gateway {method} {path} 失败 HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"连不上 Flink SQL Gateway {self.base_url}（检查 FLINK_SQL_GATEWAY_URL）: {exc.reason}"
            ) from exc
        return json.loads(raw) if raw else {}

    # ---- 会话 ----

    def open(self) -> FlinkSqlGatewayClient:
        """开会话。"""
        if self._session is None:
            resp = self._request("POST", "/v1/sessions", {"properties": {}})
            self._session = resp["sessionHandle"]
        return self

    def close(self) -> None:
        """关会话。关不掉不抛——调度作业不该因为清理失败而算失败。"""
        if self._session is None:
            return
        try:
            self._request("DELETE", f"/v1/sessions/{self._session}")
        except RuntimeError:
            pass
        finally:
            self._session = None

    def __enter__(self) -> FlinkSqlGatewayClient:
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 执行 ----

    def execute(self, statement: str, *, max_pages: int = 100) -> list[dict[str, Any]]:
        """提交一条 SQL 并取回全部结果行。

        :param statement: 单条 SQL（不要带结尾分号）。
        :param max_pages: 结果分页拉取的页数上限，防止异常查询把内存拖爆。
        :returns: 结果行（DQL）；DML 返回 Gateway 给的作业信息行。
        :raises RuntimeError: 会话未打开或 Gateway 报错。
        """
        if self._session is None:
            self.open()
        resp = self._request(
            "POST",
            f"/v1/sessions/{self._session}/statements",
            {"statement": statement.rstrip().rstrip(";")},
        )
        op = resp["operationHandle"]

        rows: list[dict[str, Any]] = []
        for token in range(max_pages):
            result = self._request(
                "GET",
                f"/v1/sessions/{self._session}/operations/{op}/result/{token}",
            )
            kind = result.get("resultType")
            payload = result.get("results") or {}
            columns = [c["name"] for c in payload.get("columns", [])]
            for item in payload.get("data", []):
                fields = item.get("fields", [])
                rows.append(dict(zip(columns, fields, strict=True)))
            next_uri = result.get("nextResultUri")
            if kind == "EOS" or not next_uri:
                break
        return rows


class PaimonRepository(LifecycleRepository):
    """经 Flink SQL Gateway 读写 Paimon 明细表。

    catalog / database 取自 ``config.settings().paimon``。
    ``INSERT INTO`` 在 Paimon 主键表上就是 Upsert（同主键后写覆盖先写），
    所以回写步不需要先删后插。
    """

    def __init__(
        self,
        client: FlinkSqlGatewayClient | None = None,
        *,
        catalog: str | None = None,
        database: str | None = None,
        batch_size: int = 500,
    ) -> None:
        cfg = settings().paimon
        self.client = client or FlinkSqlGatewayClient()
        self.catalog = catalog or cfg.catalog
        self.database = database or cfg.database
        self.batch_size = batch_size

    def _qualified(self, table: str) -> str:
        return f"`{self.catalog}`.`{self.database}`.`{table}`"

    def load_records(self, *, limit: int | None = None) -> list[LifecycleRecord]:
        """读明细表。已 deleted 的行不参与决策，直接在 SQL 里滤掉。"""
        cols = ", ".join(f"`{c}`" for c in LIFECYCLE_COLUMNS)
        sql = (
            f"SELECT {cols} FROM {self._qualified(LIFECYCLE_TABLE)} "
            f"WHERE `lifecycle_stage` <> 'deleted'"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [LifecycleRecord.from_row(r) for r in self.client.execute(sql)]

    def upsert_records(self, records: Iterable[LifecycleRecord]) -> int:
        """批量 Upsert。按 ``batch_size`` 切批，避免单条 SQL 过长被 Gateway 拒。"""
        cols = ", ".join(f"`{c}`" for c in LIFECYCLE_COLUMNS)
        total, batch = 0, []
        for rec in records:
            batch.append(_values_clause(rec.to_row(), LIFECYCLE_COLUMNS))
            if len(batch) >= self.batch_size:
                self.client.execute(
                    f"INSERT INTO {self._qualified(LIFECYCLE_TABLE)} ({cols}) "
                    f"VALUES {', '.join(batch)}"
                )
                total += len(batch)
                batch = []
        if batch:
            self.client.execute(
                f"INSERT INTO {self._qualified(LIFECYCLE_TABLE)} ({cols}) VALUES {', '.join(batch)}"
            )
            total += len(batch)
        return total

    def write_cost_daily(self, rows: Iterable[CostDailyRow]) -> int:
        cols = ", ".join(f"`{c}`" for c in COST_DAILY_COLUMNS)
        values = [_values_clause(r.to_row(), COST_DAILY_COLUMNS) for r in rows]
        if not values:
            return 0
        self.client.execute(
            f"INSERT INTO {self._qualified(COST_DAILY_TABLE)} ({cols}) VALUES {', '.join(values)}"
        )
        return len(values)

    def read_cost_daily(self, stat_date: date) -> list[CostDailyRow]:
        cols = ", ".join(f"`{c}`" for c in COST_DAILY_COLUMNS)
        sql = (
            f"SELECT {cols} FROM {self._qualified(COST_DAILY_TABLE)} "
            f"WHERE `stat_date` = {sql_literal(stat_date)}"
        )
        return [CostDailyRow.from_row(r) for r in self.client.execute(sql)]


# --------------------------------------------------------------------------- StarRocks


class StarRocksRepository(LifecycleRepository):
    """经 MySQL 协议读写 StarRocks。

    原文第五章①的「StarRocks 定时任务（T+1）扫描生命周期状态表」走的就是这条路——
    明细表由 External Catalog 直查 Paimon，成本日表是 StarRocks 内表。

    驱动延迟 import：优先 pymysql，退而求其次 mysql.connector；两个都没有时
    在**连接那一刻**给出可操作的报错，而不是在 import 模块时就炸。
    """

    def __init__(
        self,
        *,
        external_catalog: str | None = None,
        paimon_database: str | None = None,
        internal_database: str | None = None,
    ) -> None:
        sr = settings().starrocks
        self.external_catalog = external_catalog or sr.external_catalog
        self.paimon_database = paimon_database or settings().paimon.database
        self.internal_database = internal_database or sr.internal_database
        self._conn: Any = None

    # ---- 连接 ----

    @staticmethod
    def _driver():
        """延迟 import 驱动。"""
        try:
            import pymysql  # type: ignore

            return pymysql, "pymysql"
        except ImportError:
            pass
        try:
            import mysql.connector as connector  # type: ignore

            return connector, "mysql-connector-python"
        except ImportError as exc:
            raise RuntimeError(
                "StarRocks 需要 MySQL 协议驱动，请安装 pymysql 或 mysql-connector-python "
                "（pip install pymysql）。注意：仅连接时需要，import 本模块不需要。"
            ) from exc

    def connect(self) -> Any:
        """建立连接（幂等）。"""
        if self._conn is not None:
            return self._conn
        driver, name = self._driver()
        sr = settings().starrocks
        try:
            if name == "pymysql":
                self._conn = driver.connect(
                    host=sr.fe_host,
                    port=sr.query_port,
                    user=sr.user,
                    password=sr.password,
                    charset="utf8mb4",
                    autocommit=True,
                )
            else:
                self._conn = driver.connect(
                    host=sr.fe_host,
                    port=sr.query_port,
                    user=sr.user,
                    password=sr.password,
                    autocommit=True,
                )
        except Exception as exc:  # 驱动各自抛自己的异常类型，统一包装
            raise RuntimeError(
                f"连不上 StarRocks {sr.fe_host}:{sr.query_port}"
                f"（检查 STARROCKS_FE_HOST / STARROCKS_QUERY_PORT）: {exc}"
            ) from exc
        return self._conn

    def close(self) -> None:
        """关连接。"""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> StarRocksRepository:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def query(self, sql: str) -> list[dict[str, Any]]:
        """执行查询，返回字典行。"""
        conn = self.connect()
        cur = conn.cursor()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
        finally:
            cur.close()

    def execute(self, sql: str) -> int:
        """执行 DML，返回受影响行数。"""
        conn = self.connect()
        cur = conn.cursor()
        try:
            cur.execute(sql)
            return int(cur.rowcount or 0)
        finally:
            cur.close()

    # ---- 仓储接口 ----

    def load_records(self, *, limit: int | None = None) -> list[LifecycleRecord]:
        """经 External Catalog 直查 Paimon 明细表。"""
        cols = ", ".join(f"`{c}`" for c in LIFECYCLE_COLUMNS)
        sql = (
            f"SELECT {cols} FROM `{self.external_catalog}`.`{self.paimon_database}`"
            f".`{LIFECYCLE_TABLE}` WHERE `lifecycle_stage` <> 'deleted'"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [LifecycleRecord.from_row(r) for r in self.query(sql)]

    def upsert_records(self, records: Iterable[LifecycleRecord]) -> int:
        """明细表的写入方是 Flink（Paimon 主键表），StarRocks 侧只读不写。

        :raises NotImplementedError: 一律拒绝——如果这里放开写，
            就会出现「两个引擎同时写同一张 Paimon 主键表」的双写，
            后果是 changelog 乱序、成本日表算不准。回写请用 ``PaimonRepository``。
        """
        raise NotImplementedError(
            "明细表由 Flink 写入 Paimon，StarRocks 侧只读；回写请使用 PaimonRepository"
        )

    def write_cost_daily(self, rows: Iterable[CostDailyRow]) -> int:
        """写 StarRocks 内表（主键模型，INSERT 即 Upsert）。"""
        cols = ", ".join(f"`{c}`" for c in COST_DAILY_COLUMNS)
        values = [_values_clause(r.to_row(), COST_DAILY_COLUMNS) for r in rows]
        if not values:
            return 0
        # StarRocks 用 MySQL 方言：TIMESTAMP '...' / DATE '...' 写法不通用，转成裸字面量
        sql = (
            (
                f"INSERT INTO `{self.internal_database}`.`{COST_DAILY_TABLE}` ({cols}) "
                f"VALUES {', '.join(values)}"
            )
            .replace("DATE '", "'")
            .replace("TIMESTAMP '", "'")
        )
        return self.execute(sql)

    def read_cost_daily(self, stat_date: date) -> list[CostDailyRow]:
        cols = ", ".join(f"`{c}`" for c in COST_DAILY_COLUMNS)
        sql = (
            f"SELECT {cols} FROM `{self.internal_database}`.`{COST_DAILY_TABLE}` "
            f"WHERE `stat_date` = '{stat_date.isoformat()}'"
        )
        return [CostDailyRow.from_row(r) for r in self.query(sql)]
