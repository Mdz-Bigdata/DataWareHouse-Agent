# -*- coding: utf-8 -*-
"""本体声明 ↔ 物理库实际 schema 的漂移校验。

为什么要有这个文件
------------------
本体（`apps/data-agent-engine/backend/ontology/objects.yaml`）用
`source_tables[].field_mapping` 把业务属性钉到物理列上。物理表改了列名、
删了列、换了类型，本体不会知道——**漂移只在用户问数、SQL 执行失败的那一刻
才暴露**，而且暴露给的是用户，不是维护者。

这个模块把「本体说有的表/列」和「物理库实际有的表/列」摆在一起比，
输出缺失 / 多余 / 类型不符，可以当 CI 卡口，也可以当运维命令随时跑。

用法
----
    # 对样例库跑（data-agent-engine 自带的 SQLite 样例库）
    python -m app.service.schema_drift \\
        --ontology-dir apps/data-agent-engine/backend/ontology \\
        --sqlite apps/data-agent-engine/backend/seed/sample.db

    # 对真实数仓跑（Doris / MySQL / PostgreSQL，只读元数据，不扫数据）
    python -m app.service.schema_drift \\
        --ontology-dir apps/data-agent-engine/backend/ontology \\
        --url "doris://ro_user:***@fe-host:9030/dw_store"

退出码：0 = 无 error 级漂移；1 = 有；2 = 用法/配置错误。

只读保证：只读 `PRAGMA table_info` / `information_schema`，
不 SELECT 业务数据，不写任何东西。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

__all__ = [
    "DriftIssue",
    "TableDeclaration",
    "SchemaReader",
    "DictSchemaReader",
    "SqliteSchemaReader",
    "SqlAlchemySchemaReader",
    "declarations_from_objects",
    "check_schema_drift",
    "type_family",
    "main",
]

SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


# ── 类型归一 ──────────────────────────────────────────────────────────
# 物理类型（各引擎写法各异）→ 类型族。比较的是族，不是字面量：
# Doris 的 VARCHAR(50) 与 SQLite 的 TEXT 是同一族，不该报成漂移。
_FAMILY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^(bool|boolean|bit)", "bool"),
    (r"(decimal|numeric|money)", "decimal"),
    (r"(double|float|real)", "float"),
    (r"(timestamp|datetime)", "datetime"),
    (r"^date$|^date\b", "date"),
    (r"^time\b", "time"),
    (r"(int|serial|long)", "int"),
    (r"(char|text|string|varchar|clob|uuid|json|enum)", "text"),
    (r"(binary|blob|bytes)", "binary"),
)

# 本体业务类型 → 物理上可以接受的类型族。
# 刻意放宽的两处，都是这个仓库既有的存储约定，不是校验漏洞：
#   * date 接受 text/int —— 分区列 dt 按约定存 'YYYYMMDD' 字符串
#   * decimal 接受 float/int —— SQLite 用 REAL 存金额
_ACCEPTED_FAMILIES: dict[str, frozenset[str]] = {
    "string": frozenset({"text"}),
    "text": frozenset({"text"}),
    "decimal": frozenset({"decimal", "float", "int"}),
    "double": frozenset({"decimal", "float", "int"}),
    "float": frozenset({"decimal", "float", "int"}),
    "date": frozenset({"date", "datetime", "text", "int"}),
    "datetime": frozenset({"datetime", "date", "text", "int"}),
    "timestamp": frozenset({"datetime", "date", "text", "int"}),
    "int": frozenset({"int"}),
    "integer": frozenset({"int"}),
    "bigint": frozenset({"int"}),
    "bool": frozenset({"bool", "int"}),
    "boolean": frozenset({"bool", "int"}),
}

# 预聚合列（指标值）与必要过滤标志列的期望族
_NUMERIC_FAMILIES = frozenset({"int", "float", "decimal"})


def type_family(physical_type: str | None) -> str:
    """物理类型字面量 → 类型族；认不出返回 'unknown'（不报漂移，避免假阳性）。"""
    raw = (physical_type or "").strip().lower()
    if not raw:
        return "unknown"
    for pattern, family in _FAMILY_PATTERNS:
        if re.search(pattern, raw):
            return family
    return "unknown"


# ── 数据结构 ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DriftIssue:
    severity: str          # error / warn / info
    kind: str
    obj: str               # 本体对象名（物理侧独有的问题为空串）
    table: str
    column: str
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = f"{self.table}.{self.column}" if self.column else self.table
        owner = f"[{self.obj}] " if self.obj else ""
        return f"[{self.severity}] {owner}{where} — {self.detail}"


@dataclass
class TableDeclaration:
    """本体对一张物理表的全部声明，已从对象结构里摊平。"""

    obj: str
    table: str
    layer: str = ""
    status: str = "active"
    object_status: str = "active"
    # 物理列 → 期望的业务类型（None = 本体没声明类型，只校验列是否存在）
    columns: dict[str, str | None] = field(default_factory=dict)
    # 预聚合指标列：物理列 → 指标名
    measure_columns: dict[str, str] = field(default_factory=dict)
    perm_column: str = ""
    required_filter_columns: tuple[str, ...] = ()
    detail_capable: bool = False

    @property
    def declared_columns(self) -> set[str]:
        cols = set(self.columns) | set(self.measure_columns)
        if self.perm_column:
            cols.add(self.perm_column)
        cols.update(self.required_filter_columns)
        return cols


class SchemaReader(Protocol):
    """物理库 schema 的最小只读接口。"""

    def tables(self) -> set[str]: ...

    def columns(self, table: str) -> dict[str, str]: ...


class DictSchemaReader:
    """从 {表名: {列名: 类型}} 构造的只读 reader（快照比对与测试用）。"""

    def __init__(self, schema: Mapping[str, Mapping[str, str]]):
        self._schema = {str(t).lower(): {str(c).lower(): str(ty)
                                         for c, ty in cols.items()}
                        for t, cols in schema.items()}

    def tables(self) -> set[str]:
        return set(self._schema)

    def columns(self, table: str) -> dict[str, str]:
        return dict(self._schema.get(table.lower(), {}))


class SqliteSchemaReader:
    """SQLite：PRAGMA table_info。只读打开，绝不建表。"""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def tables(self) -> set[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%'")
            return {r[0].lower() for r in rows}
        finally:
            conn.close()

    def columns(self, table: str) -> dict[str, str]:
        if not _SAFE_IDENT.match(table):
            return {}
        conn = self._connect()
        try:
            # PRAGMA 不支持参数绑定，因此上面先用白名单正则卡死表名
            return {row[1].lower(): row[2] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            return {}
        finally:
            conn.close()


class SqlAlchemySchemaReader:
    """任何 SQLAlchemy 能连的库（Doris/StarRocks 走 MySQL 协议）的 schema。

    只用 Inspector 读元数据，不发业务查询。
    """

    def __init__(self, url: str, *, schemas: Sequence[str] | None = None,
                 connect_timeout: int = 10):
        from sqlalchemy import create_engine, inspect  # 延迟导入：不用远程库时不付代价

        connect_args: dict[str, Any] = {}
        if url.split("://", 1)[0].split("+", 1)[0] in {"mysql", "doris", "starrocks", "postgresql"}:
            connect_args["connect_timeout"] = connect_timeout
        self._engine = create_engine(url, connect_args=connect_args)
        self._inspector = inspect(self._engine)
        self._schemas = list(schemas) if schemas else [None]

    def tables(self) -> set[str]:
        out: set[str] = set()
        for schema in self._schemas:
            out.update(name.lower() for name in self._inspector.get_table_names(schema=schema))
            out.update(name.lower() for name in self._inspector.get_view_names(schema=schema))
        return out

    def columns(self, table: str) -> dict[str, str]:
        for schema in self._schemas:
            try:
                cols = self._inspector.get_columns(table, schema=schema)
            except Exception:  # noqa: BLE001 —— 某个 schema 里没这张表就换下一个
                continue
            if cols:
                return {c["name"].lower(): str(c["type"]) for c in cols}
        return {}

    def dispose(self) -> None:
        self._engine.dispose()


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


# ── 本体 → 声明 ───────────────────────────────────────────────────────
def _required_filter_columns(entries: Iterable[str]) -> tuple[str, ...]:
    """['is_valid = 1'] → ('is_valid',)。取比较式左侧的标识符。"""
    cols: list[str] = []
    for raw in entries or ():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", str(raw))
        if match:
            cols.append(match.group(1).lower())
    return tuple(dict.fromkeys(cols))


def declarations_from_objects(objects: Iterable[Mapping[str, Any]]) -> list[TableDeclaration]:
    """本体 objects 列表 → 摊平的表声明。

    只依赖 objects 的结构（name / properties / source_tables），
    不 import data-agent-engine 的 Ontology 类——那个类要 PyYAML，
    而本模块要能在任何 venv 里跑。
    """
    prop_types: dict[str, dict[str, str | None]] = {}
    declarations: list[TableDeclaration] = []
    for obj in objects:
        name = str(obj.get("name", ""))
        prop_types[name] = {str(p["name"]): p.get("type")
                            for p in obj.get("properties", []) or []
                            if isinstance(p, Mapping) and p.get("name")}
        required = _required_filter_columns(obj.get("required_filters", []) or [])
        for table_entry in obj.get("source_tables", []) or []:
            if not isinstance(table_entry, Mapping) or not table_entry.get("table"):
                continue
            mapping = table_entry.get("field_mapping", {}) or {}
            pre_agg = table_entry.get("pre_aggregated", {}) or {}
            columns: dict[str, str | None] = {}
            for business_prop, physical_col in mapping.items():
                columns[str(physical_col).lower()] = prop_types[name].get(str(business_prop))
            declarations.append(TableDeclaration(
                obj=name,
                table=str(table_entry["table"]).lower(),
                layer=str(table_entry.get("layer", "")),
                status=str(table_entry.get("status", "active")),
                object_status=str(obj.get("status", "active")),
                columns=columns,
                measure_columns={str(col).lower(): str(metric)
                                 for metric, col in pre_agg.items()},
                perm_column=str(table_entry.get("perm_column", "") or "").lower(),
                required_filter_columns=required,
                # 没有预聚合列的表，翻译引擎会走明细模式，此时才会注入 required_filter
                detail_capable=not pre_agg,
            ))
    return declarations


def load_objects_from_yaml(ontology_dir: str | os.PathLike[str]) -> list[dict]:
    """读 ontology/objects.yaml。PyYAML 缺失时给明确指引而不是 ImportError 堆栈。"""
    path = Path(ontology_dir)
    if path.is_dir():
        path = path / "objects.yaml"
    try:
        import yaml  # noqa: PLC0415 —— 只有走 YAML 入口才需要这个依赖
    except ImportError as exc:
        raise RuntimeError(
            "读取本体 YAML 需要 PyYAML。要么在当前解释器里装 PyYAML，"
            "要么改用 --objects-json（由 data-agent-engine 的 ontology_export 导出）"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("objects", []))


# ── 校验 ──────────────────────────────────────────────────────────────
def check_schema_drift(objects: Iterable[Mapping[str, Any]], reader: SchemaReader, *,
                       report_unreferenced_columns: bool = True,
                       report_undeclared_tables: bool = True,
                       ignore_tables: Sequence[str] = ()) -> dict:
    """比对本体声明与物理 schema，返回结构化报告。

    报告里的 issues 已按严重度排序。severity 语义：
      error —— 本体照着这个声明翻译出来的 SQL 一定跑不起来（或行级权限失效）
      warn  —— 能跑，但口径/类型对不上，结果可能是错的
      info  —— 只是两边不一致的事实陈述，需要人看一眼
    """
    declarations = declarations_from_objects(objects)
    physical_tables = {t.lower() for t in reader.tables()}
    ignored = {t.lower() for t in ignore_tables}
    issues: list[DriftIssue] = []
    checked: list[str] = []
    declared_tables: set[str] = set()

    for decl in declarations:
        if decl.table in ignored:
            continue
        declared_tables.add(decl.table)

        if decl.status == "deprecated" or decl.object_status == "deprecated":
            issues.append(DriftIssue(
                "info", "deprecated_declaration", decl.obj, decl.table, "",
                "本体已标记 deprecated，跳过 schema 校验"))
            continue

        if decl.table not in physical_tables:
            issues.append(DriftIssue(
                "error", "missing_table", decl.obj, decl.table, "",
                "本体声明了这张表，物理库里不存在——任何路由到它的问数都会失败"))
            continue

        physical = {c.lower(): t for c, t in reader.columns(decl.table).items()}
        checked.append(decl.table)

        # ① field_mapping 指向的列必须存在，类型族必须兼容
        for column, business_type in sorted(decl.columns.items()):
            if column not in physical:
                issues.append(DriftIssue(
                    "error", "missing_column", decl.obj, decl.table, column,
                    f"本体 field_mapping 映射到这一列，物理表没有；"
                    f"物理实际列: {sorted(physical)}"))
                continue
            if not business_type:
                continue
            accepted = _ACCEPTED_FAMILIES.get(str(business_type).lower())
            actual = type_family(physical[column])
            if accepted and actual != "unknown" and actual not in accepted:
                issues.append(DriftIssue(
                    "warn", "type_mismatch", decl.obj, decl.table, column,
                    f"本体声明业务类型 {business_type}（期望 {sorted(accepted)}），"
                    f"物理类型 {physical[column]}（族 {actual}）"))

        # ② 预聚合指标列必须存在且是数值
        for column, metric in sorted(decl.measure_columns.items()):
            if column not in physical:
                issues.append(DriftIssue(
                    "error", "missing_measure_column", decl.obj, decl.table, column,
                    f"本体把指标 {metric} 声明为这一列的预聚合值，物理表没有这一列"))
                continue
            actual = type_family(physical[column])
            if actual not in _NUMERIC_FAMILIES and actual != "unknown":
                issues.append(DriftIssue(
                    "warn", "measure_not_numeric", decl.obj, decl.table, column,
                    f"指标 {metric} 的预聚合列物理类型是 {physical[column]}（族 {actual}），不是数值"))

        # ③ 行级权限列缺失 = 权限过滤静默失效，按 error 报
        if decl.perm_column and decl.perm_column not in physical:
            issues.append(DriftIssue(
                "error", "missing_perm_column", decl.obj, decl.table, decl.perm_column,
                "本体声明了行级权限列，物理表没有——权限过滤会失效或 SQL 直接报错"))

        # ④ required_filter 的标志列（只对会走明细模式的表校验）
        if decl.detail_capable:
            for column in decl.required_filter_columns:
                if column not in physical:
                    issues.append(DriftIssue(
                        "warn", "missing_required_filter_column", decl.obj, decl.table, column,
                        "本体 required_filters 引用了这一列，物理表没有——口径过滤无法注入"))

        # ⑤ 物理有、本体没引用的列（本体覆盖度，不是错误）
        if report_unreferenced_columns:
            extra = sorted(set(physical) - decl.declared_columns)
            if extra:
                issues.append(DriftIssue(
                    "info", "unreferenced_columns", decl.obj, decl.table, "",
                    f"物理列未被本体引用（{len(extra)} 个）: {extra}"))

    # ⑥ 物理有、本体完全没声明的表
    if report_undeclared_tables:
        for table in sorted(physical_tables - declared_tables - ignored):
            issues.append(DriftIssue(
                "info", "undeclared_table", "", table, "",
                "物理库里有这张表，本体没有任何对象声明它——问数覆盖不到"))

    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.table, i.column, i.kind))
    counts = {"error": 0, "warn": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return {
        "ok": counts["error"] == 0,
        "declared_tables": len(declared_tables),
        "checked_tables": len(checked),
        "physical_tables": len(physical_tables),
        "counts": counts,
        "issues": [i.as_dict() for i in issues],
    }


# ── CLI ───────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.service.schema_drift",
        description="校验本体声明的表/列与物理库实际 schema 是否漂移")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ontology-dir", help="含 objects.yaml 的本体目录（需要 PyYAML）")
    source.add_argument("--objects-json", help="本体 objects 的 JSON 文件（无需 PyYAML）")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sqlite", help="SQLite 库文件路径")
    target.add_argument("--url", help="SQLAlchemy 连接串（建议只读账号）")
    parser.add_argument("--schema", action="append", default=None,
                        help="要检索的 schema（可重复；PostgreSQL 常用）")
    parser.add_argument("--ignore-table", action="append", default=[], help="跳过某张表（可重复）")
    parser.add_argument("--no-unreferenced", action="store_true", help="不报“物理列未被本体引用”")
    parser.add_argument("--no-undeclared", action="store_true", help="不报“本体未声明的物理表”")
    parser.add_argument("--strict", action="store_true", help="warn 也算失败")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args(argv)

    try:
        if args.objects_json:
            payload = json.loads(Path(args.objects_json).read_text(encoding="utf-8"))
            objects = payload.get("objects", payload) if isinstance(payload, dict) else payload
        else:
            objects = load_objects_from_yaml(args.ontology_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"读取本体失败: {exc}", file=sys.stderr)
        return 2

    reader: SchemaReader
    try:
        if args.sqlite:
            if not Path(args.sqlite).is_file():
                print(f"SQLite 文件不存在: {args.sqlite}", file=sys.stderr)
                return 2
            reader = SqliteSchemaReader(args.sqlite)
        else:
            reader = SqlAlchemySchemaReader(args.url, schemas=args.schema)
    except Exception as exc:  # noqa: BLE001 —— 连不上库属于配置问题，报 2 不报栈
        print(f"连接物理库失败: {exc}", file=sys.stderr)
        return 2

    report = check_schema_drift(
        objects, reader,
        report_unreferenced_columns=not args.no_unreferenced,
        report_undeclared_tables=not args.no_undeclared,
        ignore_tables=args.ignore_table)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for issue in report["issues"]:
            print(str(DriftIssue(**issue)))
        counts = report["counts"]
        print(f"\n本体声明 {report['declared_tables']} 张表 / 物理库 {report['physical_tables']} 张表；"
              f"{counts['error']} error / {counts['warn']} warn / {counts['info']} info")
        if report["ok"]:
            print("✅ 无 error 级 schema 漂移")

    if report["counts"]["error"] or (args.strict and report["counts"]["warn"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
