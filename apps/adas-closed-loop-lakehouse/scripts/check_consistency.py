#!/usr/bin/env python3
"""跨模块一致性体检：子系统 / SQL / 编排文件是否都跟 catalog.registry 对得上。

这个仓库有一条硬规矩：**表定义归 catalog/tables/ 管，子系统只引用不定义**
（见 mining/tables.py、lifecycle/tables.py 等模块的 docstring）。规矩靠人守不住，
所以这里把它变成一条可执行的检查。

六项检查（`--only` 可单跑其中一项）::

    tables    src/ 里所有字符串形式的表名（ods_*/dwd_*/dws_*/ads_*）是否都在 registry
    columns   子系统显式声明的「表 → 列契约」是否都存在于 registry 对应表
    specs     子系统本地 TableSpec 与 registry 同名表的字段/主键/bucket/分区是否一致
    sql       ddl/ 与 flink/sql/ 里单表 SELECT 引用的列是否存在于该表
    env       config.py 等模块读的环境变量键是否被 .env.example 全覆盖
    deps      pyproject 声明的依赖与 src/ 实际 import 的第三方库是否双向对得上

用法::

    python3 scripts/check_consistency.py            # 全量，有问题退出码 1
    python3 scripts/check_consistency.py --only tables
    python3 scripts/check_consistency.py --quiet    # 只打小结

误报抑制：明显的占位符（``ads_xxx``/``ads_t1`` 之类）与 SQL 输出别名走
:data:`_TABLE_LITERAL_ALLOWLIST` / :data:`_SQL_IGNORED_IDENTIFIERS` 白名单，
加白名单时请写清楚理由——白名单越长，这个脚本越没用。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adas_lakehouse.catalog import registry  # noqa: E402

# --------------------------------------------------------------------------- 小工具


def _rel(p: Path) -> str:
    return str(p.relative_to(_REPO_ROOT))


def _py_files(*dirs: str):
    for d in dirs:
        for p in sorted((_REPO_ROOT / d).rglob("*.py")):
            if "__pycache__" not in p.parts:
                yield p


class Report:
    """收集问题，最后统一打印。"""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self.problems: list[str] = []
        self._section = ""

    def section(self, title: str) -> None:
        self._section = title
        if not self.quiet:
            print(f"\n=== {title} ===")

    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"  ✓ {msg}")

    def note(self, msg: str) -> None:
        if not self.quiet:
            print(f"  · {msg}")

    def bad(self, msg: str) -> None:
        self.problems.append(f"[{self._section}] {msg}")
        if not self.quiet:
            print(f"  ✗ {msg}")


# --------------------------------------------------------------------------- 1. 表名

#: src/ 里长得像表名、但确实不是表名的字符串字面量。每条都要有理由。
_TABLE_LITERAL_ALLOWLIST: dict[str, str] = {
    "ads_xxx": "ads/routing.py docstring 里的占位表名示例",
    "ads_t1": "ads/materialize.py docstring 里的 Flink 作业名前缀，不是表",
    "ads_t1_": "同上，pipeline.name = f'ads_t1_{table}'",
    "ods_sink": "quality/closed_loop.py 的构造参数名（写 ODS 的出口），不是表名",
}

_TABLE_RE = re.compile(r"\b((?:ods|dwd|dws|ads)_[a-z0-9_]+)\b")


def check_tables(rep: Report) -> None:
    rep.section("表名：src/ 字符串字面量 vs registry.all_tables()")
    known = {t.name for t in registry.all_tables()}

    hits: dict[str, list[str]] = {}
    for py in _py_files("src"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - 语法错在别处会先炸
            rep.bad(f"{_rel(py)} 解析失败: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in _TABLE_RE.finditer(node.value):
                    hits.setdefault(m.group(1), []).append(f"{_rel(py)}:{node.lineno}")

    bad = 0
    for name in sorted(hits):
        if name in known:
            continue
        if name in _TABLE_LITERAL_ALLOWLIST:
            rep.note(f"{name}（白名单：{_TABLE_LITERAL_ALLOWLIST[name]}）")
            continue
        bad += 1
        rep.bad(f"{name} 不在 registry —— 出现于 {', '.join(sorted(set(hits[name])))}")

    never = sorted(known - set(hits))
    if never:
        rep.note(f"registry 有、src/ 从未按字面量引用的表 {len(never)} 张: {', '.join(never)}")
    if not bad:
        rep.ok(f"{len(hits)} 种表名字面量全部落在 registry 的 {len(known)} 张表里")


# --------------------------------------------------------------------------- 2. 列契约


def _column_contracts() -> list[tuple[str, str, tuple[str, ...]]]:
    """子系统显式声明的「表 → 列契约」。新增契约时在这里登记。"""
    out: list[tuple[str, str, tuple[str, ...]]] = []

    from adas_lakehouse.mining import tables as mt

    out += [
        (
            "mining.tables.MINING_RESULT_COLUMNS",
            "dwd_mining_result_detail",
            mt.MINING_RESULT_COLUMNS,
        ),
        ("mining.tables.SCENE_GAP_COLUMNS", "dwd_scene_gap_detail", mt.SCENE_GAP_COLUMNS),
        ("mining.tables.MINING_TASK_COLUMNS", "dwd_mining_task_detail", mt.MINING_TASK_COLUMNS),
        ("mining.tables.RULE_CONFIG_COLUMNS", "ods_mining_rule_config", mt.RULE_CONFIG_COLUMNS),
    ]

    from adas_lakehouse.lineage import model as lm

    for label, (table, id_column) in lm.NODE_SOURCE_TABLES.items():
        out.append((f"lineage.NODE_SOURCE_TABLES[{label.value}]", table, (id_column,)))
    for rel, spec in lm.REL_SPECS.items():
        if spec.reconcile_table and spec.reconcile_column:
            out.append(
                (
                    f"lineage.REL_SPECS[{rel.value}].reconcile_column",
                    spec.reconcile_table,
                    (spec.reconcile_column,),
                )
            )

    from adas_lakehouse.quality.builtin import default_rule_center

    for rule in default_rule_center():
        if rule.table != "*" and rule.field:
            out.append((f"quality.builtin[{rule.rule_id}]", rule.table, (rule.field,)))

        # RuleSpec.field 只是规则**主**判据列。when 前置条件与 params 里的判据列
        # 同样要真实存在——它们不存在时规则不会报错，而是恒取 NULL 后**静默跳过**
        # （when 条件永不成立 / 状态机比不出旧态 / 双合规标记永远缺失）。
        # 这正是本次收口要根治的缺陷类型，所以一并纳入契约检查。
        params = rule.params or {}
        if rule.table != "*":
            aux: list[str] = []
            if rule.when and (wf := rule.when.get("field")):
                aux.append(wf)
            for key in ("from_field", "flag_field", "not_before_field", "group_field"):
                if isinstance(params.get(key), str):
                    aux.append(params[key])
            if isinstance(params.get("flags"), list):
                aux += [f for f in params["flags"] if isinstance(f, str)]
            if aux:
                out.append((f"quality.builtin[{rule.rule_id}].判据列", rule.table, tuple(aux)))

        # 跨源关联的对端列挂在 target_table 上，不是规则自己那张表
        tgt_table, tgt_field = params.get("target_table"), params.get("target_field")
        if isinstance(tgt_table, str) and isinstance(tgt_field, str):
            out.append((f"quality.builtin[{rule.rule_id}].target", tgt_table, (tgt_field,)))

    return out


def check_columns(rep: Report) -> None:
    rep.section("列契约：子系统声明的列是否存在于 registry 对应表")
    cols = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}

    bad = 0
    seen = 0
    for label, table, names in _column_contracts():
        seen += 1
        known = cols.get(table)
        if known is None:
            bad += 1
            rep.bad(f"{label}: 表 {table} 不在 registry")
            continue
        missing = [n for n in names if n not in known]
        if missing:
            bad += 1
            rep.bad(f"{label} -> {table}: registry 里没有这些列 {missing}")
    if not bad:
        rep.ok(f"{seen} 条列契约全部对得上")


# --------------------------------------------------------------------------- 3. 本地 TableSpec


def _local_specs() -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    from adas_lakehouse.lifecycle import tables as lt
    from adas_lakehouse.quality import tables as qt
    from adas_lakehouse.sampling import table as sat
    from adas_lakehouse.tags import tables as tt
    from adas_lakehouse.vector import schema as vs

    out += [("tags.tables", s) for s in tt.TABLES]
    out += [("lifecycle.tables", s) for s in lt.TABLES]
    out += [("quality.tables", s) for s in getattr(qt, "TABLES", ())]
    out.append(("sampling.table.FRAME_TABLE_SPEC", sat.FRAME_TABLE_SPEC))
    # vector 子系统已改为直接引用 registry，本地定义随之消失；哪天有人再加回来，
    # 这里照样把它捞出来比对（与上面 quality.tables 的 getattr 同一写法）。
    if (vector_local := getattr(vs, "_local_spec", None)) is not None:
        out.append(("vector.schema._local_spec", vector_local()))
    return out


def check_specs(rep: Report) -> None:
    rep.section("本地 TableSpec：子系统自带定义 vs registry 同名表")
    reg = {t.name: t for t in registry.all_tables()}

    bad = 0
    for owner, spec in _local_specs():
        other = reg.get(spec.name)
        if other is None:
            rep.note(f"{owner}: {spec.name} 尚未登记进 registry（装配前属预期）")
            continue
        mine = {c.name for c in spec.all_columns()}
        theirs = {c.name for c in other.all_columns()}
        extra = sorted(mine - theirs)
        if extra:
            bad += 1
            rep.bad(f"{owner}: {spec.name} 有 registry 没有的列 {extra}")
        if tuple(spec.primary_key) != tuple(other.primary_key):
            bad += 1
            rep.bad(
                f"{owner}: {spec.name} 主键不一致 本地={spec.primary_key} registry={other.primary_key}"
            )
        if spec.bucket != other.bucket:
            bad += 1
            rep.bad(
                f"{owner}: {spec.name} bucket 不一致 本地={spec.bucket} registry={other.bucket}"
            )
        if tuple(spec.partition_by) != tuple(other.partition_by):
            bad += 1
            rep.bad(
                f"{owner}: {spec.name} 分区不一致 本地={spec.partition_by} registry={other.partition_by}"
            )
    if not bad:
        rep.ok("本地 TableSpec 与 registry 一致")


# --------------------------------------------------------------------------- 4. SQL

#: SQL 里出现的反引号标识符，但不是列名的。
_SQL_IGNORED_IDENTIFIERS = {
    # Paimon / StarRocks 的库与 catalog 名
    "paimon",
    "paimon_catalog",
    "adas_lakehouse",
    "adas_ads",
    "default_catalog",
}

_SQL_TABLE_REF = re.compile(r"(?:FROM|JOIN)\s+((?:`[^`]+`\.)*`([^`]+)`)", re.IGNORECASE)
_SQL_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
_SQL_ALIAS = re.compile(r"\bAS\s+`([A-Za-z_][A-Za-z0-9_]*)`", re.IGNORECASE)
#: 语句自己创建/写入的对象名——是目标不是列引用
_SQL_TARGET = re.compile(
    r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+|MATERIALIZED\s+)?(?:VIEW|TABLE)"
    r"(?:\s+IF\s+NOT\s+EXISTS)?|INSERT\s+(?:INTO|OVERWRITE))\s+((?:`[^`]+`\.)*`([^`]+)`)",
    re.IGNORECASE,
)
#: CTE 名（``WITH x AS (`` / ``, x AS (``）——也是对象名不是列引用
_SQL_CTE = re.compile(r"(?:WITH|,)\s*`([A-Za-z_][A-Za-z0-9_]*)`\s+AS\s*\(", re.IGNORECASE)
#: ``UNNEST(...) AS t (`col`)`` 声明的派生列名——不是被查表的列
_SQL_UNNEST_ALIAS = re.compile(r"\bAS\s+\w+\s*\(([^)]*)\)", re.IGNORECASE)


def check_sql(rep: Report) -> None:
    rep.section("SQL：ddl/ 与 flink/sql/ 里单表 SELECT 引用的列 vs registry")
    cols = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}

    files = sorted((_REPO_ROOT / "ddl").glob("*.sql")) + sorted(
        (_REPO_ROOT / "flink" / "sql").glob("*.sql")
    )
    bad = 0
    checked = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        # 去注释，避免注释里的列名被当成引用
        text = re.sub(r"--[^\n]*", "", text)
        for stmt in text.split(";"):
            if not stmt.strip():
                continue
            refs = {m.group(2) for m in _SQL_TABLE_REF.finditer(stmt)}
            lake_refs = {r for r in refs if _TABLE_RE.fullmatch(r)}
            if len(lake_refs) != 1 or refs != lake_refs:
                # 多表 JOIN、或 JOIN 了视图/CTE：列的归属判不了，跳过
                continue
            table = next(iter(lake_refs))
            known = cols.get(table)
            if known is None:
                bad += 1
                line = text[: text.index(stmt)].count("\n") + 1 if stmt in text else 0
                rep.bad(f"{_rel(f)}:~{line} FROM {table} —— 该表不在 registry")
                continue
            if "CREATE TABLE" in stmt.upper():
                continue  # 建表语句自己声明列，不是引用
            checked += 1
            aliases = {m.group(1) for m in _SQL_ALIAS.finditer(stmt)}
            targets = {m.group(2) for m in _SQL_TARGET.finditer(stmt)}
            targets |= {m.group(1) for m in _SQL_CTE.finditer(stmt)}
            for m in _SQL_UNNEST_ALIAS.finditer(stmt):
                targets |= set(_SQL_IDENT.findall(m.group(1)))
            idents = {m.group(1) for m in _SQL_IDENT.finditer(stmt)}
            unknown = sorted(
                i
                for i in idents - aliases - targets - _SQL_IGNORED_IDENTIFIERS - refs
                if i not in known
            )
            if unknown:
                bad += 1
                line = text[: text.index(stmt)].count("\n") + 1
                rep.bad(f"{_rel(f)}:~{line} FROM {table} 引用了不存在的列 {unknown}")
    if not bad:
        rep.ok(f"{checked} 条单表语句的列引用全部对得上")


# --------------------------------------------------------------------------- 5. 环境变量

_ENV_CALL = re.compile(
    r'(?:os\.environ\.get|os\.getenv|_env|_env_int|_env_float)\(\s*"([A-Z0-9_]+)"'
)


def check_env(rep: Report) -> None:
    rep.section("环境变量：代码读的键 vs .env.example")
    used: dict[str, str] = {}
    for py in _py_files("src", "scripts"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            for m in _ENV_CALL.finditer(line):
                used.setdefault(m.group(1), f"{_rel(py)}:{i}")

    example = _REPO_ROOT / ".env.example"
    declared = set(re.findall(r"^([A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M))

    bad = 0
    for key in sorted(used):
        if key not in declared:
            bad += 1
            rep.bad(f"{key} 代码里读（{used[key]}），.env.example 没有")
    compose = (_REPO_ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8")
    for key in sorted(set(re.findall(r"\$\{([A-Z0-9_]+)", compose))):
        if key not in declared:
            bad += 1
            rep.bad(f"{key} compose.yaml 引用，.env.example 没有")
    unread = sorted(declared - set(used))
    if unread:
        rep.note(f".env.example 里代码不读的键（应为 compose 专用）: {', '.join(unread)}")
    if not bad:
        rep.ok(f"{len(used)} 个代码读的键 + compose 引用的键全部在 .env.example 里")


# --------------------------------------------------------------------------- 6. 依赖

#: 发行包名 → import 时的顶层模块名（两者不一致的才登记）。
_DIST_TO_MODULE = {
    "mysql-connector-python": "mysql",
    "apache-flink": "pyflink",
    "kafka-python": "kafka",
    "pyyaml": "yaml",
    "pytest-cov": "pytest_cov",
}
#: 只在命令行用、永远不会被 import 的开发工具。
_TOOL_ONLY = {"ruff", "mypy", "pytest-cov"}


def check_deps(rep: Report) -> None:
    rep.section("依赖：pyproject 声明 vs src/ 实际 import（双向）")
    std = set(sys.stdlib_module_names)
    local = {"adas_lakehouse"}

    imported: dict[str, str] = {}
    for py in _py_files("src", "scripts", "tests"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            mods: list[str] = []
            if isinstance(n, ast.Import):
                mods = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
                mods = [n.module.split(".")[0]]
            for m in mods:
                if m not in std and m not in local:
                    imported.setdefault(m, f"{_rel(py)}:{n.lineno}")

    pp = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    proj = pp["project"]
    norm = lambda r: re.split(r"[<>=!~\[ ]", r.strip())[0].lower()  # noqa: E731
    groups = {
        g: {norm(r) for r in reqs} for g, reqs in proj.get("optional-dependencies", {}).items()
    }
    all_declared = {norm(r) for r in proj.get("dependencies", [])}
    for s in groups.values():
        all_declared |= s
    declared_modules = {_DIST_TO_MODULE.get(d, d) for d in all_declared}

    # 本仓自己的模块不是第三方依赖：scripts/ 下的同名 .py、以及 src/ 下的包，
    # 出现在 import 里是正常的本地引用，不该要求写进 pyproject.dependencies。
    local_modules = {p.stem for p in (_REPO_ROOT / "scripts").glob("*.py")}
    local_modules |= {p.name for p in (_REPO_ROOT / "src").iterdir() if p.is_dir()}
    local_modules |= {p.stem for p in (_REPO_ROOT / "src").glob("*.py")}

    bad = 0
    for mod, where in sorted(imported.items()):
        if mod in local_modules:
            continue  # 本地模块，非第三方依赖
        if mod not in declared_modules:
            bad += 1
            rep.bad(f"import {mod}（{where}）但 pyproject 未声明")
    for dist in sorted(all_declared):
        if dist in _TOOL_ONLY:
            continue
        if _DIST_TO_MODULE.get(dist, dist) not in imported:
            bad += 1
            rep.bad(f"pyproject 声明了 {dist}，但 src/scripts/tests 里没有任何 import")

    # `all` 分组的自述是「一键装齐全部运行时可选依赖（不含 dev）」——就该名副其实
    runtime = set()
    for g, s in groups.items():
        if g not in {"dev", "all"}:
            runtime |= s
    missing_in_all = sorted(runtime - groups.get("all", set()))
    if missing_in_all:
        bad += 1
        rep.bad(f"optional-dependencies.all 漏了运行时分组里的 {missing_in_all}")
    if not bad:
        rep.ok(f"{len(imported)} 个第三方模块与 {len(all_declared)} 条声明双向对得上")


# --------------------------------------------------------------------------- main

_CHECKS = {
    "tables": check_tables,
    "columns": check_columns,
    "specs": check_specs,
    "sql": check_sql,
    "env": check_env,
    "deps": check_deps,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", choices=sorted(_CHECKS), action="append", help="只跑指定检查，可重复")
    ap.add_argument("--quiet", action="store_true", help="只打小结")
    args = ap.parse_args(argv)

    rep = Report(quiet=args.quiet)
    for name in args.only or list(_CHECKS):
        _CHECKS[name](rep)

    print()
    if rep.problems:
        print(f"跨模块一致性体检：发现 {len(rep.problems)} 处不一致")
        if args.quiet:
            for p in rep.problems:
                print(f"  ✗ {p}")
        return 1
    print("跨模块一致性体检：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
