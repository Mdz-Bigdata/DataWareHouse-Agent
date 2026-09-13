"""查询接口层：业务平台读 ADS 内表的唯一入口。

[S1-全景] 第二章原则「应用层不碰存储引擎」：所有平台经服务层 API 访问数据，
不直接连 StarRocks 或 Paimon——底层引擎换了，上层应用无感。本模块就是那层「不碰」：
业务平台传业务语义（表、维度、过滤、排序），拿回 dict 行，永远不写 SQL、不知道连接串。

三条硬约束：
  1. 表白名单：只允许 products.PRODUCTS 里的 11 张 ADS 表（出口收敛）；
  2. 字段白名单：标识符只能取自 schema.column_names()，用户输入绝不进标识符位置；
  3. 取值参数化：所有字面量走 %s 占位符交给驱动转义，杜绝 SQL 注入。

外部依赖是延迟 import 的：没装 pymysql / mysql-connector-python 时，
import 本模块不炸，只有真正建连接时才抛 StarRocksUnavailableError。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Protocol

from ..config import settings
from .constants import (
    ADS_QUERY_DEFAULT_LIMIT,
    ADS_QUERY_MAX_LIMIT,
    ADS_QUERY_SLO_MS,
    ADS_QUERY_TIMEOUT_SECONDS,
    ADS_RESULT_CACHE_TTL_SECONDS,
)
from .errors import (
    AdsQueryError,
    InvalidFilterError,
    StarRocksUnavailableError,
    UnknownColumnError,
)
from .products import AdsProduct, get_product
from .routing import QueryRoute, WorkloadKind, qualify, route_for
from .schema import column_names, require_column

__all__ = [
    "Filter",
    "AdsQuery",
    "QueryResult",
    "RowSource",
    "StarRocksRowSource",
    "StaticRowSource",
    "AdsQueryService",
    "SUPPORTED_OPERATORS",
]

_LOG = logging.getLogger(__name__)

Row = dict[str, Any]

#: 允许的过滤算子。白名单而非黑名单——没登记的算子一律拒绝。
SUPPORTED_OPERATORS: frozenset[str] = frozenset(
    {"=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "BETWEEN", "LIKE", "IS NULL", "IS NOT NULL"}
)
_NO_VALUE_OPERATORS: frozenset[str] = frozenset({"IS NULL", "IS NOT NULL"})
_MULTI_VALUE_OPERATORS: frozenset[str] = frozenset({"IN", "NOT IN"})


@dataclass(frozen=True, slots=True)
class Filter:
    """一个过滤条件。

    Args:
        column: 字段名，必须属于目标表（构造 SQL 时用 schema.require_column 校验）。
        op: 算子，取自 :data:`SUPPORTED_OPERATORS`。
        value: 取值。``IN``/``NOT IN`` 传序列，``BETWEEN`` 传二元组，
            ``IS NULL``/``IS NOT NULL`` 不传。
    """

    column: str
    op: str = "="
    value: Any = None

    def __post_init__(self) -> None:
        op = self.op.upper().strip()
        if op not in SUPPORTED_OPERATORS:
            raise InvalidFilterError(
                f"不支持的算子 {self.op!r}；可用：{', '.join(sorted(SUPPORTED_OPERATORS))}"
            )
        object.__setattr__(self, "op", op)
        if op in _NO_VALUE_OPERATORS:
            return
        if op in _MULTI_VALUE_OPERATORS:
            if not isinstance(self.value, (list, tuple, set, frozenset)) or not self.value:
                raise InvalidFilterError(f"{op} 需要一个非空序列，收到 {self.value!r}")
            object.__setattr__(self, "value", tuple(self.value))
            return
        if op == "BETWEEN":
            if not isinstance(self.value, (list, tuple)) or len(self.value) != 2:
                raise InvalidFilterError(f"BETWEEN 需要 (下界, 上界) 二元组，收到 {self.value!r}")
            object.__setattr__(self, "value", tuple(self.value))
            return
        if self.value is None:
            raise InvalidFilterError(f"算子 {op} 需要取值，收到 None（判空请用 IS NULL）")

    # ---- 渲染 ----

    def render(self, table: str) -> tuple[str, list[Any]]:
        """渲染成 (SQL 片段, 参数列表)。字段名经白名单校验后才拼进 SQL。"""
        col = f"`{require_column(table, self.column)}`"
        if self.op in _NO_VALUE_OPERATORS:
            return f"{col} {self.op}", []
        if self.op in _MULTI_VALUE_OPERATORS:
            holders = ", ".join("%s" for _ in self.value)
            return f"{col} {self.op} ({holders})", list(self.value)
        if self.op == "BETWEEN":
            return f"{col} BETWEEN %s AND %s", list(self.value)
        return f"{col} {self.op} %s", [self.value]

    def matches(self, row: Row) -> bool:
        """在内存里判定一行是否命中——供 :class:`StaticRowSource` 离线复用同一套语义。"""
        actual = row.get(self.column)
        if self.op == "IS NULL":
            return actual is None
        if self.op == "IS NOT NULL":
            return actual is not None
        if actual is None:
            return False
        if self.op == "IN":
            return actual in self.value
        if self.op == "NOT IN":
            return actual not in self.value
        if self.op == "BETWEEN":
            return self.value[0] <= actual <= self.value[1]
        if self.op == "LIKE":
            pattern = str(self.value)
            body = pattern.strip("%")
            if pattern.startswith("%") and pattern.endswith("%"):
                return body in str(actual)
            if pattern.startswith("%"):
                return str(actual).endswith(body)
            if pattern.endswith("%"):
                return str(actual).startswith(body)
            return str(actual) == pattern
        if self.op == "=":
            return bool(actual == self.value)
        if self.op == "!=":
            return bool(actual != self.value)
        if self.op == ">":
            return bool(actual > self.value)
        if self.op == ">=":
            return bool(actual >= self.value)
        if self.op == "<":
            return bool(actual < self.value)
        return bool(actual <= self.value)  # "<="


@dataclass(frozen=True, slots=True)
class AdsQuery:
    """一次 ADS 查询的业务语义描述（不含任何 SQL）。

    Args:
        table: 11 张 ADS 表之一。
        columns: 要取的字段；空表示取全部字段。
        filters: 过滤条件列表，按 AND 连接。
        order_by: 排序字段列表，元素形如 ``("badcase_count", "DESC")``。
        limit: 返回行数上限，默认 :data:`constants.ADS_QUERY_DEFAULT_LIMIT`。
        offset: 分页偏移。
        workload: 负载类型，决定走内表还是 Paimon 外部表（[S1-全景] 第七章选路）。
    """

    table: str
    columns: tuple[str, ...] = ()
    filters: tuple[Filter, ...] = ()
    order_by: tuple[tuple[str, str], ...] = ()
    limit: int = ADS_QUERY_DEFAULT_LIMIT
    offset: int = 0
    workload: WorkloadKind = WorkloadKind.DASHBOARD_REPORT

    def __post_init__(self) -> None:
        get_product(self.table)  # 表白名单，未登记直接抛 UnknownTableError
        if self.limit <= 0 or self.limit > ADS_QUERY_MAX_LIMIT:
            raise AdsQueryError(
                f"limit 必须在 1~{ADS_QUERY_MAX_LIMIT} 之间（大屏一屏的量级），收到 {self.limit}"
            )
        if self.offset < 0:
            raise AdsQueryError(f"offset 不能为负，收到 {self.offset}")
        for col in self.columns:
            require_column(self.table, col)
        for col, direction in self.order_by:
            require_column(self.table, col)
            if direction.upper() not in ("ASC", "DESC"):
                raise AdsQueryError(f"排序方向只能是 ASC/DESC，收到 {direction!r}")

    @property
    def product(self) -> AdsProduct:
        return get_product(self.table)

    def with_filters(self, *extra: Filter) -> AdsQuery:
        """派生一个追加了过滤条件的新查询（本对象不可变）。"""
        return replace(self, filters=self.filters + tuple(extra))

    # ---- 渲染 ----

    def render(self) -> tuple[str, list[Any]]:
        """渲染成 (参数化 SQL, 参数列表)。

        Returns:
            SQL 里所有字面量都是 ``%s`` 占位符，标识符全部来自白名单。
        """
        route = route_for(self.workload).route
        cols = self.columns or column_names(self.table)
        select = ", ".join(f"`{require_column(self.table, c)}`" for c in cols)
        sql = [f"SELECT {select}", f"FROM {qualify(self.table, route)}"]

        params: list[Any] = []
        if self.filters:
            clauses = []
            for flt in self.filters:
                clause, values = flt.render(self.table)
                clauses.append(clause)
                params.extend(values)
            sql.append("WHERE " + " AND ".join(clauses))
        if self.order_by:
            sql.append("ORDER BY " + ", ".join(f"`{c}` {d.upper()}" for c, d in self.order_by))
        sql.append(f"LIMIT {int(self.limit)} OFFSET {int(self.offset)}")
        return "\n".join(sql), params


@dataclass(frozen=True, slots=True)
class QueryResult:
    """一次查询的结果与执行元信息。

    elapsed_ms 超过 :data:`constants.ADS_QUERY_SLO_MS` 时 ``slow`` 为真——
    ADS 内表是「毫秒级直查」的负载，慢了通常意味着选错了路或物化没跑。
    """

    table: str
    rows: tuple[Row, ...]
    elapsed_ms: float
    route: QueryRoute
    from_cache: bool = False

    @property
    def slow(self) -> bool:
        return self.elapsed_ms > ADS_QUERY_SLO_MS

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)


# --------------------------------------------------------------------------- 行源


class RowSource(Protocol):
    """执行 SQL 并返回字典行的端口。

    有两个实现：:class:`StarRocksRowSource`（生产）与 :class:`StaticRowSource`
    （离线/测试/演示）。服务层只依赖这个协议，因此不接 StarRocks 也能跑通全部业务逻辑。
    """

    def execute(
        self, sql: str, params: Sequence[Any], *, query: AdsQuery | None = None
    ) -> list[Row]: ...


class StarRocksRowSource:
    """StarRocks 行源：走 MySQL 协议端口（宿主机默认 18630，见 config.StarRocksConfig）。

    驱动是延迟 import 的：pymysql 优先，其次 mysql-connector-python。
    两者都没有时，import 本模块不受影响，只有真正查询时抛
    :class:`errors.StarRocksUnavailableError`。
    """

    def __init__(self, *, timeout_seconds: int = ADS_QUERY_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds
        self._conn: Any | None = None
        self._driver: str = ""

    # ---- 连接 ----

    def _connect(self) -> Any:
        cfg = settings().starrocks
        try:
            import pymysql  # type: ignore[import-not-found]
            from pymysql.cursors import DictCursor  # type: ignore[import-not-found]
        except ImportError:
            pass
        else:
            self._driver = "pymysql"
            return pymysql.connect(
                host=cfg.fe_host,
                port=cfg.query_port,
                user=cfg.user,
                password=cfg.password,
                database=cfg.internal_database,
                charset="utf8mb4",
                cursorclass=DictCursor,
                connect_timeout=self._timeout,
                read_timeout=self._timeout,
                autocommit=True,
            )
        try:
            import mysql.connector  # type: ignore[import-not-found]
        except ImportError:
            raise StarRocksUnavailableError(
                "查询 ADS 内表需要 MySQL 协议驱动，请先安装 pymysql 或 mysql-connector-python："
                "pip install pymysql"
            ) from None
        self._driver = "mysql-connector"
        return mysql.connector.connect(
            host=cfg.fe_host,
            port=cfg.query_port,
            user=cfg.user,
            password=cfg.password,
            database=cfg.internal_database,
            connection_timeout=self._timeout,
        )

    def connection(self) -> Any:
        """惰性建连并复用。连接失败统一包成 StarRocksUnavailableError。"""
        if self._conn is None:
            cfg = settings().starrocks
            try:
                self._conn = self._connect()
            except StarRocksUnavailableError:
                raise
            except Exception as exc:  # 驱动各自的异常类型不统一，这里统一归口
                raise StarRocksUnavailableError(
                    f"连接 StarRocks 失败（{cfg.fe_host}:{cfg.query_port}"
                    f"/{cfg.internal_database}）：{exc}"
                ) from exc
        return self._conn

    # ---- 执行 ----

    def execute(
        self, sql: str, params: Sequence[Any], *, query: AdsQuery | None = None
    ) -> list[Row]:
        conn = self.connection()
        try:
            if self._driver == "pymysql":
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    return list(cur.fetchall())
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute(sql, tuple(params))
                return list(cur.fetchall())
            finally:
                cur.close()
        except StarRocksUnavailableError:
            raise
        except Exception as exc:
            self.close()  # 连接可能已废，下次重建
            raise StarRocksUnavailableError(f"StarRocks 查询失败：{exc}\nSQL:\n{sql}") from exc

    def close(self) -> None:
        """关闭连接，异常吞掉——关连接失败不应该影响调用方。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - 关闭失败无需上抛
                _LOG.debug("关闭 StarRocks 连接时出错", exc_info=True)
            finally:
                self._conn = None

    def __enter__(self) -> StarRocksRowSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class StaticRowSource:
    """内存行源：把预置数据当成 ADS 内表，用同一套过滤/排序/分页语义执行查询。

    ⚠️ 原文未明确，本项目设计：原文只讲生产链路，没有离线形态。但服务层必须能在
    没有 StarRocks 的环境里被测试与演示（验收阶段的全量 import 检查、单元测试、
    产品评审的假数据大屏），因此提供这个实现——它复用 :meth:`Filter.matches`，
    保证内存语义与 SQL 语义同源，不会出现「测试过了、线上不一样」。
    """

    def __init__(self, tables: dict[str, Iterable[Row]] | None = None) -> None:
        self._tables: dict[str, list[Row]] = {
            name: [dict(r) for r in rows] for name, rows in (tables or {}).items()
        }

    def put(self, table: str, rows: Iterable[Row]) -> None:
        """写入/覆盖一张表的数据。表名同样要在 11 张产品矩阵内。"""
        get_product(table)
        self._tables[table] = [dict(r) for r in rows]

    def execute(
        self, sql: str, params: Sequence[Any], *, query: AdsQuery | None = None
    ) -> list[Row]:
        if query is None:
            raise AdsQueryError(
                "StaticRowSource 不解析 SQL 文本，请经 AdsQueryService 调用（它会带上 query 对象）"
            )
        rows = self._tables.get(query.table, [])
        out = [r for r in rows if all(f.matches(r) for f in query.filters)]
        for col, direction in reversed(query.order_by):
            out.sort(key=lambda r: _sort_key(r.get(col)), reverse=direction.upper() == "DESC")
        out = out[query.offset : query.offset + query.limit]
        if query.columns:
            out = [{c: r.get(c) for c in query.columns} for r in out]
        return [dict(r) for r in out]


