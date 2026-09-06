"""SQL AST guard for generated read-only audiobook analytics queries."""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, traverse_scope


class SQLSafetyError(ValueError):
    """Raised when generated SQL cannot be safely executed."""


@dataclass(frozen=True)
class SafeSQL:
    sql: str
    tables: tuple[str, ...]
    limit: int
    validation_stages: tuple[str, ...] = (
        "ast_permissions",
        "rls_injection",
        "post_rls_ast",
    )


_FORBIDDEN_FUNCTIONS = {
    "BENCHMARK",
    "GET_LOCK",
    "LOAD_FILE",
    "RELEASE_LOCK",
    "SLEEP",
}


def extract_sensitive_columns(table_infos: list[dict]) -> set[str]:
    """Phase 1.3：从 table_infos 提取敏感列全名集合（"表名.列名" 小写）。

    供 validate_and_normalize_sql 的 sensitive_columns 参数使用。
    调用方一般在校验前调用本函数，把结果传入 guard。
    """

    sensitive: set[str] = set()
    for table in table_infos:
        table_name = str(table.get("name", "")).lower()
        for column in table.get("columns", []):
            if column.get("sensitive"):
                column_name = str(column.get("name", "")).lower()
                if table_name and column_name:
                    sensitive.add(f"{table_name}.{column_name}")
    return sensitive


def extract_filter_only_columns(table_infos: list[dict]) -> set[str]:
    """Return sensitive columns explicitly exposed for aggregate WHERE filtering."""

    return {
        f"{str(table.get('name', '')).lower()}.{str(column.get('name', '')).lower()}"
        for table in table_infos
        for column in table.get("columns", [])
        if table.get("name") and column.get("name") and column.get("filter_only")
    }


def validate_and_normalize_sql(
    sql: str,
    table_infos: list[dict],
    max_result_rows: int,
    *,
    sensitive_columns: set[str] | None = None,
    filter_only_columns: set[str] | None = None,
    relationships: list[dict] | None = None,
    row_level_scope: list[dict] | None = None,
    analysis_plan: dict | None = None,
    dialect: str = "mysql",
    table_acl: dict[str, list[str]] | None = None,
    allowed_functions: list[str] | tuple[str, ...] | set[str] | None = None,
) -> SafeSQL:
    """Allow one simple SELECT over only the selected safe schema context.

    参数：
        sql: 待校验的 SQL 文本。
        table_infos: 授权表与其字段信息，结构同 TableInfoState 序列化结果。
        max_result_rows: 强制 LIMIT 上限。
        sensitive_columns: Phase 1.3。敏感列全名集合（"表名.列名"小写），
            形如 {"user.phone"}。命中则拒绝，防止敏感字段通过 LLM 猜测进入 SQL。
            默认 None 表示不启用敏感阻断（向后兼容既有调用方）。
        relationships: 授权表关系白名单，结构同 RelationshipState 序列化结果。
            多表 SQL 必须为每个 JOIN 命中一条已声明关系；None/空目录不再跳过
            校验，而是拒绝多表 SQL。单表 SQL 不受关系目录是否为空影响。
        row_level_scope: Phase 1.2。行级数据权限约束，结构为
            [{"column": "region", "value": "华东"}]。提供后，会在校验通过的
            AST 上注入 WHERE 等值过滤，实现用户无感的行级数据隔离。
            注入的列必须已在授权字段范围内，否则拒绝（防配置错误越权）。
            默认 None/空 表示不注入（admin 全量可见）。
        dialect: Phase 3.2。sqlglot 解析与生成 SQL 用的方言名，默认 "mysql"。
            支持 mysql/postgres/clickhouse（值传给 sqlglot.parse 的 read 参数
            与 expression.sql 的 dialect 参数）。
        table_acl: 当前访问策略中的表/字段授权。通配符仅用于已审计的绕过或
            旧版策略；显式 ACL 会与召回语义层字段取交集。
        allowed_functions: 当前访问策略允许的函数集合，并继续受方言安全白名单
            限制；"*" 只表示采用方言安全白名单，不会允许任意函数。
    """

    if not sql or not sql.strip():
        raise SQLSafetyError("未生成可执行 SQL")
    if max_result_rows < 1:
        raise ValueError("max_result_rows 必须大于 0")
    expression = _parse_single_select(sql, dialect)
    _reject_unsupported_read_features(expression)
    _reject_tautological_or(expression)
    allowed_columns = _allowed_columns(table_infos, table_acl)
    scopes = _validate_scoped_schema(expression, allowed_columns)
    table_aliases = _flatten_table_aliases(scopes)
    # Phase 1.3：敏感列阻断。
    if sensitive_columns:
        _validate_sensitive_columns(
            expression,
            table_aliases,
            sensitive_columns,
            filter_only_columns or set(),
        )
    # JOIN 关系目录始终参与校验，空目录只允许单表查询。
    _validate_joins(scopes, allowed_columns, relationships or [])
    _validate_relationship_connectivity(expression, scopes, allowed_columns, relationships or [])
    if analysis_plan:
        _validate_semantic_requirements(expression, table_aliases, analysis_plan)
    _validate_functions(expression, dialect=dialect, allowed_functions=allowed_functions)
    # 行级权限在各 SQL 作用域内按真实表别名注入。
    if row_level_scope:
        _inject_row_level_scope(scopes, allowed_columns, row_level_scope)
    limit = _apply_limit(expression, max_result_rows)

    # 注入与 LIMIT 变更后重新解析并执行完整安全校验；第二次注入必须幂等。
    normalized = expression.sql(dialect=dialect, pretty=False)
    expression = _parse_single_select(normalized, dialect)
    _reject_unsupported_read_features(expression)
    _reject_tautological_or(expression)
    reparsed_scopes = _validate_scoped_schema(expression, allowed_columns)
    reparsed_aliases = _flatten_table_aliases(reparsed_scopes)
    if sensitive_columns:
        _validate_sensitive_columns(
            expression,
            reparsed_aliases,
            sensitive_columns,
            filter_only_columns or set(),
        )
    _validate_joins(reparsed_scopes, allowed_columns, relationships or [])
    _validate_relationship_connectivity(
        expression, reparsed_scopes, allowed_columns, relationships or []
    )
    if analysis_plan:
        _validate_semantic_requirements(expression, reparsed_aliases, analysis_plan)
    _validate_functions(expression, dialect=dialect, allowed_functions=allowed_functions)
    if row_level_scope:
        before_second_injection = expression.sql(dialect=dialect, pretty=False)
        _inject_row_level_scope(reparsed_scopes, allowed_columns, row_level_scope)
        after_second_injection = expression.sql(dialect=dialect, pretty=False)
        if after_second_injection != before_second_injection:
            raise SQLSafetyError("行级权限注入不是幂等操作，已拒绝执行")
    limit = _apply_limit(expression, max_result_rows)
    return SafeSQL(
        sql=expression.sql(dialect=dialect, pretty=False),
        tables=tuple(sorted(_physical_tables(reparsed_scopes))),
        limit=limit,
    )


