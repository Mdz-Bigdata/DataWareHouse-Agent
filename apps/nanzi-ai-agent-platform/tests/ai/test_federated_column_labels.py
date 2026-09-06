from app.services.ai.federated_column_labels import (
    build_column_label_map,
    extract_alias_term_hints_from_join_sql,
    extract_column_term_map_from_schema,
    merge_column_term_maps,
    resolve_column_display_name,
)
from app.services.ai.executors.prompts import DataQueryPrompts


SCHEMA_SNIPPET = """
--- [Schema:1] type=table dataset=crm_ds table=VIEW_AI_VISIT_LOG ---
table_name: VIEW_AI_VISIT_LOG
dataset: crm_ds
columns:
  - name: visit_id
    type: int
    term: 拜访ID
  - name: FOLLOW_UP_DATE
    type: varchar
    term: 跟进日期
  - name: CUSTOMER_NAME
    type: varchar
    term: 客户名称
  - name: FOLLOW_UP_PERSON
    type: int
    term: 跟进人
"""


def test_extract_column_term_map_from_schema():
    term_map = extract_column_term_map_from_schema(SCHEMA_SNIPPET)
    assert term_map["follow_up_date"] == "跟进日期"
    assert term_map["customer_name"] == "客户名称"


def test_build_column_label_map_uses_chinese_terms():
    term_map = extract_column_term_map_from_schema(SCHEMA_SNIPPET)
    labels = build_column_label_map(
        ["visit_id", "FOLLOW_UP_DATE", "关键进展摘要"],
        term_map,
    )
    assert labels["visit_id"] == "拜访ID"
    assert labels["FOLLOW_UP_DATE"] == "跟进日期"
    assert "关键进展摘要" not in labels


def test_alias_term_hints_from_join_sql():
    term_map = extract_column_term_map_from_schema(SCHEMA_SNIPPET)
    term_map = merge_column_term_maps(
        term_map,
        {"lastname": "销售姓名", "last_name": "销售姓名"},
    )
    hints = extract_alias_term_hints_from_join_sql(
        "SELECT v.FOLLOW_UP_DATE, h.LASTNAME AS sales_name FROM t_visit v JOIN t_hr h ON v.id = h.id",
        term_map,
    )
    assert hints.get("sales_name") == "销售姓名"


def test_resolve_column_display_name_keeps_chinese():
    assert resolve_column_display_name("关键进展摘要", {}) == "关键进展摘要"


def test_federated_synthesis_prompt_includes_column_label_guide():
    prompt = DataQueryPrompts.build_federated_synthesis_prompt(
        "查询拜访",
        "| 跟进日期 | 客户名称 |\n| --- | --- |",
        column_label_guide="- FOLLOW_UP_DATE -> 跟进日期",
    )
    assert "中文表头" in prompt
    assert "FOLLOW_UP_DATE -> 跟进日期" in prompt
