"""Build redacted, parameterized SQL templates without persisting runtime values."""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, traverse_scope


@dataclass(frozen=True)
class ParameterizedSQLTemplate:
    sql: str
    parameter_types: tuple[str, ...]


def build_parameterized_sql_template(
    sql: str,
    *,
    row_level_scope: list[dict] | None = None,
    dialect: str = "mysql",
) -> ParameterizedSQLTemplate:
    """Strip exact RLS predicates and replace filter literals with named placeholders."""

    if not sql or not sql.strip():
        return ParameterizedSQLTemplate(sql="", parameter_types=())
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except ParseError:
        return ParameterizedSQLTemplate(sql="/* redacted: unparseable SQL */", parameter_types=())
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return ParameterizedSQLTemplate(sql="/* redacted: unsupported SQL */", parameter_types=())

    expression = statements[0]
    if row_level_scope:
        _strip_row_level_predicates(expression, row_level_scope)

    parameter_types: list[str] = []
    parameter_index = 0
    for literal in list(expression.find_all(exp.Literal)):
        if not _is_filter_literal(literal):
            continue
        parameter_index += 1
        parameter_types.append(_literal_type(literal))
        literal.replace(exp.Placeholder(this=f"p{parameter_index}"))
    return ParameterizedSQLTemplate(
        sql=expression.sql(dialect=dialect, pretty=False),
        parameter_types=tuple(parameter_types),
    )


def redact_feedback_text(value: str, *, max_length: int = 1000) -> str:
    """Redact common identifiers and literal values from feedback metadata."""

    text = str(value or "")
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<email>", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "<phone>", text)
    text = re.sub(r"(?<!\d)\d{6,}(?!\d)", "<id>", text)
    text = re.sub(r"'[^']*'", "'<value>'", text)
    text = re.sub(r'"[^"]*"', '"<value>"', text)
    return text[:max_length]


def _strip_row_level_predicates(expression: exp.Select, row_level_scope: list[dict]) -> None:
    constraints = [
        (
            str(item.get("table", "")).lower(),
            str(item.get("column", "")).lower(),
            str(item.get("value", "")),
        )
        for item in row_level_scope
        if item.get("column") and item.get("value") is not None
    ]
    for scope in traverse_scope(expression):
        where = scope.expression.args.get("where")
        if where is None:
            continue
        aliases = _scope_physical_aliases(scope)
        cleaned = _remove_exact_rls_conjunct(where.this, constraints, aliases)
        if cleaned is None:
            scope.expression.set("where", None)
        else:
            where.set("this", cleaned)


def _scope_physical_aliases(scope: Scope) -> dict[str, str]:
    return {
        alias.lower(): source.name.lower()
        for alias, (_, source) in scope.selected_sources.items()
        if isinstance(source, exp.Table)
    }


def _remove_exact_rls_conjunct(
    node: exp.Expression,
    constraints: list[tuple[str, str, str]],
    aliases: dict[str, str],
) -> exp.Expression | None:
    if isinstance(node, exp.Paren):
        cleaned = _remove_exact_rls_conjunct(node.this, constraints, aliases)
        if cleaned is None:
            return None
        node.set("this", cleaned)
        return node
    if isinstance(node, exp.And):
        left = _remove_exact_rls_conjunct(node.left, constraints, aliases)  # type: ignore[arg-type]
        right = _remove_exact_rls_conjunct(node.right, constraints, aliases)  # type: ignore[arg-type]
        if left is None:
            return right
        if right is None:
            return left
        node.set("this", left)
        node.set("expression", right)
        return node
    if _matches_exact_rls_equality(node, constraints, aliases):
        return None
    return node


def _matches_exact_rls_equality(
    node: exp.Expression,
    constraints: list[tuple[str, str, str]],
    aliases: dict[str, str],
) -> bool:
    if not isinstance(node, exp.EQ):
        return False
    for column, literal in ((node.left, node.right), (node.right, node.left)):
        if not isinstance(column, exp.Column) or not isinstance(literal, exp.Literal):
            continue
        qualifier = column.table.lower()
        physical_table = aliases.get(qualifier, qualifier)
        for table, expected_column, expected_value in constraints:
            if table and table != physical_table:
                continue
            if column.name.lower() == expected_column and str(literal.this) == expected_value:
                return True
    return False


_FILTER_ANCESTORS = (
    exp.Between,
    exp.EQ,
    exp.GT,
    exp.GTE,
    exp.In,
    exp.Like,
    exp.LT,
    exp.LTE,
    exp.NEQ,
)


def _is_filter_literal(literal: exp.Literal) -> bool:
    current = literal.parent
    while current is not None:
        if isinstance(current, _FILTER_ANCESTORS):
            return True
        if isinstance(current, exp.Select | exp.Func | exp.Limit):
            return False
        current = current.parent
    return False


def _literal_type(literal: exp.Literal) -> str:
    if literal.is_string:
        return "string"
    raw = str(literal.this)
    return "integer" if raw.lstrip("-").isdigit() else "number"
