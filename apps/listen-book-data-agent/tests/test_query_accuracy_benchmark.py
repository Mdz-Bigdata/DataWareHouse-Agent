import sqlglot

from app.services.query_accuracy_benchmark import (
    CORE_ACCURACY_CASES,
    DSL_COMPARISON_CASES,
    build_all_metric_accuracy_cases,
)


def test_core_accuracy_benchmark_is_large_enough_for_95_percent_target():
    """16 条用例下必须至少正确 16 条，才可报告 >=95%。"""

    assert len(CORE_ACCURACY_CASES) == 16
    assert 15 / len(CORE_ACCURACY_CASES) < 0.95


def test_core_accuracy_benchmark_has_unique_cases_and_valid_reference_sql():
    case_ids = [case.case_id for case in CORE_ACCURACY_CASES]
    questions = [case.question for case in CORE_ACCURACY_CASES]

    assert len(case_ids) == len(set(case_ids))
    assert len(questions) == len(set(questions))
    for case in CORE_ACCURACY_CASES:
        parsed = sqlglot.parse_one(case.reference_sql, read="mysql")
        assert parsed.key == "select"


def test_all_semantic_metrics_have_one_valid_accuracy_case():
    cases = build_all_metric_accuracy_cases()

    assert len(cases) == 46
    assert len({case.metric_name for case in cases}) == 46
    for case in cases:
        assert case.question
        assert sqlglot.parse_one(case.reference_sql, read="mysql").key == "select"


def test_dsl_comparison_benchmark_has_five_cases_for_each_intent():
    assert len(DSL_COMPARISON_CASES) == 25
    intents = {case.intent for case in DSL_COMPARISON_CASES}
    assert intents == {"aggregate", "trend", "ranking", "compare", "detail"}
    for intent in intents:
        assert sum(case.intent == intent for case in DSL_COMPARISON_CASES) == 5
    for case in DSL_COMPARISON_CASES:
        assert sqlglot.parse_one(case.reference_sql, read="mysql").key == "select"
