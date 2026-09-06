from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.nodes.validate_query_plan import validate_query_plan
from app.services.dry_plan_service import DryPlanValidationError, validate_dry_plan


def _plan() -> dict:
    return {
        "schema_version": "query-plan/v1",
        "intent": "ranking",
        "complexity": "NON_NESTED",
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
        "join_path": [],
        "subplans": [],
        "limit": 10,
        "comparison": None,
        "source_hints": {},
    }


METRICS = [
    {
        "id": "play_count",
        "relevant_columns": ["play_session.id", "play_session.play_start_at"],
    }
]
TABLES = [
    {
        "id": "play_session",
        "name": "play_session",
        "columns": [
            {"id": "play_session.id"},
            {"id": "play_session.album_id"},
            {"id": "play_session.play_status"},
            {"id": "play_session.play_start_at"},
        ],
    }
]


def test_dry_plan_validates_all_stable_references_and_acl():
    result = validate_dry_plan(
        _plan(),
        metric_infos=METRICS,
        table_infos=TABLES,
        relationships=[],
        access_policy={"table_acl": {"play_session": ["id"]}},
        max_result_rows=500,
    )

    assert result.checks == (
        "schema_version",
        "semantic_references",
        "join_path",
        "sort_and_limit",
        "table_acl",
    )


def test_dry_plan_rejects_unknown_ids_and_unauthorized_tables():
    unknown = _plan()
    unknown["dimensions"] = [{"semantic_id": "missing.secret", "label": "未知"}]
    with pytest.raises(DryPlanValidationError, match="字段未在当前语义构建中召回"):
        validate_dry_plan(
            unknown,
            metric_infos=METRICS,
            table_infos=TABLES,
            relationships=[],
            access_policy={"table_acl": {"play_session": ["id"]}},
            max_result_rows=500,
        )

    with pytest.raises(DryPlanValidationError, match="未授权的表"):
        validate_dry_plan(
            _plan(),
            metric_infos=METRICS,
            table_infos=TABLES,
            relationships=[],
            access_policy={"table_acl": {"audio_album": ["id"]}},
            max_result_rows=500,
        )


def test_dry_plan_node_emits_success_before_sql_generation():
    events: list[dict] = []
    runtime = SimpleNamespace(stream_writer=events.append)
    state = {
        "query_plan": _plan(),
        "metric_infos": METRICS,
        "table_infos": TABLES,
        "relationships": [],
        "access_policy": {"table_acl": {"play_session": ["id"]}},
    }

    result = asyncio.run(validate_query_plan(state, runtime))

    assert result["dry_plan_status"] == "validated"
    assert [event["step"] for event in events if event["type"] == "progress"] == [
        "Dry Plan 校验",
        "Dry Plan 校验",
    ]
    assert next(event for event in events if event["type"] == "context")[
        "dry_plan_status"
    ] == "validated"
