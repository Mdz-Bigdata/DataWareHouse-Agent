"""Parameterized feedback SQL templates must not persist request values or RLS."""

from app.entities.feedback_entry import FeedbackEntry
from app.models.mysql.conversation_mysql import ConversationMySQL
from app.models.mysql.query_feedback_mysql import (
    QueryFeedbackMySQL,
    QueryTemplateConfidenceMySQL,
)
from app.models.mysql.query_trace_mysql import QueryTraceMySQL, QueryTracePhaseMySQL
from app.services.sql_template_service import build_parameterized_sql_template


def test_strips_alias_qualified_rls_and_parameterizes_filter_values():
    template = build_parameterized_sql_template(
        "SELECT o.id FROM orders o WHERE o.status = 'completed' "
        "AND o.region = '华东' AND o.amount >= 100 LIMIT 500",
        row_level_scope=[{"table": "orders", "column": "region", "value": "华东"}],
    )

    assert "region" not in template.sql.lower()
    assert "华东" not in template.sql
    assert "completed" not in template.sql
    assert "100" not in template.sql
    assert "LIMIT 500" in template.sql
    assert template.sql.count(":p") == 2
    assert template.parameter_types == ("string", "integer")


def test_preserves_static_function_format_literal():
    template = build_parameterized_sql_template(
        "SELECT DATE_FORMAT(stat_date, '%Y-%m') AS month FROM daily_stat WHERE category = 'audio'"
    )

    assert "%Y-%m" in template.sql
    assert "audio" not in template.sql
    assert template.parameter_types == ("string",)


def test_unparseable_sql_never_falls_back_to_raw_text():
    template = build_parameterized_sql_template("SELECT secret='private-value' FROM broken WHERE")

    assert template.sql == "/* redacted: unparseable SQL */"
    assert "private-value" not in template.sql


def test_persistence_models_and_feedback_entries_have_no_result_rows_field():
    assert "result_rows" not in QueryTraceMySQL.__table__.columns
    assert "rows" not in QueryTraceMySQL.__table__.columns
    assert "result_rows" not in QueryTracePhaseMySQL.__table__.columns
    assert "rows" not in QueryTracePhaseMySQL.__table__.columns
    assert "result_rows" not in FeedbackEntry.__dataclass_fields__
    assert "rows" not in FeedbackEntry.__dataclass_fields__
    assert "result_rows" not in QueryFeedbackMySQL.__table__.columns
    assert "rows" not in QueryFeedbackMySQL.__table__.columns
    assert "result_rows" not in QueryTemplateConfidenceMySQL.__table__.columns
    assert "rows" not in QueryTemplateConfidenceMySQL.__table__.columns
    assert "result_rows" not in ConversationMySQL.__table__.columns
    assert "rows" not in ConversationMySQL.__table__.columns
