"""sql_gates 模块集中单元测试。"""

import pytest

from app.services.ai.runners.chatbi.sql_gates import (
    build_schema_binding_summary,
    build_sql_schema_preflight_error,
    collect_preflight_unknown_tables,
    detect_sql_static_risk,
    extract_invalid_sql_identifiers,
    extract_schema_table_column_meta,
    extract_schema_table_columns,
    has_sql_plan,
    is_cross_dataset_scope_sql_error,
    is_date_format_sql_error,
    is_diagnostic_sql,
    is_failed_sql_repeat_gate_block,
    is_schema_gate_block,
    is_schema_reference_sql_error,
    is_sql_fatal_error,
    is_sql_schema_preflight_error,
    is_where_condition_sql_error,
    mask_sql_literals_and_comments,
    normalize_sql_text,
    should_require_sql_plan,
)

pytestmark = pytest.mark.no_infrastructure


VISIT_SCHEMA = {
    "view_ai_visit_log": ["ID", "FOLLOW_UP_PERSON", "FOLLOW_UP_DATE"],
    "hrmresource": ["ID", "ACCOUNTNAME"],
}

AGG_SQL = (
    "SELECT hr.ACCOUNTNAME AS sales_name, COUNT(v.ID) AS visit_count "
    "FROM VIEW_AI_VISIT_LOG v "
    "LEFT JOIN HRMRESOURCE hr ON v.FOLLOW_UP_PERSON = hr.ID "
    "WHERE v.FOLLOW_UP_DATE >= '2026-05-01' "
    "GROUP BY hr.ACCOUNTNAME "
)


# --- Gate 前缀 / 错误分类 ---


def test_gate_prefix_detectors():
    assert is_schema_gate_block("[SCHEMA_GATE] blocked")
    assert is_failed_sql_repeat_gate_block("[FAILED_SQL_REPEAT_GATE] blocked")
    assert is_sql_schema_preflight_error("[TOOL_ERROR] SQL 预检失败：字段/表引用错误。")


def test_is_schema_reference_sql_error_patterns():
    assert is_schema_reference_sql_error("ORA-00904: invalid identifier")
    assert is_schema_reference_sql_error("Unknown column 'foo'")
    assert not is_schema_reference_sql_error("ORA-01861: literal does not match format string")


def test_is_where_and_date_format_errors():
    assert is_where_condition_sql_error("ORA-01861: literal does not match format string")
    assert is_where_condition_sql_error("ORA-01722: invalid number")
    assert is_date_format_sql_error("ORA-01861: literal does not match format string")
    assert not is_where_condition_sql_error("connection reset")


def test_is_cross_dataset_scope_sql_error():
    assert is_cross_dataset_scope_sql_error("表 'foo' 不属于当前指定的数据集 'bar'")
    assert not is_cross_dataset_scope_sql_error("unknown column")


def test_extract_invalid_sql_identifiers():
    ids = extract_invalid_sql_identifiers('ORA-00904: "T"."BAD_COL": invalid identifier')
    assert "BAD_COL" in ids


def test_is_sql_fatal_error():
    assert is_sql_fatal_error("[Permission Denied] no access")
    assert is_sql_fatal_error("Error: Dataset 'x' not found")
    assert is_sql_fatal_error("table does not exist")
    assert not is_sql_fatal_error("ORA-00904: invalid identifier")


# --- SQL Plan ---


def test_has_sql_plan_and_should_require():
    assert has_sql_plan("plan <sql_plan>{\"tables\":[]}</sql_plan> end")
    assert not has_sql_plan("no plan here")
    assert should_require_sql_plan("查询各机房 PUE 占比趋势")
    assert should_require_sql_plan("按部门分组统计")
    assert not should_require_sql_plan("查客户列表")


# --- 静态风险 ---


