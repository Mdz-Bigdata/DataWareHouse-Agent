"""Resolve one immutable, auditable access-policy context per query request."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.mysql.user_mysql import UserMySQL


class AccessPolicyError(ValueError):
    """Raised when a non-admin user does not have a usable access policy."""


class RowPredicateV1(BaseModel):
    """A resolved row predicate. Runtime variables never reach SQL generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str | None = None
    column: str = Field(min_length=1)
    operator: Literal["eq"] = "eq"
    value: str = Field(min_length=1)
    source_variable: str | None = None


class AccessPolicyContextV1(BaseModel):
    """Request-scoped authorization facts consumed by retrieval and SQL Guard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["access-policy/v1"] = "access-policy/v1"
    user_id: str
    role: Literal["admin", "user"]
    domain: str
    datasource: str
    table_acl: dict[str, tuple[str, ...]]
    row_predicates: tuple[RowPredicateV1, ...] = ()
    function_whitelist: tuple[str, ...]
    policy_version: str
    policy_hash: str
    admin_bypass: bool = False

    def row_level_scope(self) -> list[dict[str, str]]:
        """Return the existing SQL Guard input shape without exposing it to clients."""

        return [
            {
                **({"table": predicate.table} if predicate.table else {}),
                "column": predicate.column,
                "operator": predicate.operator,
                "value": predicate.value,
            }
            for predicate in self.row_predicates
        ]

    def public_metadata(self) -> dict[str, str | bool]:
        """Return safe policy metadata for SSE and trace inspection."""

        return {
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "policy_admin_bypass": self.admin_bypass,
            "policy_domain": self.domain,
            "policy_datasource": self.datasource,
        }


_LEGACY_FUNCTION_WHITELIST = (
    "ABS",
    "AVG",
    "CAST",
    "COALESCE",
    "CONCAT",
    "COUNT",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "DATE",
    "DATE_ADD",
    "DATE_FORMAT",
    "DATE_SUB",
    "DAY",
    "FLOOR",
    "IF",
    "IFNULL",
    "LOWER",
    "MAX",
    "MIN",
    "MONTH",
    "NULLIF",
    "QUARTER",
    "ROUND",
    "SUM",
    "TIMESTAMPDIFF",
    "TRIM",
    "UPPER",
    "WEEK",
    "YEAR",
)


def resolve_access_policy(
    user: UserMySQL,
    *,
    domain: str,
    datasource: str,
    now: datetime | None = None,
) -> AccessPolicyContextV1:
    """Build a fail-closed policy context from ``users.data_scope``.

    Administrators receive an explicit, hashed bypass context. Ordinary users must
    have either a structured v1 policy or a non-empty legacy constraint list.
    Missing, malformed, mismatched, or expired policies and variables are rejected.
    """

    current_time = _as_utc(now or datetime.now(UTC))
    if user.role == "admin":
        payload = {
            "schema_version": "access-policy/v1",
            "user_id": user.id,
            "role": "admin",
            "domain": domain,
            "datasource": datasource,
            "table_acl": {"*": ("*",)},
            "row_predicates": (),
            "function_whitelist": ("*",),
            "policy_version": "admin-bypass-v1",
            "admin_bypass": True,
        }
        return AccessPolicyContextV1(**payload, policy_hash=_policy_hash(payload))

    if user.role != "user":
        raise AccessPolicyError("用户角色无有效访问策略")
    raw = _load_policy_json(user.data_scope)
    if isinstance(raw, list):
        return _resolve_legacy_policy(
            user=user,
            raw=raw,
            domain=domain,
            datasource=datasource,
        )
    if not isinstance(raw, dict):
        raise AccessPolicyError("访问策略必须是 JSON 对象")
    return _resolve_structured_policy(
        user=user,
        raw=raw,
        domain=domain,
        datasource=datasource,
        now=current_time,
    )


def internal_access_policy(*, domain: str, datasource: str) -> AccessPolicyContextV1:
    """Create an explicit bypass for trusted jobs that do not run as an API user."""

    internal_user = UserMySQL(
        id="internal-system",
        username="internal-system",
        password_hash="",
        role="admin",
        must_change_password=False,
    )
    return resolve_access_policy(internal_user, domain=domain, datasource=datasource)


def _load_policy_json(data_scope: str | None) -> Any:
    if not data_scope or not data_scope.strip():
        raise AccessPolicyError("普通用户缺少访问策略")
    try:
        return json.loads(data_scope)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AccessPolicyError("访问策略 JSON 无效") from exc


def _resolve_legacy_policy(
    *,
    user: UserMySQL,
    raw: list[Any],
    domain: str,
    datasource: str,
) -> AccessPolicyContextV1:
    if not raw:
        raise AccessPolicyError("普通用户访问策略不能为空")
    predicates: list[RowPredicateV1] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AccessPolicyError("旧版访问策略约束无效")
        column = _required_text(item.get("column"), "旧版访问策略缺少列名")
        value = _required_text(item.get("value"), "旧版访问策略缺少约束值")
        predicates.append(RowPredicateV1(column=column, value=value))
    payload = {
        "schema_version": "access-policy/v1",
        "user_id": user.id,
        "role": "user",
        "domain": domain,
        "datasource": datasource,
        "table_acl": {"*": ("*",)},
        "row_predicates": tuple(predicates),
        "function_whitelist": _LEGACY_FUNCTION_WHITELIST,
        "policy_version": "legacy-row-scope-v1",
        "admin_bypass": False,
    }
    return AccessPolicyContextV1(**payload, policy_hash=_policy_hash(payload))


def _resolve_structured_policy(
    *,
    user: UserMySQL,
    raw: dict[str, Any],
    domain: str,
    datasource: str,
    now: datetime,
) -> AccessPolicyContextV1:
    policy_version = _required_text(raw.get("policy_version"), "访问策略缺少版本")
    configured_domain = _required_text(raw.get("domain"), "访问策略缺少业务域")
    configured_datasource = _required_text(raw.get("datasource"), "访问策略缺少数据源")
    if configured_domain != domain or configured_datasource != datasource:
        raise AccessPolicyError("访问策略与当前业务域或数据源不匹配")
    _reject_expired(raw.get("expires_at"), now, "访问策略已过期")

    table_acl = _parse_table_acl(raw.get("table_acl"))
    functions = _parse_function_whitelist(raw.get("function_whitelist"))
    variables = _parse_variables(raw.get("variables"), now)
    predicates, referenced_variables = _parse_row_predicates(raw.get("row_predicates"), variables)
    _validate_predicate_acl(predicates, table_acl)
    unknown_variables = set(variables) - referenced_variables
    if unknown_variables:
        raise AccessPolicyError("访问策略包含未使用变量")

    payload = {
        "schema_version": "access-policy/v1",
        "user_id": user.id,
        "role": "user",
        "domain": domain,
        "datasource": datasource,
        "table_acl": table_acl,
        "row_predicates": tuple(predicates),
        "function_whitelist": functions,
        "policy_version": policy_version,
        "admin_bypass": False,
    }
    return AccessPolicyContextV1(**payload, policy_hash=_policy_hash(payload))


def _parse_table_acl(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict) or not value:
        raise AccessPolicyError("访问策略缺少表字段授权")
    result: dict[str, tuple[str, ...]] = {}
    for table, columns in value.items():
        table_name = _required_text(table, "访问策略表名无效")
        if not isinstance(columns, list) or not columns:
            raise AccessPolicyError("访问策略字段授权不能为空")
        normalized = tuple(_required_text(column, "访问策略字段名无效") for column in columns)
        if table_name == "*" or "*" in normalized:
            raise AccessPolicyError("普通用户表字段授权不允许通配符")
        result[table_name] = normalized
    return result


def _validate_predicate_acl(
    predicates: list[RowPredicateV1], table_acl: dict[str, tuple[str, ...]]
) -> None:
    for predicate in predicates:
        if not predicate.table:
            raise AccessPolicyError("结构化行级约束必须指定表名")
        if predicate.table not in table_acl:
            raise AccessPolicyError("行级约束引用未授权表")
        if predicate.column not in table_acl[predicate.table]:
            raise AccessPolicyError("行级约束引用未授权字段")


def _parse_function_whitelist(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AccessPolicyError("访问策略缺少函数白名单")
    functions = tuple(
        sorted({_required_text(item, "访问策略函数名无效").upper() for item in value})
    )
    if "*" in functions:
        raise AccessPolicyError("普通用户函数白名单不允许通配符")
    return functions


def _parse_variables(value: Any, now: datetime) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AccessPolicyError("访问策略变量无效")
    result: dict[str, str] = {}
    for name, item in value.items():
        variable_name = _required_text(name, "访问策略变量名无效")
        if not isinstance(item, dict):
            raise AccessPolicyError("访问策略变量必须包含值和有效期")
        _reject_expired(item.get("expires_at"), now, f"访问策略变量 {variable_name} 已过期")
        if "expires_at" not in item:
            raise AccessPolicyError(f"访问策略变量 {variable_name} 缺少有效期")
        result[variable_name] = _required_text(
            item.get("value"), f"访问策略变量 {variable_name} 缺少值"
        )
    return result


def _parse_row_predicates(
    value: Any, variables: dict[str, str]
) -> tuple[list[RowPredicateV1], set[str]]:
    if value is None:
        return [], set()
    if not isinstance(value, list):
        raise AccessPolicyError("访问策略行级约束无效")
    predicates: list[RowPredicateV1] = []
    referenced_variables: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise AccessPolicyError("访问策略行级约束无效")
        operator = item.get("operator", "eq")
        if operator != "eq":
            raise AccessPolicyError("访问策略暂不支持该行级操作符")
        variable = item.get("variable")
        direct_value = item.get("value")
        if variable is not None:
            variable_name = _required_text(variable, "访问策略变量引用无效")
            if direct_value is not None or variable_name not in variables:
                raise AccessPolicyError("访问策略变量缺失或引用冲突")
            predicate_value = variables[variable_name]
            referenced_variables.add(variable_name)
        else:
            predicate_value = _required_text(direct_value, "访问策略行级约束缺少值")
            variable_name = None
        predicates.append(
            RowPredicateV1(
                table=_optional_text(item.get("table")),
                column=_required_text(item.get("column"), "访问策略行级约束缺少列名"),
                operator="eq",
                value=predicate_value,
                source_variable=variable_name,
            )
        )
    return predicates, referenced_variables


def _reject_expired(value: Any, now: datetime, message: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise AccessPolicyError("访问策略有效期无效")
    try:
        expires_at = _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise AccessPolicyError("访问策略有效期无效") from exc
    if expires_at <= now:
        raise AccessPolicyError(message)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_text(value: Any, message: str) -> str:
    if value is None:
        raise AccessPolicyError(message)
    text = str(value).strip()
    if not text:
        raise AccessPolicyError(message)
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _policy_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        _jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value