def _parse_single_select(sql: str, dialect: str = "mysql") -> exp.Select:
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except ParseError as exc:
        raise SQLSafetyError("SQL 语法无法解析") from exc
    if len(statements) != 1:
        raise SQLSafetyError("只允许执行一条 SQL 语句")
    expression = statements[0]
    if not isinstance(expression, exp.Select):
        raise SQLSafetyError("只允许执行 SELECT 查询")
    return expression


def _reject_unsupported_read_features(expression: exp.Select) -> None:
    if list(expression.find_all(exp.Lock)):
        raise SQLSafetyError("禁止使用 SELECT ... FOR UPDATE 等锁定查询")
    if expression.args.get("offset") is not None:
        raise SQLSafetyError("当前不允许使用 OFFSET 分页")
    for nested_select in expression.find_all(exp.Select):
        if nested_select.args.get("offset") is not None:
            raise SQLSafetyError("当前不允许使用 OFFSET 分页")
    if list(expression.find_all(exp.SessionParameter)):
        raise SQLSafetyError("禁止读取数据库会话或系统变量")
    for star in expression.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SQLSafetyError("禁止使用 SELECT *，请显式指定授权字段")


def _reject_tautological_or(expression: exp.Select) -> None:
    """Reject ``... OR 1=1``-style predicate widening anywhere in the query."""

    for disjunction in expression.find_all(exp.Or):
        if any(_is_constant_true(node) for node in disjunction.walk()):
            raise SQLSafetyError("禁止使用包含恒真条件的 OR 表达式")


def _is_constant_true(node: exp.Expression) -> bool:
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if not isinstance(node, exp.EQ):
        return False
    left, right = node.left, node.right
    return (
        isinstance(left, exp.Literal)
        and isinstance(right, exp.Literal)
        and left.is_string == right.is_string
        and left.this == right.this
    )


def _allowed_columns(
    table_infos: list[dict], table_acl: dict[str, list[str]] | None
) -> dict[str, set[str]]:
    normalized_acl = {
        str(table).lower(): {str(column).lower() for column in columns}
        for table, columns in (table_acl or {}).items()
    }
    wildcard_acl = not normalized_acl or "*" in normalized_acl
    allowed: dict[str, set[str]] = {}
    for table in table_infos:
        table_name = str(table.get("name", "")).lower()
        columns = {
            str(column.get("name", "")).lower()
            for column in table.get("columns", [])
            if column.get("name")
        }
        if not wildcard_acl:
            columns &= normalized_acl.get(table_name, set())
        if table_name and columns:
            allowed[table_name] = columns
    if not allowed:
        raise SQLSafetyError("没有可用的授权表和字段")
    return allowed


