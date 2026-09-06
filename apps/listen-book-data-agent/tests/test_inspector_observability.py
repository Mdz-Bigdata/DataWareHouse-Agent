from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.callbacks import UsageMetadataCallbackHandler

from app.agent.nodes.merge_retrieved_info import (
    _apply_catalog_acl,
    _public_semantic_term_matches,
)
from app.core.context import set_request_id
from app.services.query_service import (
    QueryService,
    _public_graph_event,
    _summarize_token_usage,
)


class FakeTraceRepository:
    async def create_trace(self, *args, **kwargs):
        return None

    async def record_phase(self, **kwargs):
        return None

    async def finish_trace(self, **kwargs):
        return None


class CapturingGraph:
    def __init__(self):
        self.input = None

    async def astream(self, **kwargs):
        self.input = kwargs["input"]
        yield {
            "type": "context",
            "query_plan": {"schema_version": "query-plan/v1"},
            "raw_prompt": "never expose this",
            "api_key": "never expose this either",
            "result_rows": [{"private": True}],
        }


class FakeSemanticTermService:
    async def exact_match(self, query: str, **kwargs):
        return []

    async def vector_search(self, query: str, **kwargs):
        return [
            SimpleNamespace(
                id="term-1",
                term_key="play_count",
                standard_term="播放次数",
                synonyms=["播放量"],
                bindings=[{"kind": "metric", "semantic_id": "play_count"}],
                version=2,
                status="published",
            )
        ]


def _query_service(graph: CapturingGraph) -> QueryService:
    return QueryService(
        dw_mysql_repository=None,
        meta_mysql_repository=None,
        column_qdrant_repository=None,
        metric_qdrant_repository=None,
        value_es_repository=None,
        embedding_client=None,
        query_trace_repository=FakeTraceRepository(),
        semantic_term_service=FakeSemanticTermService(),
        graph_runner=graph,
    )


def test_query_pipeline_uses_published_terms_but_allowlists_public_context():
    graph = CapturingGraph()
    service = _query_service(graph)
    set_request_id("request-inspector")

    async def run():
        return [event async for event in service.events("哪些内容播放量高")]

    events = asyncio.run(run())
    assert graph.input["semantic_terms"][0]["standard_term"] == "播放次数"
    context = next(event for event in events if event.get("query_plan"))
    assert context["query_plan"]["schema_version"] == "query-plan/v1"
    assert "raw_prompt" not in context
    assert "api_key" not in context
    assert "result_rows" not in context
    assert events[-1]["token_usage"]["available"] is False


def test_term_matches_are_grounded_in_recalled_semantics_and_current_acl():
    matches = _public_semantic_term_matches(
        [
            {
                "term_key": "play_count",
                "standard_term": "播放次数",
                "version": 2,
                "bindings": [{"kind": "metric", "semantic_id": "play_count"}],
            },
            {
                "term_key": "secret",
                "standard_term": "敏感会员",
                "version": 1,
                "bindings": [{"kind": "column", "semantic_id": "member.phone"}],
            },
        ],
        metric_infos=[
            {
                "id": "play_count",
                "relevant_columns": ["play_session.id"],
            }
        ],
        table_infos=[
            {
                "id": "play_session",
                "columns": [{"id": "play_session.id"}],
            },
            {
                "id": "member",
                "columns": [{"id": "member.phone"}],
            },
        ],
        access_policy={
            "admin_bypass": False,
            "table_acl": {"play_session": ["id"]},
        },
    )

    assert [item["term_key"] for item in matches] == ["play_count"]


def test_token_usage_is_aggregated_without_exposing_model_metadata():
    callback = UsageMetadataCallbackHandler()
    callback.usage_metadata["deepseek-chat"] = {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
    }

    assert _summarize_token_usage(callback) == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "available": True,
    }
    assert _public_graph_event({"type": "context", "system_prompt": "secret"}) == {
        "type": "context"
    }


def test_catalog_acl_removes_unauthorized_tables_before_plan_and_prompt():
    tables, metrics, relationships = _apply_catalog_acl(
        [
            {"id": "play_session", "columns": [{"id": "play_session.id"}]},
            {"id": "member", "columns": [{"id": "member.phone"}]},
        ],
        [
            {"id": "play_count", "relevant_columns": ["play_session.id"]},
            {"id": "phone_count", "relevant_columns": ["member.phone"]},
        ],
        [
            {
                "source_table": "play_session",
                "source_column": "user_id",
                "target_table": "member",
                "target_column": "id",
            }
        ],
        {
            "admin_bypass": False,
            "table_acl": {"play_session": ["id"]},
        },
    )

    assert [item["id"] for item in tables] == ["play_session"]
    assert [item["id"] for item in metrics] == ["play_count"]
    assert relationships == []
