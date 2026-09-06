from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.complex_planning import (
    ComplexPlanError,
    decompose_nested_plan,
    refine_complex_plan,
    select_query_semantics,
)
from app.agent.graph import route_after_selector, route_after_semantic_merge
from app.agent.nodes.decompose_query import decompose_query
from app.agent.nodes.refine_query_plan import refine_query_plan
from app.agent.nodes.select_semantics import select_semantics


def _plan(complexity: str = "NON_NESTED") -> dict:
    return {
        "schema_version": "query-plan/v1",
        "intent": "ranking",
        "complexity": complexity,
        "metrics": [{"semantic_id": "play_count", "label": "播放次数"}],
        "dimensions": [{"semantic_id": "play_session.album_id", "label": "专辑"}],
        "filters": [
            {
                "filter_id": "filter:0",
                "field_ids": ["play_session.play_status"],
                "operator": "exact",
                "values": ["completed"],
            }
        ],
        "time": {
            "field_id": "play_session.play_start_at",
            "start": "2026-07-01",
            "end": "2026-07-19",
            "grain": None,
        },
        "sort": [{"semantic_id": "play_count", "direction": "desc"}],
        "join_path": ["play_session.album"],
        "subplans": [],
        "limit": 10,
        "comparison": None,
        "source_hints": {},
    }


def _metadata():
    metrics = [
        {
            "id": "play_count",
            "relevant_columns": ["play_session.id", "play_session.play_start_at"],
        },
        {"id": "unrelated_metric", "relevant_columns": ["payment_record.id"]},
    ]
    tables = [
        {
            "id": "play_session",
            "name": "play_session",
            "columns": [
                {"id": "play_session.id"},
                {"id": "play_session.album_id"},
                {"id": "play_session.play_status"},
                {"id": "play_session.play_start_at"},
            ],
        },
        {
            "id": "audio_album",
            "name": "audio_album",
            "columns": [{"id": "audio_album.id"}],
        },
        {
            "id": "payment_record",
            "name": "payment_record",
            "columns": [{"id": "payment_record.id"}],
        },
    ]
    relationships = [
        {
            "id": "play_session.album",
            "source_table": "play_session",
            "target_table": "audio_album",
        }
    ]
    return metrics, tables, relationships


def test_selector_keeps_only_ids_referenced_by_current_query_plan():
    metrics, tables, relationships = _metadata()
    selection = select_query_semantics(
        _plan(),
        metric_infos=metrics,
        table_infos=tables,
        relationships=relationships,
    )

    assert selection.metric_ids == ("play_count",)
    assert set(selection.field_ids) == {
        "play_session.album_id",
        "play_session.play_status",
        "play_session.play_start_at",
    }
    assert selection.table_ids == ("play_session", "audio_album")
    assert selection.relationship_ids == ("play_session.album",)


def test_decomposer_and_refiner_validate_nested_subplans():
    plan = _plan("NESTED")
    plan["subplans"] = [
        {
            "subplan_id": "subplan:comparison:1",
            "purpose": "comparison_operand",
            "metric_ids": ["play_count"],
            "dimension_ids": ["play_session.album_id"],
            "filter_ids": ["filter:0"],
        }
    ]
    metrics, tables, relationships = _metadata()
    selection = select_query_semantics(
        plan,
        metric_infos=metrics,
        table_infos=tables,
        relationships=relationships,
    ).to_state()
    decomposition = decompose_nested_plan(plan)

    refined = refine_complex_plan(plan, selection, decomposition)

    assert decomposition[0]["subplan_id"] == "subplan:comparison:1"
    assert refined["refinement"] == {
        "status": "validated",
        "selected_metric_count": 1,
        "selected_field_count": 3,
        "subplan_count": 1,
    }


def test_refiner_rejects_semantic_ids_outside_the_plan():
    with pytest.raises(ComplexPlanError, match="指标不属于当前 QueryPlan"):
        refine_complex_plan(
            _plan(),
            {
                "metric_ids": ["unrelated_metric"],
                "field_ids": [],
                "relationship_ids": [],
            },
            [],
        )


def test_graph_routes_easy_and_complex_plans_on_demand():
    assert route_after_semantic_merge({"query_plan": {"complexity": "EASY"}}) == (
        "add_extra_context"
    )
    assert route_after_semantic_merge({"query_plan": {"complexity": "NON_NESTED"}}) == (
        "select_semantics"
    )
    assert route_after_selector({"query_plan": {"complexity": "NON_NESTED"}}) == (
        "refine_query_plan"
    )
    assert route_after_selector({"query_plan": {"complexity": "NESTED"}}) == (
        "decompose_query"
    )


def test_complex_role_nodes_emit_observable_context_in_order():
    plan = _plan("NESTED")
    plan["subplans"] = [
        {
            "subplan_id": "subplan:comparison:1",
            "purpose": "comparison_operand",
            "metric_ids": ["play_count"],
            "dimension_ids": ["play_session.album_id"],
            "filter_ids": ["filter:0"],
        }
    ]
    metrics, tables, relationships = _metadata()
    state = {
        "query_plan": plan,
        "metric_infos": metrics,
        "table_infos": tables,
        "relationships": relationships,
        "planning_roles": [],
    }
    events: list[dict] = []
    runtime = SimpleNamespace(stream_writer=events.append)

    async def run_roles():
        state.update(await select_semantics(state, runtime))
        state.update(await decompose_query(state, runtime))
        state.update(await refine_query_plan(state, runtime))

    asyncio.run(run_roles())

    assert state["planning_roles"] == ["Selector", "Decomposer", "Refiner"]
    assert state["query_plan_refined"] is True
    context_events = [event for event in events if event["type"] == "context"]
    assert context_events[-1]["planning_roles"] == ["Selector", "Decomposer", "Refiner"]
    assert context_events[-1]["query_plan_refined"] is True