def test_detect_sql_static_risk_rules():
    assert detect_sql_static_risk("") == "SQL 为空"
    assert detect_sql_static_risk("DELETE FROM t") != ""
    assert detect_sql_static_risk("SELECT a FROM t JOIN u") != ""
    assert detect_sql_static_risk("SELECT * FROM demo") == ""
    assert detect_sql_static_risk("SELECT * FROM a CROSS JOIN b") == ""
    assert detect_sql_static_risk("SELECT * FROM a NATURAL JOIN b") == ""
    assert "ORDER BY 后不能接 AND ROWNUM" in detect_sql_static_risk(
        "SELECT * FROM demo ORDER BY id AND ROWNUM <= 10"
    )
    assert detect_sql_static_risk(
        "SELECT name FROM demo WHERE note = 'ORDER BY x AND ROWNUM <= 1'"
    ) == ""


# --- 诊断 SQL 识别 ---


def test_is_diagnostic_sql_patterns():
    assert is_diagnostic_sql("SELECT DISTINCT status FROM t LIMIT 20")
    assert is_diagnostic_sql("SELECT COUNT(1) FROM t")
    assert is_diagnostic_sql(
        "SELECT CREATE_DATE FROM VIEW_AI_CULEOPP WHERE CREATE_DATE IS NOT NULL AND ROWNUM <= 5"
    )
    assert not is_diagnostic_sql("SELECT metric FROM demo LIMIT 10")
    assert is_diagnostic_sql("SHOW TABLES")
    assert is_diagnostic_sql("DESCRIBE demo_table")


def test_is_diagnostic_sql_allows_aggregate_summary_and_filtered_count():
    leave_sql = (
        "SELECT COUNT(*) AS leave_count, SUM(NVL(QJSCT,0)) AS total_days "
        "FROM FORMTABLE_MAIN_11 "
        "WHERE TO_DATE(QJRQQ, 'YYYY-MM-DD') "
        "BETWEEN TO_DATE('2025-07-01', 'YYYY-MM-DD') AND TO_DATE('2026-01-31', 'YYYY-MM-DD')"
    )
    assert not is_diagnostic_sql(leave_sql)
    assert not is_diagnostic_sql(
        "SELECT COUNT(*) AS cnt FROM orders WHERE status = 'ACTIVE'"
    )
    assert is_diagnostic_sql("SELECT COUNT(*) FROM orders")


# --- Schema 预检：列/表 ---


def test_preflight_allows_order_by_aggregate_alias_multi_table():
    sql = AGG_SQL + "ORDER BY visit_count DESC"
    assert build_sql_schema_preflight_error(sql, VISIT_SCHEMA, dialect="oracle") == ""


def test_preflight_allows_order_by_select_alias_multi_table():
    sql = (
        "SELECT hr.ACCOUNTNAME AS sales_name, COUNT(v.ID) AS visit_count "
        "FROM VIEW_AI_VISIT_LOG v "
        "LEFT JOIN HRMRESOURCE hr ON v.FOLLOW_UP_PERSON = hr.ID "
        "ORDER BY sales_name"
    )
    assert build_sql_schema_preflight_error(sql, VISIT_SCHEMA, dialect="oracle") == ""


def test_preflight_blocks_unknown_order_by_column():
    sql = AGG_SQL + "ORDER BY unknown_col DESC"
    err = build_sql_schema_preflight_error(sql, VISIT_SCHEMA, dialect="oracle")
    assert "unknown_col" in err.lower()


def test_preflight_having_aggregate_alias():
    """HAVING 引用 SELECT 聚合别名 — 当前实现应放行或需修复。"""
    sql = AGG_SQL + "HAVING visit_count > 0 ORDER BY visit_count DESC"
    err = build_sql_schema_preflight_error(sql, VISIT_SCHEMA, dialect="oracle")
    assert err == "", f"unexpected preflight error: {err}"