def _sort_key(value: Any) -> tuple[int, Any]:
    """排序键：None 一律排最后，其余按原值。避免 None 与数值比较直接 TypeError。"""
    if value is None:
        return (1, 0)
    if isinstance(value, (int, float, str, date, datetime)):
        return (0, value)
    return (0, str(value))


# --------------------------------------------------------------------------- 服务


@dataclass(slots=True)
class _CacheEntry:
    rows: tuple[Row, ...]
    stored_at: float


@dataclass(slots=True)
class AdsQueryService:
    """ADS 查询接口层：业务平台唯一的取数入口。

    Args:
        source: 行源。生产传 :class:`StarRocksRowSource`，测试/演示传
            :class:`StaticRowSource`。
        cache_ttl_seconds: 结果缓存 TTL，0 表示关闭缓存。
            默认 :data:`constants.ADS_RESULT_CACHE_TTL_SECONDS`。

    Examples:
        >>> svc = AdsQueryService(StaticRowSource({"ads_closed_loop_dashboard": [
        ...     {"stat_date": "2026-08-28", "project_code": "P1",
        ...      "avg_closed_loop_hours": 216.0}]}))
        >>> result = svc.fetch(AdsQuery("ads_closed_loop_dashboard",
        ...                             columns=("project_code", "avg_closed_loop_hours")))
        >>> result.rows[0]["avg_closed_loop_hours"]
        216.0
    """

    source: RowSource
    cache_ttl_seconds: int = ADS_RESULT_CACHE_TTL_SECONDS
    _cache: dict[tuple[Any, ...], _CacheEntry] = field(default_factory=dict, repr=False)

    # ---- 取数 ----

    def fetch(self, query: AdsQuery, *, use_cache: bool = True) -> QueryResult:
        """执行一次查询。

        Args:
            query: 查询描述。
            use_cache: 是否允许命中结果缓存（T+1 数据一天只变一次，默认允许）。

        Returns:
            :class:`QueryResult`，含行数据、耗时与选路信息。

        Raises:
            UnknownTableError / UnknownColumnError / InvalidFilterError: 调用方参数问题。
            StarRocksUnavailableError: 后端不可用。
        """
        route = route_for(query.workload).route
        sql, params = query.render()
        key = self._cache_key(sql, params)

        if use_cache and self.cache_ttl_seconds > 0:
            hit = self._cache.get(key)
            if hit is not None and (time.monotonic() - hit.stored_at) <= self.cache_ttl_seconds:
                return QueryResult(query.table, hit.rows, 0.0, route, from_cache=True)

        started = time.perf_counter()
        rows = tuple(self.source.execute(sql, params, query=query))
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if self.cache_ttl_seconds > 0:
            self._cache[key] = _CacheEntry(rows, time.monotonic())
        if elapsed_ms > ADS_QUERY_SLO_MS:
            _LOG.warning(
                "ADS 查询超出 %d ms SLO：table=%s elapsed=%.1fms route=%s",
                ADS_QUERY_SLO_MS,
                query.table,
                elapsed_ms,
                route.value,
            )
        return QueryResult(query.table, rows, elapsed_ms, route)

    def fetch_rows(self, query: AdsQuery, *, use_cache: bool = True) -> list[Row]:
        """只要行数据的便捷版。"""
        return list(self.fetch(query, use_cache=use_cache).rows)

    def fetch_one(self, query: AdsQuery, *, use_cache: bool = True) -> Row | None:
        """取第一行，没有则返回 None。"""
        rows = self.fetch(replace(query, limit=1), use_cache=use_cache).rows
        return rows[0] if rows else None

    def latest_stat_date(self, table: str) -> Any | None:
        """取该 ADS 表已物化的最新统计日期。

        大屏进入页面时先问这个：T+1 物化有没有跑、数据截至哪天，一次查询说清楚。

        Returns:
            最新的日期值；表没有日期维度（如 ads_ota_deployment_summary）或无数据时返回 None。
        """
        product = get_product(table)
        if not product.date_column:
            return None
        row = self.fetch_one(
            AdsQuery(
                table,
                columns=(product.date_column,),
                order_by=((product.date_column, "DESC"),),
            )
        )
        return row[product.date_column] if row else None

    def latest_snapshot(
        self,
        table: str,
        *,
        filters: Sequence[Filter] = (),
        columns: Sequence[str] = (),
        order_by: Sequence[tuple[str, str]] = (),
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
        stat_date: Any | None = None,
    ) -> list[Row]:
        """取「最新一天」的整屏数据——六项业务服务里最常用的取数形态。

        Args:
            table: ADS 表名。
            filters: 附加过滤条件。
            columns: 需要的字段，空表示全部。
            order_by: 排序。
            limit: 行数上限。
            stat_date: 指定统计日期；不传则自动取该表已物化的最新一天。

        Returns:
            行列表；该表还没物化出任何数据时返回空列表。
        """
        product = get_product(table)
        all_filters = list(filters)
        if product.date_column:
            day = stat_date if stat_date is not None else self.latest_stat_date(table)
            if day is None:
                return []
            all_filters.append(Filter(product.date_column, "=", day))
        query = AdsQuery(
            table,
            columns=tuple(columns),
            filters=tuple(all_filters),
            order_by=tuple(order_by),
            limit=limit,
        )
        return self.fetch_rows(query)

    # ---- 元信息 ----

    @staticmethod
    def describe(table: str) -> dict[str, Any]:
        """返回该 ADS 表的产品说明：服务对象、计算维度、核心指标、上游链路、字段清单。

        业务平台接入时先调它——「这张表能回答什么问题」不需要翻文档。
        """
        p = get_product(table)
        return {
            "table": p.table,
            "ordinal": p.ordinal,
            "title_cn": p.title_cn,
            "theme": p.theme.name_cn,
            "serves": [s.name_cn for s in p.serves],
            "domain": p.domain.name_cn,
            "dimensions": p.dimensions_cn,
            "core_metrics": list(p.core_metrics_cn),
            "scenario": p.scenario_cn,
            "source_tables": list(p.source_tables),
            "services": [s.name_cn for s in p.consumed_by],
            "key_columns": list(p.key_columns),
            "date_column": p.date_column,
            "refresh": "T+1",
            "columns": list(column_names(p.table)),
            "notes": p.notes,
        }

    def invalidate_cache(self, table: str | None = None) -> int:
        """清空结果缓存（物化作业跑完后调用）。返回清掉的条目数。"""
        if table is None:
            n = len(self._cache)
            self._cache.clear()
            return n
        get_product(table)
        keys = [k for k in self._cache if k[0] == table]
        for k in keys:
            del self._cache[k]
        return len(keys)

    @staticmethod
    def _cache_key(sql: str, params: Sequence[Any]) -> tuple[Any, ...]:
        table = sql.split("`")[-2] if "`" in sql else sql
        return (table, sql, tuple(str(p) for p in params))


def ensure_columns(table: str, columns: Sequence[str]) -> tuple[str, ...]:
    """批量校验字段并回传，供服务层在构造查询前做一次显式检查。

    Raises:
        UnknownColumnError: 任一字段不属于该表。
    """
    missing = [c for c in columns if c not in column_names(table)]
    if missing:
        raise UnknownColumnError(f"{table} 缺少字段：{', '.join(missing)}")
    return tuple(columns)
