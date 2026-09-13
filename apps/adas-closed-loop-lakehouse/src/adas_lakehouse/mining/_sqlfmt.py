"""SQL 片段格式化与转义的小工具。

规则配置来自控制面 MySQL，而 MySQL 里的内容由「工程师写 SQL、业务同学拖配置」
两条路径产生（[S3-04] 一、双模式表达）。可视化配置编译成 SQL 时，字段名与字面量
全部要走这里的校验与转义——业务同学在输入框里打的字，不能直接拼进 WHERE 子句。

⚠️ 原文未明确，本项目设计：原文只说两种表达「产出的规则等价」，未涉及编译期的
转义与标识符白名单。这是本项目补的工程保护。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

__all__ = [
    "SqlRenderError",
    "ident",
    "qualified_ident",
    "literal",
    "literal_list",
    "join_predicates",
    "indent_sql",
]

#: 合法标识符：字母开头，后接字母/数字/下划线。故意不允许点号——
#: 需要限定表别名时用 qualified_ident()，逐段校验。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 标识符长度上限。⚠️ 原文未明确，本项目设计：对齐常见湖仓引擎的列名长度限制。
_IDENT_MAX_LEN = 128


class SqlRenderError(ValueError):
    """规则编译成 SQL 时的非法输入。

    编译期就抛，不要拖到执行期——一条坏规则如果被提交到 Spark/Flink 才失败，
    整批任务的 4 小时 SLA（constants.BATCH_SLA_HOURS）就白白烧掉了。
    """


def ident(name: str) -> str:
    """校验并渲染一个反引号标识符。

    Args:
        name: 列名或表名的单段。

    Returns:
        形如 ``` `weather` ``` 的字符串。

    Raises:
        SqlRenderError: 名字为空、超长或含非法字符。
    """
    if not isinstance(name, str) or not name:
        raise SqlRenderError("标识符不能为空")
    if len(name) > _IDENT_MAX_LEN:
        raise SqlRenderError(f"标识符过长（>{_IDENT_MAX_LEN}）: {name!r}")
    if not _IDENT_RE.match(name):
        raise SqlRenderError(f"非法标识符: {name!r}（只允许字母/数字/下划线，且不能以数字开头）")
    return f"`{name}`"


def qualified_ident(name: str) -> str:
    """渲染可带别名前缀的标识符，如 ``clip.weather`` -> ``` `clip`.`weather` ```。"""
    parts = name.split(".")
    if len(parts) > 3:
        raise SqlRenderError(f"标识符段数过多: {name!r}")
    return ".".join(ident(p) for p in parts)


def literal(value: Any) -> str:
    """把 Python 值渲染成 SQL 字面量。

    支持 None / bool / int / float / str / date / datetime。字符串里的单引号与
    反斜杠做转义；其余类型一律拒绝——宁可编译失败，也不要把未知对象 str() 后拼进 SQL。

    Raises:
        SqlRenderError: 不支持的类型，或浮点数为 NaN/Inf。
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SqlRenderError(f"浮点字面量非法: {value!r}")
        return repr(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "''")
        return f"'{escaped}'"
    raise SqlRenderError(f"不支持的字面量类型: {type(value).__name__}")


def literal_list(values: object) -> str:
    """渲染 IN 列表，如 ``('rain', 'heavy_rain')``。空列表会被拒绝。"""
    items = list(values)  # type: ignore[call-overload]
    if not items:
        raise SqlRenderError("IN 列表不能为空")
    return "(" + ", ".join(literal(v) for v in items) + ")"


def join_predicates(predicates: list[str], operator: str) -> str:
    """用 AND / OR 连接谓词并加外层括号。空列表返回恒真谓词。"""
    op = operator.strip().upper()
    if op not in ("AND", "OR"):
        raise SqlRenderError(f"逻辑运算符只能是 AND/OR，收到 {operator!r}")
    kept = [p for p in predicates if p and p.strip()]
    if not kept:
        return "TRUE"
    if len(kept) == 1:
        return kept[0]
    return "(" + f"\n  {op} ".join(kept) + ")"


def indent_sql(sql: str, spaces: int = 2) -> str:
    """给多行 SQL 片段整体缩进，纯排版用。"""
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in sql.splitlines())
