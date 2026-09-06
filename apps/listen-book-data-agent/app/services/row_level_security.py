"""行级数据权限（Phase 1.2 + 1.4）。

职责：
1. 解析 user.data_scope（JSON 字符串）为结构化的多维度约束列表。
2. 将约束注入到 SQL AST 的 WHERE 子句，实现行级数据隔离（用户无感）。

data_scope JSON 结构（多维度组合）：
    [
        {"column": "region", "value": "华东"},
        {"column": "category", "value": "audio"}
    ]
含义：该用户的查询会自动追加 `AND (region = '华东' AND category = 'audio')`。

设计约定：
- admin 绕过由 AccessPolicyContextV1 显式表达和审计，本模块不判断角色。
- 普通用户的 data_scope 为 null/空/解析失败时拒绝，不再失败开放。
- 注入逻辑在 sql_guard 校验流程末尾执行，确保注入后的 SQL 仍通过授权校验。
- 注入的列必须已在授权表字段范围内（防止 data_scope 配置错误导致注入非法字段）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.services.access_policy import AccessPolicyError


@dataclass(frozen=True)
class ScopeConstraint:
    """单个行级权限约束维度。"""

    column: str  # 列名（如 region）
    value: str  # 约束值（如 华东）


def parse_data_scope(data_scope: str | None) -> list[ScopeConstraint]:
    """解析 user.data_scope JSON 字符串为约束列表。

    这是旧版约束数组的兼容解析器。无效配置抛出 AccessPolicyError，防止调用方
    意外把解析失败当成无限权限。新请求入口使用 resolve_access_policy。
    """

    if not data_scope or not data_scope.strip():
        raise AccessPolicyError("普通用户缺少访问策略")
    try:
        raw = json.loads(data_scope)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AccessPolicyError("访问策略 JSON 无效") from exc
    if not isinstance(raw, list) or not raw:
        raise AccessPolicyError("旧版访问策略必须是非空约束数组")
    constraints: list[ScopeConstraint] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AccessPolicyError("旧版访问策略约束无效")
        column = item.get("column")
        value = item.get("value")
        if not column or value is None or not str(value).strip():
            raise AccessPolicyError("旧版访问策略约束缺少列名或值")
        constraints.append(ScopeConstraint(column=str(column), value=str(value)))
    return constraints


def scope_constraints_to_dict_list(
    constraints: list[ScopeConstraint],
) -> list[dict[str, str]]:
    """序列化为 dict 列表，供 sql_guard 的 row_level_scope 参数使用。"""

    return [{"column": c.column, "value": c.value} for c in constraints]
