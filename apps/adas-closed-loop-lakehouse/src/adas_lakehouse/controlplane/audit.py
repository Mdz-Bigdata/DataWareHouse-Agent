"""控制面重建自检的**模式层**实现：拿 a4 的硬判据去审真正会落盘的东西。

背景 —— 为什么需要这一层
======================================================================

原文第四章给的健康判据是一句可证伪的话：

    「把平台的数据库清空重建，业务数据是否完好？」

:meth:`~.scheduler.ControlPlane.rebuild_check` 原先只做一件事::

    leaked = [a for a in CONTROL_PLANE_ASSETS if asset_plane(a) is not Plane.CONTROL]

而 :func:`~.contracts.asset_plane` 的第一行就是「``asset in CONTROL_PLANE_ASSETS``
→ :attr:`~.contracts.Plane.CONTROL`」。两者互为定义，``leaked`` **恒为空**——
这条断言对任何仓库状态都成立，因此它证明不了任何事。真正会把主数据悄悄留在控制面的
地方（MySQL 建表语句里多一列 ``image_embedding``、多一张 ``cp_clip_cache``），
它一个都看不见。

本模块把判据下沉到**持久化模式**这一层：清库重建之后还能活下来的，只有
:data:`~.store.CONTROL_PLANE_MYSQL_DDL` 定义的那几张表；主数据完不完好，取决于
数据面资产是不是真的落在 :mod:`adas_lakehouse.catalog.registry` 登记的湖仓表上。
两头都是可枚举的事实，于是判据就变成可证伪的了。

四条审计规则
======================================================================

1. **表清单封闭**：控制面 DDL 里的每一张表都必须在 :data:`CONTROL_PLANE_TABLES`
   里登记归属。有人新加一张表而不登记，自检立刻失败——「顺手建一套数据副本」的第一步
   恰恰就是新加一张表。
2. **列名不得命中主数据黑名单**：复用
   :data:`~.contracts._MASTER_DATA_FIELD_HINTS`（运行时守卫用的同一份），
   指针型列（``*_id`` / ``*_json`` / ``*_key`` / ``*_handle``）显式放行。
3. **不得影射湖仓表**：控制面表名去掉 ``cp_`` 前缀后不得等于任何一张登记在册的湖仓表名，
   也不得以四段式前缀（``ods_`` / ``dwd_`` / ``dws_`` / ``ads_``）开头。
4. **数据面资产必须锚定到真实湖仓表**：:data:`~.contracts.DATA_PLANE_ASSETS`
   的每一项都要指得出一张 registry 里存在的表，否则「业务数据完好」是句空话——
   数据都不知道在哪，谈何完好。

⚠️ 原文未明确，本项目设计：以上四条规则的**具体形式**是本项目补的。原文只给了判据
这一句话，没说怎么执行。选这四条的理由是它们全部只依赖仓库里已有的事实
（DDL 常量 + catalog registry），不需要连库，因此能进 CI 当断言跑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from ..domains import Layer
from .contracts import (
    CONTROL_PLANE_ASSETS,
    DATA_PLANE_ASSETS,
    master_data_field_hints,
    pointer_field_whitelist,
)
from .store import CONTROL_PLANE_MYSQL_DDL

__all__ = [
    "CONTROL_PLANE_TABLES",
    "CONTROL_PLANE_TABLE_PREFIX",
    "SchemaFinding",
    "SchemaAudit",
    "parse_control_plane_schema",
    "audit_control_plane_schema",
]

#: 控制面表名前缀。与湖仓四段式命名刻意分开（见 store.CONTROL_PLANE_MYSQL_DDL 的说明）。
CONTROL_PLANE_TABLE_PREFIX: Final[str] = "cp_"

#: 控制面允许存在的表 -> 它承载的控制面资产键（:data:`~.contracts.CONTROL_PLANE_ASSETS` 的键）。
#:
#: ⚠️ 原文未明确，本项目设计：原文只说 MySQL 存「规则配置 / 任务配置与执行状态 /
#: 审核流状态」三类，没有逐表映射。这份映射把 store 里那四张表逐一挂到资产键上——
#: 有了它，「控制面持有什么」不再是一份可以随口增删的字符串清单，而是与建表语句对齐的契约。
CONTROL_PLANE_TABLES: Final[dict[str, tuple[str, ...]]] = {
    "cp_rule_config": ("rule_config",),
    "cp_task": ("task_config", "review_state", "idempotency_key"),
    "cp_task_event": ("task_config",),
    "cp_audit_log": ("audit_event",),
}

#: 建表语句头：``CREATE TABLE [IF NOT EXISTS] `name` (``
_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`(?P<name>[^`]+)`\s*\(", re.IGNORECASE
)

#: 列定义行：以反引号列名开头，后面跟类型。PRIMARY KEY / KEY / UNIQUE KEY 不是列。
_COLUMN_RE = re.compile(r"^\s*`(?P<col>[^`]+)`\s+(?P<type>[A-Za-z]+)")

#: 索引/约束行的引导词——这些行里的反引号是列引用，不是列定义。
_NON_COLUMN_PREFIXES: Final[tuple[str, ...]] = (
    "primary key",
    "key ",
    "unique key",
    "index ",
    "constraint",
    "foreign key",
    "fulltext",
)

#: 指针型列后缀：只引用主数据，不搬主数据本体。
#:
#: ⚠️ 原文未明确，本项目设计：黑名单是按子串匹配的，``artifacts_json`` 里含 ``artifact``
#: 这类列必须显式放行，否则会误杀「只存 ID 的指针列」——而指针列恰恰是本设计要的东西。
_POINTER_SUFFIXES: Final[tuple[str, ...]] = (
    "_id",
    "_ids",
    "_json",
    "_key",
    "_handle",
    "_code",
    "_name",
    "_version",
    "_table",
    "_column",
    "_path",
    "_uri",
    "_url",
)


@dataclass(frozen=True, slots=True)
class SchemaFinding:
    """一条审计发现。``rule`` 是四条规则之一的短名，``detail`` 可直接贴进评审记录。"""

    rule: str
    table: str
    column: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "table": self.table,
            "column": self.column,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SchemaAudit:
    """模式层自检结论。

    :param passed: 四条规则全过才为 True
    :param tables: 解析出的控制面表 -> 列名元组
    :param findings: 违规明细，空表示干净
    :param data_plane_anchors: 数据面资产键 -> 它锚定的湖仓表名（规则 4 的证据）
    """

    passed: bool
    tables: dict[str, tuple[str, ...]]
    findings: tuple[SchemaFinding, ...] = ()
    data_plane_anchors: dict[str, str] = field(default_factory=dict)

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def column_count(self) -> int:
        return sum(len(c) for c in self.tables.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "table_count": self.table_count,
            "column_count": self.column_count,
            "tables": {t: list(c) for t, c in self.tables.items()},
            "findings": [f.as_dict() for f in self.findings],
            "data_plane_anchors": dict(self.data_plane_anchors),
        }


def parse_control_plane_schema(ddl: str | None = None) -> dict[str, tuple[str, ...]]:
    """把控制面建表 DDL 解析成 ``{表名: (列名, ...)}``。

    只认 :data:`~.store.CONTROL_PLANE_MYSQL_DDL` 这一种写法（本项目自己渲染的，
    格式固定），不是通用 SQL 解析器：索引与约束行按 :data:`_NON_COLUMN_PREFIXES` 剔除。

    :param ddl: 待解析的 DDL，缺省取 :data:`~.store.CONTROL_PLANE_MYSQL_DDL`
    """
    text = CONTROL_PLANE_MYSQL_DDL if ddl is None else ddl
    out: dict[str, tuple[str, ...]] = {}
    for match in _CREATE_RE.finditer(text):
        name = match.group("name")
        body = _table_body(text, match.end())
        cols: list[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            if stripped.lower().startswith(_NON_COLUMN_PREFIXES):
                continue
            col = _COLUMN_RE.match(line)
            if col:
                cols.append(col.group("col"))
        out[name] = tuple(cols)
    return out


def _table_body(text: str, start: int) -> str:
    """从建表语句的左括号之后取到配对的右括号为止。"""
    depth = 1
    i = start
    while i < len(text) and depth:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    return text[start : i - 1]


def _is_pointer_column(column: str) -> bool:
    lower = column.lower()
    if lower in pointer_field_whitelist():
        return True
    return lower.endswith(_POINTER_SUFFIXES)


def audit_control_plane_schema(
    ddl: str | None = None,
    *,
    lakehouse_tables: frozenset[str] | None = None,
) -> SchemaAudit:
    """对控制面持久化模式跑四条审计规则。

    :param ddl: 待审的建表语句，缺省取 :data:`~.store.CONTROL_PLANE_MYSQL_DDL`
    :param lakehouse_tables: 湖仓表名集合，缺省从 :mod:`~adas_lakehouse.catalog.registry`
        现取（延迟 import：catalog 是最底层契约，controlplane 不在模块顶层依赖它）
    """
    tables = parse_control_plane_schema(ddl)
    if lakehouse_tables is None:
        from ..catalog import registry

        lakehouse_tables = frozenset(t.name for t in registry.all_tables())

    findings: list[SchemaFinding] = []
    hints = master_data_field_hints()
    layer_prefixes = tuple(f"{layer.value}_" for layer in Layer)

    # 规则 1：表清单封闭
    for table in tables:
        if table not in CONTROL_PLANE_TABLES:
            findings.append(
                SchemaFinding(
                    "table_registered",
                    table,
                    "",
                    f"控制面新增了未登记的表 {table!r}；"
                    "新表必须先在 CONTROL_PLANE_TABLES 里声明它承载哪个控制面资产"
                    "——「平台顺手建一套数据副本」就是从多一张表开始的",
                )
            )
        else:
            unknown = [a for a in CONTROL_PLANE_TABLES[table] if a not in CONTROL_PLANE_ASSETS]
            if unknown:
                findings.append(
                    SchemaFinding(
                        "table_registered",
                        table,
                        "",
                        f"{table} 声称承载的资产 {unknown} 不在 CONTROL_PLANE_ASSETS 里",
                    )
                )

    # 规则 3：不得影射湖仓表
    for table in tables:
        bare = (
            table[len(CONTROL_PLANE_TABLE_PREFIX) :]
            if table.startswith(CONTROL_PLANE_TABLE_PREFIX)
            else table
        )
        if bare in lakehouse_tables or table in lakehouse_tables:
            findings.append(
                SchemaFinding(
                    "no_lakehouse_shadow",
                    table,
                    "",
                    f"{table} 与湖仓表 {bare!r} 同名——控制面影射了一张湖仓表，"
                    "这正是原文点名的口径分裂起点",
                )
            )
        if table.lower().startswith(layer_prefixes):
            findings.append(
                SchemaFinding(
                    "no_lakehouse_shadow",
                    table,
                    "",
                    f"{table} 用了湖仓四段式层级前缀；控制面表一律 "
                    f"{CONTROL_PLANE_TABLE_PREFIX!r} 前缀，两套命名不混用",
                )
            )

    # 规则 2：列名不得命中主数据黑名单
    for table, columns in tables.items():
        for column in columns:
            lower = column.lower()
            hit = next((h for h in hints if h in lower), None)
            if hit is None:
                continue
            if _is_pointer_column(column):
                continue
            findings.append(
                SchemaFinding(
                    "no_master_data_column",
                    table,
                    column,
                    f"{table}.{column} 命中主数据黑名单词 {hit!r} 且不是指针型列；"
                    "主数据本体必须留在 Paimon，控制面只能存 artifact_id 这类指针",
                )
            )

    # 规则 4：数据面资产锚定到真实湖仓表
    anchors: dict[str, str] = {}
    for asset, desc in DATA_PLANE_ASSETS.items():
        hit = next((t for t in sorted(lakehouse_tables) if t in desc), None)
        if hit:
            anchors[asset] = hit
    for asset in DATA_PLANE_ASSETS:
        if asset in anchors:
            continue
        findings.append(
            SchemaFinding(
                "data_plane_anchored",
                "",
                asset,
                f"数据面资产 {asset!r} 指不出任何一张 registry 登记的湖仓表："
                "清库后「业务数据完好」对这一类资产无从验证——"
                "请在 DATA_PLANE_ASSETS 的描述里写明它落在哪张表",
            )
        )

    return SchemaAudit(
        passed=not findings,
        tables=tables,
        findings=tuple(findings),
        data_plane_anchors=anchors,
    )
