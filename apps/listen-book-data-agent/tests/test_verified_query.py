from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.mysql.base import Base
from app.models.mysql.verified_query_mysql import (
    QuerySetCaseMySQL,
    QuerySetVersionMySQL,
)
from app.repositories.mysql.verified_query_repository import (
    QuerySetRepository,
    VerifiedQueryRepository,
)
from app.services.query_set_service import QuerySetService
from app.services.verified_query_service import VerifiedQueryService


async def _with_session(callback):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await callback(session)
    await engine.dispose()


def _revision_kwargs(**overrides):
    values = {
        "case_key": "album_status_count",
        "question": "统计指定状态的专辑数",
        "dialect": "mysql",
        "sql_template": (
            "SELECT album_status, COUNT(*) AS album_count FROM audio_album "
            "WHERE album_status = :p1 GROUP BY album_status"
        ),
        "parameter_schema": [{"name": "p1", "type": "string", "required": True}],
        "expected_fields": ["album_status", "album_count"],
        "expected_metrics": ["album_count"],
        "assertions": [
            {"kind": "row_count", "operator": "gte", "value": 0},
            {"kind": "not_null", "field": "album_count"},
        ],
        "domain": "audio",
        "datasource": "audio_full",
        "source_trace_id": "trace-1",
        "source": "trace",
        "created_by": "admin-1",
    }
    values.update(overrides)
    return values


def test_verified_query_revision_is_versioned_typed_and_reviewed():
    async def scenario(session):
        service = VerifiedQueryService(VerifiedQueryRepository(session))
        first = await service.create_revision(**_revision_kwargs())
        second = await service.create_revision(
            **_revision_kwargs(question="统计某状态下的专辑数量")
        )

        assert first.revision == 1
        assert second.revision == 2
        assert first.lifecycle == "candidate"
        assert first.source_trace_id == "trace-1"
        reviewed = await service.review(second.id, reviewer_id="reviewer-1", approved=True)
        assert reviewed.lifecycle == "reviewed"
        assert reviewed.reviewer_id == "reviewer-1"

    asyncio.run(_with_session(scenario))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"sql_template": "SELECT id FROM audio_album WHERE album_status = 'active'"},
            "命名参数",
        ),
        ({"parameter_schema": []}, "参数定义不一致"),
        (
            {"assertions": [{"kind": "not_null", "field": "missing"}]},
            "未声明字段",
        ),
        (
            {"assertions": [{"kind": "python", "value": "eval(rows)"}]},
            "类型无效",
        ),
        ({"sql_template": "DELETE FROM audio_album"}, "单条 SELECT"),
    ],
)
def test_verified_query_rejects_unsafe_or_untyped_contracts(overrides, message):
    async def scenario(session):
        service = VerifiedQueryService(VerifiedQueryRepository(session))
        with pytest.raises(ValueError, match=message):
            await service.create_revision(**_revision_kwargs(**overrides))

    asyncio.run(_with_session(scenario))


def test_query_set_version_and_membership_are_immutable():
    async def scenario(session):
        query_set_id = str(uuid.uuid4())
        version = QuerySetVersionMySQL(
            id=query_set_id,
            version=1,
            version_label="audio-v1",
            domain="audio",
            datasource="audio_full",
            content_hash="a" * 64,
            manifest=[{"case_key": "album_status_count", "revision": 1}],
            status="published",
            created_by="admin-1",
            reviewer_id="reviewer-1",
        )
        case = QuerySetCaseMySQL(
            query_set_id=query_set_id,
            sequence=1,
            verified_revision_id=str(uuid.uuid4()),
        )
        session.add_all([version, case])
        await session.commit()

        version.version_label = "tampered"
        with pytest.raises(ValueError, match="不可修改"):
            await session.commit()
        await session.rollback()

        stored_case = await session.get(
            QuerySetCaseMySQL,
            {"query_set_id": query_set_id, "sequence": 1},
        )
        stored_case.verified_revision_id = str(uuid.uuid4())
        with pytest.raises(ValueError, match="不可修改"):
            await session.commit()
        await session.rollback()

        stored_version = await session.get(QuerySetVersionMySQL, query_set_id)
        await session.delete(stored_version)
        with pytest.raises(ValueError, match="不可修改"):
            await session.commit()

    asyncio.run(_with_session(scenario))


def test_seed_import_review_publish_and_yaml_export_are_deterministic():
    async def scenario(session):
        verified_repository = VerifiedQueryRepository(session)
        query_set_repository = QuerySetRepository(session)
        query_sets = QuerySetService(verified_repository, query_set_repository)
        seed_path = Path(__file__).parents[1] / "conf" / "domains" / "audio" / "queries.yaml"
        imported = await query_sets.import_seed_file(
            seed_path,
            domain="audio",
            datasource="audio_full",
            created_by="admin-1",
        )
        assert len(imported) == 3
        assert (
            await query_sets.import_seed_file(
                seed_path,
                domain="audio",
                datasource="audio_full",
                created_by="admin-1",
            )
            == []
        )

        verified = VerifiedQueryService(verified_repository)
        for revision_id in imported:
            await verified.review(revision_id, reviewer_id="reviewer-1", approved=True)
        first = await query_sets.publish(
            domain="audio",
            datasource="audio_full",
            created_by="admin-1",
            reviewer_id="reviewer-1",
        )
        duplicate = await query_sets.publish(
            domain="audio",
            datasource="audio_full",
            created_by="admin-1",
            reviewer_id="reviewer-1",
        )
        exported = await query_sets.export_yaml(first.id)

        assert first.id == duplicate.id
        assert first.version == 1
        assert len(first.manifest) == 3
        assert "schema_version: query-set/v1" in exported
        assert f"content_hash: {first.content_hash}" in exported
        assert "sql_template:" in exported

    asyncio.run(_with_session(scenario))


def test_builtin_seed_bootstraps_first_query_set_idempotently():
    async def scenario(session):
        service = QuerySetService(
            VerifiedQueryRepository(session),
            QuerySetRepository(session),
        )
        seed_path = Path(__file__).parents[1] / "conf" / "domains" / "audio" / "queries.yaml"

        first = await service.ensure_builtin_seed_published(
            seed_path,
            domain="audio",
            datasource="audio_full",
        )
        second = await service.ensure_builtin_seed_published(
            seed_path,
            domain="audio",
            datasource="audio_full",
        )

        assert first.id == second.id
        assert first.version == 1
        assert first.created_by == "internal-system"
        assert len(first.manifest) == 3

    asyncio.run(_with_session(scenario))
