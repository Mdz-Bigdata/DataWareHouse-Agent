from __future__ import annotations

from datetime import date

from app.agent.analysis_plan import build_analysis_plan
from app.agent.query_plan import (
    QueryComplexity,
    build_query_plan_v1,
    resolve_query_plan_v1,
)


def _analysis(question: str) -> dict:
    return build_analysis_plan(question, reference_date=date(2026, 7, 19)).to_state()


def test_query_plan_v1_builds_easy_stable_metric_skeleton():
    question = "平台当前播放次数是多少"
    plan = build_query_plan_v1(question, _analysis(question))

    assert plan.schema_version == "query-plan/v1"
    assert plan.complexity is QueryComplexity.EASY
    assert [item.semantic_id for item in plan.metrics] == ["play_count"]
    assert plan.time.field_id is None
    assert plan.join_path == ()


def test_query_plan_v1_resolves_active_build_ids_for_ranking():
    question = "最近30天播放量最高的前10个专辑"
    analysis = _analysis(question)
    skeleton = build_query_plan_v1(question, analysis).to_state()

    resolved = resolve_query_plan_v1(
        skeleton,
        query=question,
        analysis_plan=analysis,
        metric_infos=[
            {
                "id": "play_count",
                "name": "play_count",
                "alias": ["播放量", "播放次数"],
                "time_column": "play_session.play_start_at",
            }
        ],
        table_infos=[
            {
                "id": "play_session",
                "columns": [
                    {"id": "play_session.album_id", "name": "album_id", "alias": ["专辑"]},
                    {
                        "id": "play_session.play_start_at",
                        "name": "play_start_at",
                        "alias": ["播放时间"],
                    },
                ],
            },
            {
                "id": "audio_album",
                "columns": [{"id": "audio_album.id", "name": "id", "alias": []}],
            },
        ],
        relationships=[{"id": "play_session.album", "source_table": "play_session"}],
    )

    assert resolved.complexity is QueryComplexity.NON_NESTED
    assert [item.semantic_id for item in resolved.metrics] == ["play_count"]
    assert [item.semantic_id for item in resolved.dimensions] == ["play_session.album_id"]
    assert resolved.time.field_id == "play_session.play_start_at"
    assert resolved.sort[0].semantic_id == "play_count"
    assert resolved.sort[0].direction == "desc"
    assert resolved.join_path == ("play_session.album",)
    assert resolved.limit == 10


def test_query_plan_v1_classifies_nested_comparison_and_creates_subplans():
    question = "玄幻和言情类有声书的平均播放时长差多少"
    analysis = _analysis(question)
    plan = build_query_plan_v1(question, analysis)

    assert plan.complexity is QueryComplexity.NESTED
    assert len(plan.subplans) == 2
    assert all(item.purpose == "comparison_operand" for item in plan.subplans)
    assert {item.filter_ids[0] for item in plan.subplans} == {"filter:0", "filter:1"}
