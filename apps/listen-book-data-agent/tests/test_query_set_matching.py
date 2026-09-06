from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import sqlglot
from pydantic import ValidationError
from sqlglot import exp

from app.agent.graph import route_after_verified_query
from app.api.schemas.query_schema import QuerySchema
from app.entities.verified_query import VerifiedQueryExample
from app.repositories.qdrant.verified_query_qdrant_repository import (
    VerifiedQueryQdrantRepository,
)
from app.services.query_set_match_service import (
    QuerySetMatchService,
    bind_query_template,
)


def _query_set_row():
    return SimpleNamespace(
        id="query-set-1",
        version=3,
        version_label="audio-query-set-v3",
        domain="audio",
        datasource="audio_full",
        content_hash="a" * 64,
        status="published",
        created_by="admin-1",
        reviewer_id="reviewer-1",
        manifest=[
            {
                "revision_id": "revision-1",
                "case_key": "album_count_by_status",
                "revision": 1,
                "question": "统计指定状态的专辑数量",
                "dialect": "mysql",
                "sql_template": (
                    "SELECT album_status, COUNT(*) AS album_count FROM audio_album "
                    "WHERE album_status = :p1 GROUP BY album_status"
                ),
                "parameter_schema": [
                    {"name": "p1", "type": "string", "required": True}
                ],
                "expected_fields": ["album_status", "album_count"],
                "expected_metrics": ["album_count"],
            }
        ],
    )


class FakeQuerySetRepository:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    async def get_latest_published(self, *, domain, datasource):
        self.calls.append((domain, datasource))
        return self.row


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = []

    async def aembed_query(self, text):
        self.calls.append(text)
        return [0.1, 0.2]


class FakeVectorRepository:
    def __init__(self, examples=None):
        self.examples = examples or []
        self.calls = []

    async def search(self, embedding, **scope):
        self.calls.append((embedding, scope))
        return self.examples


def test_exact_published_case_binds_typed_parameters_without_vector_or_llm_path():
    async def scenario():
        mysql = FakeQuerySetRepository(_query_set_row())
        vector = FakeVectorRepository()
        embedding = FakeEmbeddingClient()
        service = QuerySetMatchService(mysql, vector, embedding)

        result = await service.match(
            " 统计指定状态的专辑数量。 ",
            parameters={"p1": "active' OR 1=1 --"},
            domain="audio",
            datasource="audio_full",
            dialect="mysql",
        )

        assert result.exact_example.case_key == "album_count_by_status"
        assert result.exact_sql is not None
        assert result.exact_error is None
        assert embedding.calls == []
        assert vector.calls == []
        expression = sqlglot.parse_one(result.exact_sql, read="mysql")
        predicate = expression.find(exp.EQ)
        assert predicate.right.this == "active' OR 1=1 --"
        assert expression.find(exp.Or) is None

    asyncio.run(scenario())


def test_invalid_exact_parameters_never_execute_template_and_only_retrieve_few_shot():
    async def scenario():
        example = VerifiedQueryExample(
            query_set_id="query-set-1",
            query_set_version=3,
            query_set_hash="a" * 64,
            domain="audio",
            datasource="audio_full",
            revision_id="revision-2",
            case_key="similar_case",
            question="按状态统计专辑",
            dialect="mysql",
            sql_template="SELECT COUNT(*) FROM audio_album",
            score=0.88,
        )
        vector = FakeVectorRepository([example])
        service = QuerySetMatchService(
            FakeQuerySetRepository(_query_set_row()),
            vector,
            FakeEmbeddingClient(),
        )

        result = await service.match(
            "统计指定状态的专辑数量",
            parameters={"p1": 123},
            domain="audio",
            datasource="audio_full",
            dialect="mysql",
        )

        assert result.exact_sql is None
        assert "必须是长度不超过 2000 的 string" in result.exact_error
        assert [item.case_key for item in result.semantic_examples] == ["similar_case"]
        assert vector.calls[0][1] == {
            "query_set_id": "query-set-1",
            "domain": "audio",
            "datasource": "audio_full",
            "dialect": "mysql",
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("schema", "parameters", "message"),
    [
        ([{"name": "p1", "type": "integer", "required": True}], {}, "缺少必填"),
        (
            [{"name": "p1", "type": "integer", "required": True}],
            {"p1": True},
            "必须是 integer",
        ),
        (
            [{"name": "p1", "type": "date", "required": True}],
            {"p1": "2026-02-30"},
            "ISO date",
        ),
        (
            [{"name": "p1", "type": "string", "required": True}],
            {"p1": "ok", "p2": "unknown"},
            "未声明参数",
        ),
    ],
)
def test_template_binding_rejects_missing_unknown_or_wrong_parameter_types(
    schema, parameters, message
):
    with pytest.raises(ValueError, match=message):
        bind_query_template(
            "SELECT id FROM audio_album WHERE id = :p1",
            schema,
            parameters,
            dialect="mysql",
        )


def test_vector_repository_filters_to_current_published_query_set_scope():
    class FakeClient:
        def __init__(self):
            self.kwargs = None

        async def query_points(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(points=[])

    async def scenario():
        client = FakeClient()
        repository = VerifiedQueryQdrantRepository(client)
        assert (
            await repository.search(
                [0.1],
                query_set_id="query-set-3",
                domain="audio",
                datasource="audio_full",
                dialect="mysql",
            )
            == []
        )
        conditions = client.kwargs["query_filter"].must
        assert {(item.key, item.match.value) for item in conditions} == {
            ("query_set_id", "query-set-3"),
            ("domain", "audio"),
            ("datasource", "audio_full"),
            ("dialect", "mysql"),
        }

    asyncio.run(scenario())


def test_exact_match_graph_route_enters_sql_guard_and_api_parameters_are_strict():
    assert (
        route_after_verified_query(
            {"generation_source": "verified_exact", "sql": "SELECT id FROM audio_album"}
        )
        == "validate_sql"
    )
    assert route_after_verified_query({}) == "load_business_rules"
    assert QuerySchema.model_validate({"query": "专辑数"}).parameters == {}
    with pytest.raises(ValidationError):
        QuerySchema.model_validate({"query": "专辑数", "parameters": {"p1": [1]}})
