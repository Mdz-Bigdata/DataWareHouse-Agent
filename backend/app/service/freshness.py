# -*- coding: utf-8 -*-
"""查询结果的数据新鲜度声明：这份数字截止到什么时候。

为什么要有这个文件
------------------
问数结果只给了数字，没给「数据截止到什么时候」。跑批延迟或失败时，
用户看到的是一个**安静的旧数**——数字本身没有任何地方提示它是旧的。
（`init/doris/dws_trade_order_summary_daily.sql` 的分区从 2026-08-01 起
写不进去，跑批连续失败了一个多月，问数照样出数，就是这个问题。）

成本约束（这是本模块设计的主要矛盾）
------------------------------------
最朴素的做法——每次问数对所有表打一次 `MAX(dt)`——在真实数仓上是笔实打实的
开销：一次问数一个用户，几十张表，每张一次全表/全分区扫描。所以这里分四层挡：

  1. **进程内 TTL 缓存**（默认 900s）。T+1 的表，15 分钟内 max(dt) 不会变，
     绝大多数问数根本不发探测 SQL。
  2. **只探测本次查询真正用到的表**，不是全库；表名从 SQL 里解析出来，
     再与已知表白名单取交集。
  3. **一次 UNION ALL 批量探测**：N 张表 1 次往返，不是 N 次。
  4. **失败负缓存**（默认 120s）+ `max_tables` 上限 + `DATA_FRESHNESS_MODE=off`
     总开关。探测失败或超限时降级成「新鲜度未知」，**绝不让新鲜度探测拖垮问数**。

安全
----
表名从不直接拼：必须同时通过 `_SAFE_IDENT` 正则**和**调用方给的
`known_tables` 白名单，两道都过才进 SQL。时间列同理，只从库里读到的
列名里选，不接受外部输入。

对外契约
--------
`snapshot()` 返回的是一个**附加**字段，调用方把它挂到结果里即可，
不改动任何既有返回结构。
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "TableFreshness",
    "FreshnessService",
    "extract_tables",
    "default_time_columns",
]

DEFAULT_TTL_S = 900.0            # 正常结果缓存 15 分钟
DEFAULT_NEGATIVE_TTL_S = 120.0   # 探测失败后 2 分钟内不重试
DEFAULT_MAX_TABLES = 8           # 单次问数最多探测几张表
DEFAULT_EXPECTED_LAG_DAYS = 1    # T+1：昨天的数据算新鲜

MODE_ENV = "DATA_FRESHNESS_MODE"  # auto（默认）/ cache_only / off

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)",
    re.IGNORECASE)

_FALLBACK_TIME_COLUMNS = ("dt", "date", "publish_date", "publish_time")


def default_time_columns() -> tuple[str, ...]:
    """时间/分区列候选。优先复用语义层那一份，避免两处各写一份。"""
    try:
        from app.service.semantic_layer import TIME_PARTITION_COLUMNS  # noqa: PLC0415
        return tuple(TIME_PARTITION_COLUMNS)
    except Exception:  # noqa: BLE001 —— 语义层不可用时退回本地默认
        return _FALLBACK_TIME_COLUMNS


def extract_tables(sql: str, known_tables: Iterable[str]) -> list[str]:
    """从 SQL 里取出本次真正用到的表（只保留白名单内的，保持出现顺序去重）。

    优先用 sqlglot 解析（CTE、子查询、别名都能认对）；解析失败退回正则。
    白名单交集是安全边界：解析出什么不重要，只有白名单里的名字才会进 SQL。
    """
    allowed = {str(t).lower() for t in known_tables}
    found: list[str] = []

    def take(name: str) -> None:
        low = name.lower().rsplit(".", 1)[-1]
        if low in allowed and low not in found:
            found.append(low)

    try:
        import sqlglot  # noqa: PLC0415 —— 解析失败要能退回正则，故延迟导入

        for table in sqlglot.parse_one(sql).find_all(sqlglot.exp.Table):
            take(table.name or "")
    except Exception:  # noqa: BLE001 —— 方言/语法问题不该影响主流程
        for match in _FROM_JOIN_RE.finditer(sql or ""):
            take(match.group(1))
    if not found:
        for match in _FROM_JOIN_RE.finditer(sql or ""):
            take(match.group(1))
    return found


def _normalize_value(value: Any) -> str | None:
    """max(dt) 的原始值 → 可比较可展示的字符串；空值返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _to_date(value: str | None) -> date | None:
    """'2026-09-12' / '20260912' / '2026-09' → date；认不出返回 None。"""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    try:
        if len(digits) == 8:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
        if len(digits) == 6:
            return date(int(digits[:4]), int(digits[4:6]), 1)
    except ValueError:
        return None
    return None


