from __future__ import annotations

import re
import uuid
from datetime import datetime

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.entities.verified_query import VerifiedQueryRevision
from app.models.mysql.verified_query_mysql import VerifiedQueryRevisionMySQL
from app.repositories.dialect import get_dialect_strategy
from app.repositories.mysql.verified_query_repository import (
    VerifiedQueryRepository,
    revision_to_entity,
)

_CASE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PARAMETER_TYPES = {
    "boolean",
    "date",
    "datetime",
    "integer",
    "number",
    "string",
}
_SOURCES = {"feedback", "manual", "seed", "trace"}
_ASSERTION_KINDS = {"equals", "max", "min", "not_null", "row_count", "unique"}


class VerifiedQueryService:
    def __init__(self, repository: VerifiedQueryRepository):
        self.repository = repository

    async def create_revision(
        self,
        *,
        case_key: str,
        question: str,
        dialect: str,
        sql_template: str,
        parameter_schema: list[dict],
        expected_fields: list[str],
        expected_metrics: list[str],
        assertions: list[dict],
        domain: str,
        datasource: str,
        source_trace_id: str | None,
        source: str,
        created_by: str | None,
        commit: bool = True,
    ) -> VerifiedQueryRevision:
        normalized_dialect = _validate_revision(
            case_key=case_key,
            question=question,
            dialect=dialect,
            sql_template=sql_template,
            parameter_schema=parameter_schema,
            expected_fields=expected_fields,
            expected_metrics=expected_metrics,
            assertions=assertions,
            domain=domain,
            datasource=datasource,
            source=source,
        )
        revision = await self.repository.next_revision(
            case_key=case_key,
            domain=domain,
            datasource=datasource,
        )
        row = VerifiedQueryRevisionMySQL(
            id=str(uuid.uuid4()),
            case_key=case_key,
            revision=revision,
            domain=domain.strip(),
            datasource=datasource.strip(),
            question=question.strip(),
            dialect=normalized_dialect,
            sql_template=sql_template.strip(),
            parameter_schema=[dict(item) for item in parameter_schema],
            expected_fields=_dedupe(expected_fields),
            expected_metrics=_dedupe(expected_metrics),
            assertions=[dict(item) for item in assertions],
            source_trace_id=source_trace_id,
            source=source,
            lifecycle="candidate",
            created_by=created_by,
        )
        await self.repository.add_revision(row)
        if commit:
            await self.repository.session.commit()
        return revision_to_entity(row)

    async def review(
        self, revision_id: str, *, reviewer_id: str, approved: bool
    ) -> VerifiedQueryRevision:
        row = await self.repository.get_revision(revision_id)
        if row is None:
            raise LookupError("验证查询修订不存在")
        if row.lifecycle not in {"candidate", "reviewed"}:
            raise ValueError("当前生命周期不允许审核")
        row.lifecycle = "reviewed" if approved else "disabled"
        row.reviewer_id = reviewer_id
        row.reviewed_at = datetime.now()
        await self.repository.session.commit()
        return revision_to_entity(row)


def _validate_revision(
    *,
    case_key: str,
    question: str,
    dialect: str,
    sql_template: str,
    parameter_schema: list[dict],
    expected_fields: list[str],
    expected_metrics: list[str],
    assertions: list[dict],
    domain: str,
    datasource: str,
    source: str,
) -> str:
    if not _CASE_KEY_PATTERN.fullmatch(case_key):
        raise ValueError("用例编码必须使用小写字母、数字和下划线")
    if not question.strip() or len(question) > 500:
        raise ValueError("验证问题不能为空且不能超过 500 个字符")
    if not domain.strip() or not datasource.strip():
        raise ValueError("验证查询必须指定业务域和数据源")
    if source not in _SOURCES:
        raise ValueError("验证查询来源无效")
    try:
        strategy = get_dialect_strategy(dialect)
    except ValueError as exc:
        raise ValueError("验证查询方言无效") from exc
    expression = _parse_select_template(sql_template, strategy.sqlglot_dialect)
    _validate_parameter_schema(expression, parameter_schema)
    if not expected_fields or any(not str(item).strip() for item in expected_fields):
        raise ValueError("验证查询必须声明非空预期字段")
    if any(not str(item).strip() for item in expected_metrics):
        raise ValueError("预期指标标识不能为空")
    _validate_assertions(assertions, set(expected_fields))
    return strategy.name


def _parse_select_template(sql_template: str, dialect: str) -> exp.Select:
    try:
        statements = sqlglot.parse(sql_template, read=dialect)
    except ParseError as exc:
        raise ValueError("验证 SQL 模板无法解析") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("验证 SQL 模板只允许单条 SELECT")
    expression = statements[0]
    for literal in expression.find_all(exp.Literal):
        if _literal_is_filter_value(literal):
            raise ValueError("验证 SQL 筛选值必须使用命名参数")
    return expression


def _validate_parameter_schema(expression: exp.Select, parameter_schema: list[dict]) -> None:
    expected_keys = {"name", "type", "required"}
    names: list[str] = []
    for item in parameter_schema:
        if set(item) != expected_keys:
            raise ValueError("参数定义只允许 name、type、required")
        name = str(item.get("name", ""))
        if not re.fullmatch(r"p[1-9][0-9]*", name):
            raise ValueError("参数名必须使用 p1、p2 格式")
        if item.get("type") not in _PARAMETER_TYPES:
            raise ValueError("参数类型无效")
        if not isinstance(item.get("required"), bool):
            raise ValueError("参数 required 必须为布尔值")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("参数名不能重复")
    placeholders = [placeholder.name for placeholder in expression.find_all(exp.Placeholder)]
    if set(placeholders) != set(names) or len(placeholders) != len(names):
        raise ValueError("SQL 命名参数与参数定义不一致")


def _validate_assertions(assertions: list[dict], expected_fields: set[str]) -> None:
    for assertion in assertions:
        if not isinstance(assertion, dict) or assertion.get("kind") not in _ASSERTION_KINDS:
            raise ValueError("验证断言类型无效")
        if set(assertion) - {"field", "kind", "operator", "value"}:
            raise ValueError("验证断言包含未授权字段")
        kind = assertion["kind"]
        if kind == "row_count":
            if assertion.get("operator") not in {"eq", "gte", "lte"} or not isinstance(
                assertion.get("value"), int
            ):
                raise ValueError("row_count 断言必须提供整数值和合法操作符")
            continue
        field = str(assertion.get("field", ""))
        if field not in expected_fields:
            raise ValueError("验证断言引用了未声明字段")
        if kind in {"equals", "max", "min"} and "value" not in assertion:
            raise ValueError("数值断言缺少 value")


def _literal_is_filter_value(literal: exp.Literal) -> bool:
    current = literal.parent
    while current is not None:
        if isinstance(
            current,
            exp.Between
            | exp.EQ
            | exp.GT
            | exp.GTE
            | exp.In
            | exp.Like
            | exp.LT
            | exp.LTE
            | exp.NEQ,
        ):
            return True
        if isinstance(current, exp.Select | exp.Func | exp.Limit):
            return False
        current = current.parent
    return False


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values))
