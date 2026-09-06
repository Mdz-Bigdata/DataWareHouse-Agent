from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import sqlglot
from sqlglot import exp

from app.entities.verified_query import QuerySetVersion, VerifiedQueryExample
from app.repositories.dialect import get_dialect_strategy
from app.repositories.mysql.verified_query_repository import (
    QuerySetRepository,
    query_set_to_entity,
)
from app.repositories.qdrant.verified_query_qdrant_repository import (
    VerifiedQueryQdrantRepository,
)

_TRAILING_PUNCTUATION = re.compile(r"[\s。！？!?；;]+$")


@dataclass(frozen=True)
class QuerySetMatchResult:
    query_set: QuerySetVersion | None
    semantic_release_id: str | None = None
    semantic_release_version: int | None = None
    exact_example: VerifiedQueryExample | None = None
    exact_sql: str | None = None
    exact_error: str | None = None
    semantic_examples: tuple[VerifiedQueryExample, ...] = ()


class QuerySetMatchService:
    """Resolve only the latest published Query Set in the current policy scope."""

    def __init__(
        self,
        query_set_repository: QuerySetRepository,
        vector_repository: VerifiedQueryQdrantRepository,
        embedding_client,
    ):
        self.query_set_repository = query_set_repository
        self.vector_repository = vector_repository
        self.embedding_client = embedding_client

    async def match(
        self,
        query: str,
        *,
        parameters: dict[str, Any] | None,
        domain: str,
        datasource: str,
        dialect: str,
    ) -> QuerySetMatchResult:
        strategy = get_dialect_strategy(dialect)
        effective_resolver = getattr(
            self.query_set_repository,
            "get_effective_published",
            None,
        )
        if effective_resolver is None:
            row = await self.query_set_repository.get_latest_published(
                domain=domain,
                datasource=datasource,
            )
            release = None
        else:
            row, release = await effective_resolver(
                domain=domain,
                datasource=datasource,
            )
        if row is None:
            return QuerySetMatchResult(
                query_set=None,
                semantic_release_id=release.id if release is not None else None,
                semantic_release_version=(
                    release.version if release is not None else None
                ),
            )
        query_set = query_set_to_entity(row)
        examples = query_set_examples(query_set)
        scoped = [item for item in examples if item.dialect == strategy.name]

        normalized_query = normalize_verified_question(query)
        exact_candidates = [
            item
            for item in scoped
            if normalize_verified_question(item.question) == normalized_query
        ]
        exact_example = exact_candidates[0] if len(exact_candidates) == 1 else None
        exact_sql: str | None = None
        exact_error: str | None = None
        if exact_example is not None:
            try:
                exact_sql = bind_query_template(
                    exact_example.sql_template,
                    exact_example.parameter_schema,
                    parameters or {},
                    dialect=strategy.sqlglot_dialect,
                )
            except ValueError as exc:
                exact_error = str(exc)
        if exact_sql is not None:
            return QuerySetMatchResult(
                query_set=query_set,
                semantic_release_id=release.id if release is not None else None,
                semantic_release_version=(
                    release.version if release is not None else None
                ),
                exact_example=exact_example,
                exact_sql=exact_sql,
            )

        embedding = await self.embedding_client.aembed_query(query)
        semantic_examples = await self.vector_repository.search(
            embedding,
            query_set_id=query_set.id,
            domain=domain,
            datasource=datasource,
            dialect=strategy.name,
        )
        if exact_example is not None:
            semantic_examples = [
                item for item in semantic_examples if item.revision_id != exact_example.revision_id
            ]
        return QuerySetMatchResult(
            query_set=query_set,
            semantic_release_id=release.id if release is not None else None,
            semantic_release_version=(
                release.version if release is not None else None
            ),
            exact_example=exact_example,
            exact_sql=exact_sql,
            exact_error=exact_error,
            semantic_examples=tuple(semantic_examples),
        )


def query_set_examples(query_set: QuerySetVersion) -> list[VerifiedQueryExample]:
    examples: list[VerifiedQueryExample] = []
    for item in query_set.manifest:
        examples.append(
            VerifiedQueryExample(
                query_set_id=query_set.id,
                query_set_version=query_set.version,
                query_set_hash=query_set.content_hash,
                domain=query_set.domain,
                datasource=query_set.datasource,
                revision_id=str(item["revision_id"]),
                case_key=str(item["case_key"]),
                question=str(item["question"]),
                dialect=get_dialect_strategy(str(item["dialect"])).name,
                sql_template=str(item["sql_template"]),
                parameter_schema=list(item.get("parameter_schema") or []),
                expected_fields=list(item.get("expected_fields") or []),
                expected_metrics=list(item.get("expected_metrics") or []),
            )
        )
    return examples


def normalize_verified_question(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = _TRAILING_PUNCTUATION.sub("", normalized)
    return re.sub(r"\s+", " ", normalized)


def bind_query_template(
    sql_template: str,
    parameter_schema: list[dict],
    parameters: dict[str, Any],
    *,
    dialect: str,
) -> str:
    definitions = {str(item["name"]): item for item in parameter_schema}
    unknown = sorted(set(parameters) - set(definitions))
    if unknown:
        raise ValueError(f"可信案例包含未声明参数：{', '.join(unknown)}")

    typed_values: dict[str, Any] = {}
    for name, definition in definitions.items():
        if name not in parameters:
            if definition.get("required", True):
                raise ValueError(f"可信案例缺少必填参数：{name}")
            typed_values[name] = None
            continue
        if parameters[name] is None and not definition.get("required", True):
            typed_values[name] = None
            continue
        typed_values[name] = _coerce_parameter(
            name,
            str(definition.get("type")),
            parameters[name],
        )

    expression = sqlglot.parse_one(sql_template, read=dialect)
    for placeholder in list(expression.find_all(exp.Placeholder)):
        name = placeholder.name
        if name not in typed_values:
            raise ValueError(f"可信案例参数定义不完整：{name}")
        placeholder.replace(_parameter_expression(typed_values[name]))
    if expression.find(exp.Placeholder) is not None:
        raise ValueError("可信案例仍包含未绑定参数")
    return expression.sql(dialect=dialect)


def _coerce_parameter(name: str, parameter_type: str, value: Any) -> Any:
    if value is None:
        raise ValueError(f"可信案例参数 {name} 不能为空")
    if parameter_type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"可信案例参数 {name} 必须是 boolean")
        return value
    if parameter_type == "integer":
        if type(value) is not int:
            raise ValueError(f"可信案例参数 {name} 必须是 integer")
        return value
    if parameter_type == "number":
        if type(value) not in {int, float} or not math.isfinite(float(value)):
            raise ValueError(f"可信案例参数 {name} 必须是有限 number")
        return value
    if parameter_type == "string":
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError(f"可信案例参数 {name} 必须是长度不超过 2000 的 string")
        return value
    if parameter_type == "date":
        if not isinstance(value, str):
            raise ValueError(f"可信案例参数 {name} 必须是 ISO date 字符串")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"可信案例参数 {name} 必须是 ISO date 字符串") from exc
    if parameter_type == "datetime":
        if not isinstance(value, str):
            raise ValueError(f"可信案例参数 {name} 必须是 ISO datetime 字符串")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"可信案例参数 {name} 必须是 ISO datetime 字符串") from exc
        return parsed.isoformat(sep=" ")
    raise ValueError(f"可信案例参数 {name} 类型不受支持")


def _parameter_expression(value: Any) -> exp.Expression:
    if isinstance(value, str):
        return exp.Literal.string(value)
    return exp.convert(value)