@dataclass(frozen=True)
class TableFreshness:
    """一张来源表的新鲜度事实。"""

    table: str
    latest: str | None          # 最新分区 / 最大时间戳（归一化字符串）
    time_column: str            # 用哪一列算的；空串 = 这张表没有时间列
    source: str                 # cache / probe / no_time_column / skipped / error / disabled
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class FreshnessService:
    """来源表新鲜度探测器（带缓存、批量、降级）。

    参数
    ----
    runner:
        `runner(sql) -> Sequence[Mapping]`。执行只读 SQL 并返回行字典序列。
        用 `FreshnessService.from_db_service(db_service)` 可直接接 DBService。
    known_tables:
        表名白名单。只有白名单里的表才会被拼进探测 SQL。
    column_lookup:
        `column_lookup(table) -> Iterable[str]`，返回该表的物理列名。
        用来挑时间列；返回空 = 这张表不探测（维表通常没有时间列）。
    scope:
        缓存作用域，用来隔离不同数据源（传 `db_service.source_id`）。
        换数据源不会读到上一个源的缓存。
    """

    def __init__(self, runner: Callable[[str], Sequence[Mapping[str, Any]]], *,
                 known_tables: Iterable[str],
                 column_lookup: Callable[[str], Iterable[str]] | None = None,
                 table_columns: Mapping[str, Sequence[str]] | None = None,
                 time_columns: Sequence[str] | None = None,
                 scope: str = "default",
                 ttl_s: float = DEFAULT_TTL_S,
                 negative_ttl_s: float = DEFAULT_NEGATIVE_TTL_S,
                 max_tables: int = DEFAULT_MAX_TABLES,
                 expected_lag_days: int = DEFAULT_EXPECTED_LAG_DAYS,
                 clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], date] = date.today):
        self._runner = runner
        self._known = {str(t).lower() for t in known_tables}
        self._table_columns = {str(k).lower(): tuple(v) for k, v in (table_columns or {}).items()}
        self._column_lookup = column_lookup
        self._time_columns = tuple(time_columns) if time_columns else default_time_columns()
        self._scope = scope
        self._ttl = float(ttl_s)
        self._negative_ttl = float(negative_ttl_s)
        self._max_tables = int(max_tables)
        self._expected_lag_days = int(expected_lag_days)
        self._clock = clock
        self._today = today
        self._cache: dict[tuple[str, str], tuple[float, TableFreshness]] = {}
        self._lock = threading.Lock()
        self.probe_calls = 0  # 可观测：本进程一共发了几次探测 SQL

    # ── 构造帮手 ──────────────────────────────────────────────────────
    @classmethod
    def from_db_service(cls, db_service: Any, **kwargs: Any) -> "FreshnessService":
        """接 app.service.db_service.DBService（execute_query 返回 DataFrame）。"""
        def runner(sql: str) -> Sequence[Mapping[str, Any]]:
            frame = db_service.execute_query(sql)
            return frame.to_dict("records")

        kwargs.setdefault("scope", getattr(db_service, "source_id", "default"))
        if "column_lookup" not in kwargs and "table_columns" not in kwargs:
            kwargs["column_lookup"] = _db_service_columns(db_service)
        return cls(runner, **kwargs)

    # ── 模式 ──────────────────────────────────────────────────────────
    @staticmethod
    def mode() -> str:
        """运行模式：auto（默认）/ cache_only（只读缓存不探测）/ off（完全关闭）。"""
        value = (os.getenv(MODE_ENV) or "auto").strip().lower()
        return value if value in {"auto", "cache_only", "off"} else "auto"

    def invalidate(self, table: str | None = None) -> None:
        """清缓存。跑批刚完成、或切换数据源之后调用。"""
        with self._lock:
            if table is None:
                self._cache.clear()
            else:
                self._cache.pop((self._scope, str(table).lower()), None)

    # ── 主入口 ────────────────────────────────────────────────────────
    def for_sql(self, sql: str) -> dict:
        """从 SQL 里解析来源表，再给出新鲜度快照。"""
        return self.snapshot(extract_tables(sql, self._known))

    def snapshot(self, tables: Iterable[str]) -> dict:
        """给一组来源表的新鲜度快照。

        返回 dict（**新增**字段，不改动任何既有返回结构）：
            enabled            —— 本次是否真的算了新鲜度
            as_of              —— 数据截止；取各来源表最新分区的**最小值**
            stale              —— 是否落后于预期（T+1）
            tables             —— 每张表的明细
            degraded / note    —— 降级原因（表太多 / 探测失败 / 被关闭）
            statement          —— 可直接贴给用户看的一句话
        """
        requested = [str(t).lower() for t in tables]
        wanted = [t for t in dict.fromkeys(requested) if t in self._known and _SAFE_IDENT.match(t)]
        mode = self.mode()

        if mode == "off":
            return self._empty("新鲜度探测已被 DATA_FRESHNESS_MODE=off 关闭", enabled=False)
        if not wanted:
            return self._empty("没有识别出可探测的来源表")

        degraded, note, public_note = False, "", ""
        if len(wanted) > self._max_tables:
            degraded = True
            note = (f"本次查询涉及 {len(wanted)} 张表，超过 max_tables={self._max_tables}，"
                    "为控制成本只用缓存，不发探测 SQL")
            public_note = "来源表过多，未探测新鲜度"

        results: dict[str, TableFreshness] = {}
        to_probe: list[tuple[str, str]] = []   # (表, 时间列)
        now = self._clock()

        for table in wanted:
            cached = self._get_cached(table, now)
            if cached is not None:
                results[table] = cached
                continue
            time_column = self._time_column_of(table)
            if not time_column:
                results[table] = TableFreshness(table, None, "", "no_time_column")
                continue
            if degraded or mode == "cache_only":
                reason = note or "DATA_FRESHNESS_MODE=cache_only，未发探测 SQL"
                results[table] = TableFreshness(table, None, time_column, "skipped", reason)
                # 没探到就是没探到：快照必须自曝降级，不能让调用方误以为“没有新鲜度问题”
                degraded = True
                note = note or reason
                public_note = public_note or "本次未探测新鲜度"
                continue
            to_probe.append((table, time_column))

        if to_probe:
            probed, probe_error = self._probe(to_probe)
            for table, time_column in to_probe:
                if table in probed:
                    entry = TableFreshness(table, probed[table], time_column, "probe")
                    self._put_cached(entry, self._ttl)
                else:
                    entry = TableFreshness(table, None, time_column, "error",
                                           probe_error or "探测返回里没有这张表")
                    # 负缓存：探测失败时不要每次问数都重试，别把故障放大
                    self._put_cached(entry, self._negative_ttl)
                    degraded = True
                    # note 给日志（带技术细节），public_note 给用户（不回显异常原文，
                    # 连接错误里可能夹着主机名甚至凭据）
                    note = note or f"新鲜度探测失败：{entry.error}"
                    public_note = public_note or "新鲜度探测失败"
                results[table] = entry

        ordered = [results[t] for t in wanted]
        # 负缓存命中也是「没探到」：走缓存返回的 error 条目不经过上面的探测分支，
        # 这里统一兜一次，否则故障期内第二次问数会显示成「新鲜度没问题」。
        if any(e.source == "error" for e in ordered):
            degraded = True
            note = note or next(f"新鲜度探测失败：{e.error}" for e in ordered if e.source == "error")
            public_note = public_note or "新鲜度探测失败"
        return self._build(ordered, degraded=degraded, note=note, public_note=public_note)

    # ── 内部 ──────────────────────────────────────────────────────────
    def _empty(self, note: str, *, enabled: bool = True) -> dict:
        return {"enabled": enabled, "as_of": None, "stale": None, "tables": [],
                "degraded": True, "note": note, "statement": "数据截止时间未知"}

    def _build(self, entries: Sequence[TableFreshness], *, degraded: bool, note: str,
               public_note: str = "") -> dict:
        values = [e.latest for e in entries if e.latest]
        as_of = min(values) if values else None
        as_of_date = _to_date(as_of)
        today = self._today()
        stale: bool | None = None
        if as_of_date is not None:
            stale = as_of_date < today - timedelta(days=self._expected_lag_days)

        if as_of is None:
            statement = "数据截止时间未知"
            if public_note:
                statement += f"（{public_note}）"
        else:
            sources = [e.table for e in entries if e.latest == as_of]
            statement = f"数据截止至 {as_of}"
            if len(entries) > 1:
                statement += f"（{len(entries)} 张来源表中最旧的一张：{', '.join(sources)}）"
            if stale:
                lag = (today - as_of_date).days if as_of_date else None
                statement += f"；已落后预期 {self._expected_lag_days} 天口径"
                if lag is not None:
                    statement += f"（距今 {lag} 天），上游跑批可能延迟或失败"
        return {
            "enabled": True,
            "as_of": as_of,
            "stale": stale,
            "expected_lag_days": self._expected_lag_days,
            "tables": [e.as_dict() for e in entries],
            "degraded": degraded,
            "note": note,
            "statement": statement,
        }

    def _get_cached(self, table: str, now: float) -> TableFreshness | None:
        with self._lock:
            hit = self._cache.get((self._scope, table))
            if hit is None:
                return None
            expires_at, entry = hit
            if expires_at <= now:
                self._cache.pop((self._scope, table), None)
                return None
        # 命中缓存的条目把 source 改成 cache，调用方能看出这次没走库
        return TableFreshness(entry.table, entry.latest, entry.time_column,
                              "cache" if entry.source != "error" else "error",
                              entry.error)

    def _put_cached(self, entry: TableFreshness, ttl: float) -> None:
        with self._lock:
            self._cache[(self._scope, entry.table)] = (self._clock() + ttl, entry)

    def _time_column_of(self, table: str) -> str:
        """挑这张表的时间列：按 time_columns 的优先序，取物理上真实存在的第一个。"""
        columns = self._table_columns.get(table)
        if columns is None and self._column_lookup is not None:
            try:
                columns = tuple(self._column_lookup(table))
            except Exception:  # noqa: BLE001 —— 元数据读不到就当没有时间列
                columns = ()
        available = {str(c).lower() for c in (columns or ())}
        for candidate in self._time_columns:
            low = str(candidate).lower()
            # 列名同样过白名单正则：它也要被拼进 SQL
            if low in available and _SAFE_IDENT.match(low):
                return low
        return ""

    def _probe(self, targets: Sequence[tuple[str, str]]) -> tuple[dict[str, str | None], str]:
        """一条 UNION ALL 批量探测：N 张表 1 次往返。"""
        parts = []
        for table, column in targets:
            # 双保险：能走到这里的表名/列名已经过白名单，这里再卡一次正则
            if not _SAFE_IDENT.match(table) or not _SAFE_IDENT.match(column):
                continue
            parts.append(f"SELECT '{table}' AS source_table, "
                         f"MAX({column}) AS latest_value FROM {table}")
        if not parts:
            return {}, "没有可安全拼接的表名"
        sql = "\nUNION ALL\n".join(parts)
        self.probe_calls += 1
        try:
            rows = self._runner(sql)
        except Exception as exc:  # noqa: BLE001 —— 新鲜度失败绝不能让问数失败
            return {}, f"{type(exc).__name__}: {exc}"
        out: dict[str, str | None] = {}
        for row in rows or []:
            name = str(row.get("source_table", "")).lower()
            if name:
                out[name] = _normalize_value(row.get("latest_value"))
        return out, ""


def _db_service_columns(db_service: Any) -> Callable[[str], Iterable[str]]:
    """DBService → column_lookup。从建表 DDL 里抠列名，不发业务查询。"""
    cache: dict[str, tuple[str, ...]] = {}

    def lookup(table: str) -> Iterable[str]:
        low = table.lower()
        if low in cache:
            return cache[low]
        columns: tuple[str, ...] = ()
        try:
            ddl = db_service.get_table_schema(table) or ""
            columns = tuple(dict.fromkeys(
                m.group(1).lower()
                for m in re.finditer(r"^\s*[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"`\]]?\s+\S",
                                     ddl, re.MULTILINE)))
        except Exception:  # noqa: BLE001 —— 元数据拿不到就当没有时间列
            columns = ()
        cache[low] = columns
        return columns

    return lookup