def _validate_scoped_schema(
    expression: exp.Select, allowed_columns: dict[str, set[str]]
) -> list[Scope]:
    scopes = traverse_scope(expression)
    if not scopes:
        raise SQLSafetyError("SQL 查询作用域无法解析")
    lineage_cache: dict[tuple[int, str], set[str]] = {}
    for scope in scopes:
        if not isinstance(scope.expression, exp.Select):
            raise SQLSafetyError("只允许 SELECT 作用域")
        for _, (_, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table):
                continue
            if source.db or source.catalog:
                raise SQLSafetyError("禁止访问跨库或系统库表")
            if source.name.lower() not in allowed_columns:
                raise SQLSafetyError(f"表未授权：{source.name}")
        output_names = {name.lower() for name in scope.expression.named_selects}
        scoped_columns = [
            column
            for column in scope.expression.find_all(exp.Column)
            if _nearest_select(column) is scope.expression
        ]
        for column in scoped_columns:
            if column.name.lower() in output_names and _is_alias_reference_position(
                column, scope.expression
            ):
                continue
            resolved = _resolve_scope_column_ids(scope, column, allowed_columns, lineage_cache)
            if not resolved:
                qualifier = f"{column.table}." if column.table else ""
                raise SQLSafetyError(f"字段未授权：{qualifier}{column.name}")
    return scopes


def _resolve_scope_column_ids(
    scope: Scope,
    column: exp.Column,
    allowed_columns: dict[str, set[str]],
    lineage_cache: dict[tuple[int, str], set[str]],
) -> set[str]:
    column_name = column.name.lower()
    qualifier = column.table.lower()
    if qualifier:
        source = scope.sources.get(qualifier)
        if source is None and scope.parent is not None:
            return _resolve_scope_column_ids(scope.parent, column, allowed_columns, lineage_cache)
        return _resolve_source_column_ids(source, column_name, allowed_columns, lineage_cache)

    candidates: list[exp.Table | Scope] = []
    for _, (_, source) in scope.selected_sources.items():
        if column_name in _source_output_columns(source, allowed_columns):
            candidates.append(source)
    if len(candidates) == 1:
        return _resolve_source_column_ids(
            candidates[0], column_name, allowed_columns, lineage_cache
        )
    if not candidates and scope.parent is not None:
        return _resolve_scope_column_ids(scope.parent, column, allowed_columns, lineage_cache)
    if len(candidates) > 1:
        raise SQLSafetyError(f"未限定或未授权字段：{column.name}")
    return set()


def _resolve_source_column_ids(
    source: exp.Table | Scope | None,
    column_name: str,
    allowed_columns: dict[str, set[str]],
    lineage_cache: dict[tuple[int, str], set[str]],
) -> set[str]:
    if isinstance(source, exp.Table):
        table_name = source.name.lower()
        if column_name not in allowed_columns.get(table_name, set()):
            return set()
        return {f"{table_name}.{column_name}"}
    if isinstance(source, Scope):
        return _scope_output_lineage(source, column_name, allowed_columns, lineage_cache)
    return set()


def _scope_output_lineage(
    scope: Scope,
    output_name: str,
    allowed_columns: dict[str, set[str]],
    lineage_cache: dict[tuple[int, str], set[str]],
) -> set[str]:
    cache_key = (id(scope), output_name)
    if cache_key in lineage_cache:
        return lineage_cache[cache_key]
    # Break pathological recursive references conservatively.
    lineage_cache[cache_key] = set()
    selected = next(
        (
            item
            for item in scope.expression.expressions
            if item.alias_or_name.lower() == output_name
        ),
        None,
    )
    if selected is None:
        return set()
    resolved: set[str] = set()
    for column in selected.find_all(exp.Column):
        if _nearest_select(column) is not scope.expression:
            continue
        resolved.update(_resolve_scope_column_ids(scope, column, allowed_columns, lineage_cache))
    lineage_cache[cache_key] = resolved
    return resolved


def _source_output_columns(
    source: exp.Table | Scope, allowed_columns: dict[str, set[str]]
) -> set[str]:
    if isinstance(source, exp.Table):
        return allowed_columns.get(source.name.lower(), set())
    return {name.lower() for name in source.expression.named_selects}


def _nearest_select(node: exp.Expression) -> exp.Select | None:
    current = node.parent
    while current is not None:
        if isinstance(current, exp.Select):
            return current
        current = current.parent
    return None


def _physical_tables(scopes: list[Scope]) -> set[str]:
    return {
        source.name.lower()
        for scope in scopes
        for _, (_, source) in scope.selected_sources.items()
        if isinstance(source, exp.Table)
    }


def _source_physical_tables(source: exp.Table | Scope) -> set[str]:
    if isinstance(source, exp.Table):
        return {source.name.lower()}
    return _physical_tables(traverse_scope(source.expression))


