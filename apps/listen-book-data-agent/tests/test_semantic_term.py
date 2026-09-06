from __future__ import annotations

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.entities.semantic_term import SemanticTerm
from app.models.mysql.base import Base
from app.repositories.mysql.semantic_term_repository import SemanticTermRepository
from app.repositories.qdrant.semantic_term_qdrant_repository import (
    SemanticTermQdrantRepository,
)
from app.services.semantic_term_service import SemanticTermService


class FakeEmbeddingClient:
    async def aembed_query(self, text: str) -> list[float]:
        return [float(len(text)), 0.2, 0.3]


class FakeTermVectorRepository:
    def __init__(self):
        self.stored: dict[str, SemanticTerm] = {}
        self.search_calls: list[dict] = []

    async def upsert(self, term: SemanticTerm, embedding: list[float]) -> None:
        self.stored[term.id] = term

    async def search(self, embedding: list[float], **kwargs) -> list[SemanticTerm]:
        self.search_calls.append(kwargs)
        return [
            term
            for term in self.stored.values()
            if term.status == "published"
            and term.domain == kwargs["domain"]
            and term.datasource == kwargs["datasource"]
        ][: kwargs["limit"]]


async def _with_service(callback):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vectors = FakeTermVectorRepository()
    async with factory() as session:
        service = SemanticTermService(
            SemanticTermRepository(session), vectors, FakeEmbeddingClient()
        )
        await callback(service, vectors)
    await engine.dispose()


def test_versioned_term_publish_exact_match_and_scope():
    async def scenario(service, vectors):
        first = await service.create_draft(
            term_key="active_users",
            standard_term="活跃用户",
            synonyms=["活跃会员", " 活跃会员 "],
            description="发生过有效播放的用户",
            bindings=[{"kind": "metric", "semantic_id": "active_user_count"}],
            domain="audio",
            datasource="audio_full",
            created_by="admin-1",
        )
        second = await service.create_draft(
            term_key="active_users",
            standard_term="活跃听众",
            synonyms=["活跃用户"],
            description="新版口径",
            bindings=[{"kind": "metric", "semantic_id": "active_listener_count"}],
            domain="audio",
            datasource="audio_full",
            created_by="admin-1",
        )
        assert first.version == 1
        assert second.version == 2

        await service.publish(first.id)
        published = await service.publish(second.id)
        assert published.status == "published"
        assert vectors.stored[first.id].status == "disabled"

        matches = await service.exact_match(" 活跃用户 ", domain="audio", datasource="audio_full")
        assert [item.id for item in matches] == [second.id]
        assert await service.exact_match("活跃用户", domain="other", datasource="audio_full") == []

    asyncio.run(_with_service(scenario))


def test_vector_search_is_domain_datasource_scoped_and_published_only():
    async def scenario(service, vectors):
        term = await service.create_draft(
            term_key="completion_rate",
            standard_term="完播率",
            synonyms=["播放完成率"],
            description="完整播放次数占比",
            bindings=[{"kind": "metric", "semantic_id": "completion_rate"}],
            domain="audio",
            datasource="audio_full",
            created_by="admin-1",
        )
        await service.publish(term.id)
        results = await service.vector_search(
            "哪些内容完播表现好",
            domain="audio",
            datasource="audio_full",
            limit=3,
        )

        assert [item.id for item in results] == [term.id]
        assert vectors.search_calls == [
            {
                "domain": "audio",
                "datasource": "audio_full",
                "score_threshold": 0.65,
                "limit": 3,
            }
        ]

    asyncio.run(_with_service(scenario))


def test_term_binding_rejects_raw_sql_or_unknown_fields():
    async def scenario(service, _vectors):
        with pytest.raises(ValueError, match="只允许"):
            await service.create_draft(
                term_key="unsafe_term",
                standard_term="不安全术语",
                synonyms=[],
                description="",
                bindings=[
                    {
                        "kind": "metric",
                        "semantic_id": "metric_1",
                        "raw_sql": "DROP TABLE users",
                    }
                ],
                domain="audio",
                datasource="audio_full",
                created_by="admin-1",
            )

    asyncio.run(_with_service(scenario))


def test_qdrant_search_applies_lifecycle_and_scope_filter():
    term = SemanticTerm(
        id="3fca0a26-31b4-4614-950a-64ad48eaef66",
        term_key="active_users",
        standard_term="活跃用户",
        domain="audio",
        datasource="audio_full",
        status="published",
    )

    class FakeClient:
        def __init__(self):
            self.kwargs = None

        async def query_points(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(points=[SimpleNamespace(payload=asdict(term))])

    async def scenario():
        client = FakeClient()
        repository = SemanticTermQdrantRepository(client)
        results = await repository.search([0.1, 0.2], domain="audio", datasource="audio_full")
        assert results == [term]
        conditions = client.kwargs["query_filter"].must
        assert {condition.key: condition.match.value for condition in conditions} == {
            "domain": "audio",
            "datasource": "audio_full",
            "status": "published",
        }

    asyncio.run(scenario())