def test_preflight_oracle_implicit_select_alias_without_as():
    schema = {"demo": ["ID", "NAME"]}
    sql = "SELECT NAME sales_name FROM demo ORDER BY sales_name"
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    # sqlglot 通常将 NAME sales_name 解析为 Alias；ORDER BY 应识别别名
    assert err == "", f"unexpected preflight error: {err}"


def test_preflight_select_star_skips_column_tokens():
    schema = {"demo": ["ID", "NAME"]}
    sql = "SELECT * FROM demo d WHERE d.ID = 1"
    assert build_sql_schema_preflight_error(sql, schema, dialect="oracle") == ""


def test_preflight_multi_table_ambiguous_unqualified_column():
    schema = {
        "table_a": ["ID", "STATUS"],
        "table_b": ["ID", "STATUS"],
    }
    sql = "SELECT a.ID FROM table_a a JOIN table_b b ON a.ID = b.ID WHERE STATUS = 1"
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    # 裸 STATUS 在两表都存在，不应误报为 invalid（数据库可解析）
    assert err == "", f"ambiguous but valid column should pass: {err}"


def test_preflight_multi_table_unknown_unqualified_column():
    schema = {
        "table_a": ["ID"],
        "table_b": ["ID"],
    }
    sql = "SELECT a.ID FROM table_a a JOIN table_b b ON a.ID = b.ID WHERE missing_col = 1"
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert "missing_col" in err.lower()


def test_preflight_cte_outer_reference():
    schema = {"base_t": ["ID", "NAME"]}
    sql = (
        "WITH ranked AS (SELECT ID, NAME FROM base_t) "
        "SELECT r.NAME FROM ranked r WHERE r.ID > 0"
    )
    err = build_sql_schema_preflight_error(sql, schema, dialect="oracle")
    assert err == "", f"CTE outer query should pass: {err}"


def test_preflight_clickhouse_dialect():
    schema = {"events": ["event_time", "user_id"]}
    sql = "SELECT user_id, count() AS cnt FROM events GROUP BY user_id ORDER BY cnt LIMIT 10"
    err = build_sql_schema_preflight_error(sql, schema, dialect="clickhouse")
    assert err == "", f"clickhouse aggregate alias: {err}"


def test_preflight_mysql_dialect():
    schema = {"users": ["id", "name"]}
    sql = "SELECT name AS user_name FROM users ORDER BY user_name LIMIT 5"
    err = build_sql_schema_preflight_error(sql, schema, dialect="mysql")
    assert err == "", f"mysql alias order by: {err}"


def test_preflight_regex_fallback_without_dialect():
    schema = {"demo": ["id", "status"]}
    sql = "SELECT id AS row_id FROM demo ORDER BY row_id"
    err = build_sql_schema_preflight_error(sql, schema, dialect=None)
    assert err == "", f"regex fallback should allow alias: {err}"


def test_mask_sql_literals_strips_string_content():
    masked = mask_sql_literals_and_comments("WHERE x = 'ORDER BY fake AND ROWNUM'")
    assert "fake" not in masked or "''" in masked


def test_normalize_sql_text():
    assert normalize_sql_text("  SELECT  1  ") == "select 1"


def test_collect_preflight_unknown_tables_subquery():
    schema = {"outer_t": ["id"]}
    sql = "SELECT o.id FROM outer_t o JOIN (SELECT id FROM inner_t) i ON o.id = i.id"
    unknown = collect_preflight_unknown_tables(sql, schema, dialect="oracle")
    assert "inner_t" in unknown


def test_schema_meta_and_binding_summary():
    output = (
        "table_name: t\n"
        "columns:\n"
        "  - name: CREATE_DATE\n"
        "    type: VARCHAR2\n"
        "    examples:\n"
        "      - '2026-01-01'\n"
    )
    meta = extract_schema_table_column_meta(output)
    assert meta["t"][0].name == "CREATE_DATE"
    cols = extract_schema_table_columns(output)
    assert "CREATE_DATE" in cols["t"]
    summary = build_schema_binding_summary(output)
    assert "字符串日期列" in summary