def _flatten_table_aliases(scopes: list[Scope]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for scope in scopes:
        for alias, (_, source) in scope.selected_sources.items():
            physical_tables = _source_physical_tables(source)
            if len(physical_tables) == 1:
                aliases[alias.lower()] = next(iter(physical_tables))
                if isinstance(source, exp.Table):
                    aliases.setdefault(source.name.lower(), source.name.lower())
    return aliases


def _is_alias_reference_position(column: exp.Column, select: exp.Select) -> bool:
    """True when the column sits inside ORDER BY / GROUP BY / HAVING."""

    node: exp.Expression | None = column.parent
    while node is not None and node is not select:
        if isinstance(node, exp.Order | exp.Group | exp.Having):
            return True
        node = node.parent
    return False


def _validate_sensitive_columns(
    expression: exp.Select,
    table_aliases: dict[str, str],
    sensitive_columns: set[str],
    filter_only_columns: set[str],
) -> None:
    """Phase 1.3：阻断敏感字段。

    sensitive_columns 使用 "表名.列名"（均小写）的形式标记敏感字段。
    对于 SQL 中出现的每个列引用，还原其真实表名后拼接，命中即拒绝。
    无法定位到表的列（如纯别名引用）跳过——这些已在 _validate_columns 里兜底。
    """

    aggregate_query = _is_aggregate_query(expression)
    for column in expression.find_all(exp.Column):
        column_name = column.name.lower()
        qualifier = column.table.lower()
        if not qualifier:
            # 未限定表名的列：若该列名在任意表的敏感集合里出现，一律拒绝（保守策略）
            for candidate_table in set(table_aliases.values()):
                qualified = f"{candidate_table}.{column_name}"
                if qualified in sensitive_columns:
                    if (
                        qualified in filter_only_columns
                        and aggregate_query
                        and _is_inside_where(column, expression)
                    ):
                        continue
                    raise SQLSafetyError(f"禁止查询敏感字段：{column.name}")
            continue
        resolved_table = table_aliases.get(qualifier)
        qualified = f"{resolved_table}.{column_name}" if resolved_table else ""
        if qualified in sensitive_columns:
            if (
                qualified in filter_only_columns
                and aggregate_query
                and _is_inside_where(column, expression)
            ):
                continue
            raise SQLSafetyError(f"禁止查询敏感字段：{column.table}.{column.name}")


def _is_aggregate_query(expression: exp.Select) -> bool:
    return any(
        isinstance(function, exp.Count | exp.Sum | exp.Avg | exp.Min | exp.Max)
        for function in expression.find_all(exp.Func)
    )


def _is_inside_where(column: exp.Column, expression: exp.Select) -> bool:
    node: exp.Expression | None = column.parent
    while node is not None and node is not expression:
        if isinstance(node, exp.Where):
            return True
        node = node.parent
    return False


def _validate_joins(
    scopes: list[Scope],
    allowed_columns: dict[str, set[str]],
    relationships: list[dict],
) -> None:
    """Phase 1.1：JOIN 关系白名单校验（严格模式）。

    遍历 SQL 中所有 JOIN，解析其 ON 条件里的等值对（递归拆解 AND），
    要求每个 JOIN 至少有一个连接当前新表与左侧已出现表的关系边。
    无法解析或全部不匹配的 JOIN 一律拒绝，防止笛卡尔积/多对多放大。

    授权关系按 (表, 列) 二元组的无序对存储，正向反向都视为合法。
    """

    scopes_with_joins = [scope for scope in scopes if scope.expression.args.get("joins")]
    if not scopes_with_joins:
        return  # 无 JOIN，无需校验

    # 构建授权连接对的集合：{(表A.列A, 表B.列B)}，同时放入反向。
    authorized_pairs: set[tuple[str, str]] = set()
    for rel in relationships:
        source = f"{str(rel.get('source_table', '')).lower()}.{str(rel.get('source_column', '')).lower()}"
        target = f"{str(rel.get('target_table', '')).lower()}.{str(rel.get('target_column', '')).lower()}"
        if source != "." and target != ".":
            authorized_pairs.add(tuple(sorted((source, target))))

    if not authorized_pairs:
        raise SQLSafetyError("关系目录为空，禁止执行多表 SQL")

    lineage_cache: dict[tuple[int, str], set[str]] = {}
    for scope in scopes_with_joins:
        joins = scope.expression.args.get("joins") or []
        from_clause = scope.expression.args.get("from_")
        base_source = _scope_source_for_relation(scope, from_clause.this if from_clause else None)
        if base_source is None:
            raise SQLSafetyError("多表 SQL 缺少可验证的主表")
        seen_tables = _source_physical_tables(base_source)

        for join in joins:
            if not isinstance(join, exp.Join):
                raise SQLSafetyError("JOIN 目标无法验证")
            joined_source = _scope_source_for_relation(scope, join.this)
            if joined_source is None:
                raise SQLSafetyError("JOIN 目标无法验证")
            joined_tables = _source_physical_tables(joined_source)
            if str(join.args.get("kind") or "").upper() == "CROSS":
                raise SQLSafetyError("禁止 CROSS JOIN 笛卡尔积")
            on = join.args.get("on")
            if on is None:
                raise SQLSafetyError("JOIN 必须包含 ON 条件，禁止笛卡尔积")
            if any(True for _ in on.find_all(exp.Or)):
                raise SQLSafetyError("JOIN 的 ON 条件禁止使用 OR")
            eq_pairs = _extract_equijoin_pairs(on, scope, allowed_columns, lineage_cache)
            if not eq_pairs:
                raise SQLSafetyError("JOIN 的 ON 条件必须包含表间等值连接")
            if not any(
                pair in authorized_pairs
                and _pair_connects_joined_table(pair, joined_tables, seen_tables)
                for pair in eq_pairs
            ):
                raise SQLSafetyError(
                    "JOIN 关系未授权，禁止未声明的表关联（防止笛卡尔积/多对多放大）"
                )
            seen_tables.update(joined_tables)


def _validate_relationship_connectivity(
    expression: exp.Select,
    scopes: list[Scope],
    allowed_columns: dict[str, set[str]],
    relationships: list[dict],
) -> None:
    """Require JOINs and cross-scope comparisons to connect every physical table."""

    physical_tables = _physical_tables(scopes)
    if len(physical_tables) < 2:
        return
    authorized_pairs = _authorized_relationship_pairs(relationships)
    if not authorized_pairs:
        raise SQLSafetyError("关系目录为空，禁止执行多表 SQL")

    lineage_cache: dict[tuple[int, str], set[str]] = {}
    used_pairs: set[tuple[str, str]] = set()
    scope_by_expression = {id(scope.expression): scope for scope in scopes}
    for scope in scopes:
        for equality in scope.expression.find_all(exp.EQ):
            if _nearest_select(equality) is not scope.expression:
                continue
            left_ids = _column_physical_ids(
                equality.left,
                scope,
                allowed_columns,
                lineage_cache,  # type: ignore[arg-type]
            )
            right_ids = _column_physical_ids(
                equality.right,
                scope,
                allowed_columns,
                lineage_cache,  # type: ignore[arg-type]
            )
            used_pairs.update(
                pair
                for left_id in left_ids
                for right_id in right_ids
                if (pair := tuple(sorted((left_id, right_id)))) in authorized_pairs
            )

        for membership in scope.expression.find_all(exp.In):
            if _nearest_select(membership) is not scope.expression:
                continue
            query = membership.args.get("query")
            inner_select = query.this if isinstance(query, exp.Subquery) else None
            inner_scope = (
                scope_by_expression.get(id(inner_select))
                if isinstance(inner_select, exp.Select)
                else None
            )
            if inner_scope is None or not inner_select.expressions:
                continue
            outer_ids = _expression_column_ids(
                membership.this, scope, allowed_columns, lineage_cache
            )
            inner_ids = _expression_column_ids(
                inner_select.expressions[0],
                inner_scope,
                allowed_columns,
                lineage_cache,
            )
            used_pairs.update(
                pair
                for outer_id in outer_ids
                for inner_id in inner_ids
                if (pair := tuple(sorted((outer_id, inner_id)))) in authorized_pairs
            )

    connected = {next(iter(physical_tables))}
    while True:
        expanded = connected | {
            right for left, right in _relationship_table_edges(used_pairs) if left in connected
        }
        if expanded == connected:
            break
        connected = expanded
    if connected != physical_tables:
        raise SQLSafetyError("多表 SQL 未通过已声明关系形成连通路径")


def _authorized_relationship_pairs(relationships: list[dict]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for relationship in relationships:
        source = (
            f"{str(relationship.get('source_table', '')).lower()}."
            f"{str(relationship.get('source_column', '')).lower()}"
        )
        target = (
            f"{str(relationship.get('target_table', '')).lower()}."
            f"{str(relationship.get('target_column', '')).lower()}"
        )
        if source != "." and target != ".":
            pairs.add(tuple(sorted((source, target))))
    return pairs


def _relationship_table_edges(
    pairs: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for left, right in pairs:
        left_table = left.rsplit(".", 1)[0]
        right_table = right.rsplit(".", 1)[0]
        edges.add((left_table, right_table))
        edges.add((right_table, left_table))
    return edges


def _expression_column_ids(
    expression: exp.Expression,
    scope: Scope,
    allowed_columns: dict[str, set[str]],
    lineage_cache: dict[tuple[int, str], set[str]],
) -> set[str]:
    resolved: set[str] = set()
    for column in expression.find_all(exp.Column):
        if _nearest_select(column) is scope.expression:
            resolved.update(
                _resolve_scope_column_ids(scope, column, allowed_columns, lineage_cache)
            )
    return resolved


def _scope_source_for_relation(
    scope: Scope, relation: exp.Expression | None
) -> exp.Table | Scope | None:
    if not isinstance(relation, exp.Table):
        return None
    alias = relation.alias_or_name.lower()
    source = scope.sources.get(alias)
    return source if isinstance(source, exp.Table | Scope) else None


def _pair_connects_joined_table(
    pair: tuple[str, str], joined_tables: set[str], seen_tables: set[str]
) -> bool:
    left_table = pair[0].rsplit(".", 1)[0]
    right_table = pair[1].rsplit(".", 1)[0]
    return (left_table in joined_tables and right_table in seen_tables) or (
        right_table in joined_tables and left_table in seen_tables
    )


def _extract_equijoin_pairs(
    node: exp.Expression,
    scope: Scope,
    allowed_columns: dict[str, set[str]],
    lineage_cache: dict[tuple[int, str], set[str]],
) -> list[tuple[str, str]]:
    """递归拆解 ON 条件，提取所有等值连接对。

    支持：EQ、AND（两边各拆）。OR 在 JOIN 校验入口直接拒绝。
    只保留两边都是 Column 的等值对，用 table_aliases 还原别名→真实表名。
    返回的 pair 是无序的（已排序），便于和 authorized_pairs 匹配。
    """

    pairs: list[tuple[str, str]] = []

    def _collect(n: exp.Expression) -> None:
        if isinstance(n, exp.EQ):
            left, right = n.left, n.right
            # sqlglot 的 EQ.left/right 返回 Expr，运行时是 Expression 的子类实例，
            # 但类型存根里 Expr 与 Expression 是平行类型，mypy 报协变告警，运行时正确。
            left_ids = _column_physical_ids(
                left,
                scope,
                allowed_columns,
                lineage_cache,  # type: ignore[arg-type]
            )
            right_ids = _column_physical_ids(
                right,
                scope,
                allowed_columns,
                lineage_cache,  # type: ignore[arg-type]
            )
            for left_id in left_ids:
                for right_id in right_ids:
                    if left_id != right_id:
                        pairs.append(tuple(sorted((left_id, right_id))))
        elif isinstance(n, exp.And):
            _collect(n.left)  # type: ignore[arg-type]
            _collect(n.right)  # type: ignore[arg-type]

    _collect(node)
    return pairs


def _column_physical_ids(
    node: exp.Expression,
    scope: Scope,
    allowed_columns: dict[str, set[str]],
    lineage_cache: dict[tuple[int, str], set[str]],
) -> set[str]:
    """Resolve a scoped column to physical ``table.column`` identifiers."""

    if not isinstance(node, exp.Column):
        return set()
    return _resolve_scope_column_ids(scope, node, allowed_columns, lineage_cache)


def _validate_semantic_requirements(
    expression: exp.Select,
    table_aliases: dict[str, str],
    analysis_plan: dict,
) -> None:
    """Reject structurally valid SQL that silently drops user-requested semantics."""

    for requirement in analysis_plan.get("filter_requirements", []):
        if not _matches_filter_requirement(expression, table_aliases, requirement):
            raise SQLSafetyError(f"SQL 未落实必要筛选条件：{requirement.get('label', '未知条件')}")

    for requirement in analysis_plan.get("metric_requirements", []):
        functions = _matching_metric_functions(expression, table_aliases, requirement)
        minimum = max(1, int(requirement.get("minimum_occurrences", 1)))
        if len(functions) < minimum:
            raise SQLSafetyError(f"SQL 未落实必要指标：{requirement.get('label', '未知指标')}")
        if not requirement.get("allow_distinct", True) and any(
            list(function.find_all(exp.Distinct)) for function in functions
        ):
            raise SQLSafetyError(f"指标口径不允许 DISTINCT：{requirement.get('label', '未知指标')}")
        if requirement.get("operation") == "subtract" and not _has_metric_subtraction(
            expression,
            table_aliases,
            requirement,
            minimum,
        ):
            raise SQLSafetyError(f"SQL 未落实指标差值计算：{requirement.get('label', '未知指标')}")


_COMPARISON_TYPES = (exp.EQ, exp.Like, exp.In, exp.LTE, exp.GTE, exp.LT, exp.GT)


def _matches_filter_requirement(
    expression: exp.Select,
    table_aliases: dict[str, str],
    requirement: dict,
) -> bool:
    expected_columns = {str(column).lower() for column in requirement.get("columns", [])}
    expected_values = [str(value).lower() for value in requirement.get("values", [])]
    value_match = requirement.get("value_match", "exact")
    allowed_operators = {str(operator).lower() for operator in requirement.get("operators", [])}
    location = requirement.get("location", "any")

    for comparison in expression.find_all(*_COMPARISON_TYPES):
        matching_columns = [
            column
            for column in comparison.find_all(exp.Column)
            if _resolved_column_id(column, table_aliases) in expected_columns
        ]
        if not matching_columns:
            continue
        if location == "where" and not _expression_is_inside_where(comparison, expression):
            continue
        if allowed_operators:
            operator = _normalized_comparison_operator(comparison, matching_columns[0])
            if operator not in allowed_operators:
                continue
        if not expected_values:
            return True
        literals = [
            str(literal.this).lower()
            for literal in comparison.find_all(exp.Literal)
            if literal.is_string or literal.is_number
        ]
        if value_match == "contains":
            if any(expected in literal for expected in expected_values for literal in literals):
                return True
        elif any(expected == literal for expected in expected_values for literal in literals):
            return True
    return False


def _resolved_column_id(column: exp.Column, table_aliases: dict[str, str]) -> str:
    qualifier = column.table.lower()
    table_name = table_aliases.get(qualifier, qualifier)
    return f"{table_name}.{column.name.lower()}" if table_name else column.name.lower()


def _expression_is_inside_where(node: exp.Expression, expression: exp.Select) -> bool:
    current: exp.Expression | None = node.parent
    while current is not None and current is not expression:
        if isinstance(current, exp.Where):
            return True
        current = current.parent
    return False


def _normalized_comparison_operator(comparison: exp.Expression, target_column: exp.Column) -> str:
    operator = {
        exp.EQ: "eq",
        exp.Like: "like",
        exp.In: "in",
        exp.LTE: "lte",
        exp.GTE: "gte",
        exp.LT: "lt",
        exp.GT: "gt",
    }.get(type(comparison), "")
    if operator not in {"lte", "gte", "lt", "gt"}:
        return operator
    left_columns = list(comparison.left.find_all(exp.Column)) if comparison.left else []
    if target_column in left_columns:
        return operator
    return {"lte": "gte", "gte": "lte", "lt": "gt", "gt": "lt"}[operator]


def _matching_metric_functions(
    expression: exp.Select,
    table_aliases: dict[str, str],
    requirement: dict,
) -> list[exp.Func]:
    aggregate = str(requirement.get("aggregate", "")).upper()
    column_id = str(requirement.get("column", "")).lower()
    target_table = column_id.rsplit(".", 1)[0] if "." in column_id else ""
    return [
        function
        for function in expression.find_all(exp.Func)
        if (function.sql_name() or function.name).upper() == aggregate
        and (
            any(
                _resolved_column_id(column, table_aliases) == column_id
                for column in function.find_all(exp.Column)
            )
            or (
                aggregate == "COUNT"
                and requirement.get("allow_star")
                and target_table in set(table_aliases.values())
                and any(True for _ in function.find_all(exp.Star))
            )
        )
    ]


def _has_metric_subtraction(
    expression: exp.Select,
    table_aliases: dict[str, str],
    requirement: dict,
    minimum: int,
) -> bool:
    return any(
        len(_matching_metric_functions(subtraction, table_aliases, requirement)) >= minimum
        for subtraction in expression.find_all(exp.Sub)
    )


def _inject_row_level_scope(
    scopes: list[Scope],
    allowed_columns: dict[str, set[str]],
    row_level_scope: list[dict],
) -> None:
    """Inject alias-qualified predicates into every scope that reads the target table."""

    resolved_constraints: list[tuple[str, str, str]] = []
    for constraint in row_level_scope:
        operator = str(constraint.get("operator", "eq")).lower()
        if operator != "eq":
            raise SQLSafetyError("行级权限操作符未授权")
        configured_table = str(constraint.get("table", "")).lower()
        column_name = str(constraint.get("column", "")).lower()
        value = constraint.get("value")
        if not column_name or value is None:
            raise SQLSafetyError("行级权限约束缺少列名或值")
        candidate_tables = (
            [configured_table]
            if configured_table
            else [table for table, columns in allowed_columns.items() if column_name in columns]
        )
        if len(candidate_tables) != 1:
            raise SQLSafetyError(
                f"行级权限列 {column_name} 无法唯一定位（命中 {len(candidate_tables)} 张表），拒绝执行以防越权"
            )
        real_table = candidate_tables[0]
        if column_name not in allowed_columns.get(real_table, set()):
            raise SQLSafetyError(f"行级权限字段未授权：{real_table}.{column_name}")
        resolved_constraints.append((real_table, column_name, str(value)))

    for scope in scopes:
        conditions: list[exp.EQ] = []
        for alias, (_, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table):
                continue
            real_table = source.name.lower()
            for target_table, column_name, value in resolved_constraints:
                if real_table != target_table:
                    continue
                qualifier = alias.lower()
                if _scope_has_exact_rls_predicate(scope.expression, qualifier, column_name, value):
                    continue
                conditions.append(
                    exp.EQ(
                        this=exp.Column(
                            this=exp.to_identifier(column_name),
                            table=exp.to_identifier(qualifier),
                        ),
                        expression=exp.Literal.string(value),
                    )
                )
        if conditions:
            _merge_scope_conditions(scope.expression, conditions)


def _scope_has_exact_rls_predicate(
    expression: exp.Select, qualifier: str, column_name: str, value: str
) -> bool:
    where = expression.args.get("where")
    if where is None:
        return False
    for equality in where.find_all(exp.EQ):
        for column, literal in ((equality.left, equality.right), (equality.right, equality.left)):
            if (
                isinstance(column, exp.Column)
                and isinstance(literal, exp.Literal)
                and literal.is_string
                and column.table.lower() == qualifier
                and column.name.lower() == column_name
                and str(literal.this) == value
            ):
                return True
    return False


def _merge_scope_conditions(expression: exp.Select, conditions: list[exp.EQ]) -> None:
    combined: exp.Expression = conditions[0]
    for condition in conditions[1:]:
        combined = exp.And(this=combined, expression=condition)
    original_where = expression.args.get("where")
    if original_where is not None:
        combined = exp.And(this=original_where.this, expression=combined)
    expression.where(combined, copy=False)


_COMMON_ALLOWED_FUNCTIONS = {
    "ABS",
    "AVG",
    "CAST",
    "CEIL",
    "COALESCE",
    "CONCAT",
    "COUNT",
    "CURRENT_DATE",
    "CURRENT_DATETIME",
    "CURRENT_TIMESTAMP",
    "DATE",
    "DATE_ADD",
    "DATE_DIFF",
    "DATE_SUB",
    "DATE_TRUNC",
    "DENSE_RANK",
    "EXTRACT",
    "FLOOR",
    "GREATEST",
    "IF",
    "LAG",
    "LEAD",
    "LEAST",
    "LOWER",
    "MAX",
    "MIN",
    "MONTH",
    "NULLIF",
    "RANK",
    "ROUND",
    "ROW_NUMBER",
    "SUM",
    "TIME_TO_STR",
    "TRIM",
    "UPPER",
    "WEEK",
    "YEAR",
}

_DIALECT_ALLOWED_FUNCTIONS = {
    "mysql": {
        "DAY",
        "FROM_UNIXTIME",
        "GROUP_CONCAT",
        "IFNULL",
        "QUARTER",
        "STR_TO_TIME",
        "TIMESTAMPDIFF",
        "UNIX_TIMESTAMP",
    },
    "postgres": {"STRING_AGG", "TO_CHAR"},
    "postgresql": {"STRING_AGG", "TO_CHAR"},
    "clickhouse": {
        "COUNTIF",
        "SUMIF",
        "TOSTARTOFDAY",
        "TOSTARTOFMONTH",
        "TOSTARTOFWEEK",
        "UNIQ",
        "UNIQEXACT",
    },
    "doris": {"DAY", "GROUP_CONCAT", "IFNULL", "QUARTER", "TIMESTAMPDIFF"},
}

_FUNCTION_ALIASES = {
    "DATE_FORMAT": "TIME_TO_STR",
    "STR_TO_DATE": "STR_TO_TIME",
}

_INTERNAL_SAFE_FUNCTIONS = {"CASE", "EXISTS", "TS_OR_DS_TO_TIMESTAMP"}


def _validate_functions(
    expression: exp.Select,
    *,
    dialect: str,
    allowed_functions: list[str] | tuple[str, ...] | set[str] | None,
) -> None:
    dialect_name = dialect.lower()
    safe_functions = _COMMON_ALLOWED_FUNCTIONS | _DIALECT_ALLOWED_FUNCTIONS.get(dialect_name, set())
    policy_functions = {
        _FUNCTION_ALIASES.get(str(name).upper(), str(name).upper())
        for name in (allowed_functions or [])
    }
    if policy_functions and "*" not in policy_functions:
        safe_functions &= policy_functions
    for function in expression.find_all(exp.Func):
        if isinstance(function, exp.Connector):
            continue
        name = _function_name(function)
        if name in _INTERNAL_SAFE_FUNCTIONS:
            continue
        if name in _FORBIDDEN_FUNCTIONS:
            raise SQLSafetyError(f"禁止调用函数：{name}")
        if name not in safe_functions:
            raise SQLSafetyError(f"函数未在方言白名单中：{name}")


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return str(function.name).upper()
    return str(function.sql_name() or function.name).upper()


def _apply_limit(expression: exp.Select, max_result_rows: int) -> int:
    limit = expression.args.get("limit")
    if limit is None:
        expression.limit(max_result_rows, copy=False)
        return max_result_rows
    literal = limit.expression
    if not isinstance(literal, exp.Literal) or not literal.is_int:
        raise SQLSafetyError("LIMIT 必须是整数常量")
    requested = int(literal.this)
    if requested < 1:
        raise SQLSafetyError("LIMIT 必须大于 0")
    effective_limit = min(requested, max_result_rows)
    if requested != effective_limit:
        limit.set("expression", exp.Literal.number(effective_limit))
    return effective_limit
