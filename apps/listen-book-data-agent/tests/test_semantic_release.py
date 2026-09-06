from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.mysql.base import Base
from app.models.mysql.business_rule_mysql import BusinessRuleRevisionMySQL
from app.models.mysql.knowledge_build_mysql import (
    ActiveKnowledgeBuildMySQL,
    KnowledgeBuildMySQL,
    KnowledgeBuildValidationMySQL,
)
from app.models.mysql.semantic_release_mysql import (
    ActiveSemanticReleaseMySQL,
    SemanticReleaseMySQL,
)
from app.models.mysql.verified_query_mysql import QuerySetVersionMySQL
from app.repositories.mysql.business_rule_repository import BusinessRuleRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.mysql.verified_query_repository import QuerySetRepository
from app.services.semantic_release_service import (
    SemanticReleaseError,
    SemanticReleaseService,
)

DOMAIN = "audio"
DATASOURCE = "audio_full"


class FakeAliasRepository:
    def __init__(self, current: str, *, fail_target: str | None = None):
        self.current = current
        self.fail_target = fail_target
        self.history: list[str] = []

    async def get_alias_target(self) -> str | None:
        return self.current

    async def set_alias(self, target: str) -> None:
        self.history.append(target)
        if target == self.fail_target:
            raise RuntimeError("alias switch failed")
        self.current = target


@pytest_asyncio.fixture
async def release_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _build(build_id: str, version: int, *, status: str) -> KnowledgeBuildMySQL:
    return KnowledgeBuildMySQL(
        id=build_id,
        domain=DOMAIN,
        status=status,
        config_hash=f"hash-{version}",
        column_collection=f"column-{version}",
        metric_collection=f"metric-{version}",
        value_index=f"value-{version}",
    )


def _validation(build_id: str) -> KnowledgeBuildValidationMySQL:
    return KnowledgeBuildValidationMySQL(
        build_id=build_id,
        suite_version="audio-golden-v1",
        status="passed",
        semantic_accuracy=1,
        baseline_semantic_accuracy=1,
        safety_accuracy=1,
        p95_latency_ms=10,
        baseline_p95_latency_ms=10,
        report={"passed": True},
    )


def _query_set(query_set_id: str, version: int) -> QuerySetVersionMySQL:
    return QuerySetVersionMySQL(
        id=query_set_id,
        version=version,
        version_label=f"audio-query-set-v{version}",
        domain=DOMAIN,
        datasource=DATASOURCE,
        content_hash=f"{version:064x}",
        manifest=[],
        status="published",
        created_by="admin",
        reviewer_id="admin",
    )


def _rule(rule_id: str, version: int, *, status: str) -> BusinessRuleRevisionMySQL:
    return BusinessRuleRevisionMySQL(
        id=rule_id,
        rule_key="play_definition",
        version=version,
        rule_type="metric_constraint",
        content=f"播放规则版本 {version}",
        domain=DOMAIN,
        datasource=DATASOURCE,
        intents=["aggregate"],
        semantic_ids=["play_count"],
        priority=100,
        status=status,
        created_by="admin",
        reviewer_id="admin",
    )


async def _seed(session):
    old_build_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    query_set_id = str(uuid.uuid4())
    rule_id = str(uuid.uuid4())
    session.add_all(
        [
            _build(old_build_id, 0, status="active"),
            _build(candidate_id, 1, status="building"),
            _validation(candidate_id),
            ActiveKnowledgeBuildMySQL(domain=DOMAIN, build_id=old_build_id),
            _query_set(query_set_id, 1),
            _rule(rule_id, 1, status="published"),
        ]
    )
    await session.commit()
    return old_build_id, candidate_id, query_set_id, rule_id


def _service(session, *, metric_fail_target: str | None = None):
    aliases = (
        FakeAliasRepository("column-0"),
        FakeAliasRepository("metric-0", fail_target=metric_fail_target),
        FakeAliasRepository("value-0"),
    )
    return (
        SemanticReleaseService(
            meta_repository=MetaMySQlRepository(session),
            column_repository=aliases[0],
            metric_repository=aliases[1],
            value_repository=aliases[2],
        ),
        aliases,
    )


@pytest.mark.asyncio
async def test_activation_atomically_pins_all_semantic_components(
    release_session_factory,
):
    async with release_session_factory() as session:
        _, candidate_id, query_set_id, rule_id = await _seed(session)
        service, aliases = _service(session)

        release = await service.activate(
            build_id=candidate_id,
            domain=DOMAIN,
            datasource=DATASOURCE,
            created_by="admin",
        )

        assert [alias.current for alias in aliases] == [
            "column-1",
            "metric-1",
            "value-1",
        ]
        active_build = await session.get(ActiveKnowledgeBuildMySQL, DOMAIN)
        active_release = await session.get(
            ActiveSemanticReleaseMySQL,
            {"domain": DOMAIN, "datasource": DATASOURCE},
        )
        assert active_build.build_id == candidate_id
        assert active_release.release_id == release.id
        assert release.query_set_id == query_set_id

        rule_set = await service.release_repository.get_rule_set(
            release.business_rule_set_id
        )
        assert [item["revision_id"] for item in rule_set.manifest] == [rule_id]


@pytest.mark.asyncio
async def test_activation_failure_restores_aliases_and_keeps_old_pointer(
    release_session_factory,
):
    async with release_session_factory() as session:
        old_build_id, candidate_id, _, _ = await _seed(session)
        service, aliases = _service(session, metric_fail_target="metric-1")

        with pytest.raises(SemanticReleaseError, match="已恢复"):
            await service.activate(
                build_id=candidate_id,
                domain=DOMAIN,
                datasource=DATASOURCE,
                created_by="admin",
            )

        assert [alias.current for alias in aliases] == [
            "column-0",
            "metric-0",
            "value-0",
        ]
        active_build = await session.get(ActiveKnowledgeBuildMySQL, DOMAIN)
        assert active_build.build_id == old_build_id
        assert await session.scalar(select(SemanticReleaseMySQL)) is None
        assert (
            await session.get(
                ActiveSemanticReleaseMySQL,
                {"domain": DOMAIN, "datasource": DATASOURCE},
            )
            is None
        )


@pytest.mark.asyncio
async def test_release_pins_query_set_and_rules_after_new_publications(
    release_session_factory,
):
    async with release_session_factory() as session:
        _, candidate_id, query_set_id, rule_id = await _seed(session)
        service, _ = _service(session)
        await service.activate(
            build_id=candidate_id,
            domain=DOMAIN,
            datasource=DATASOURCE,
            created_by="admin",
        )

        second_query_set = _query_set(str(uuid.uuid4()), 2)
        old_rule = await session.get(BusinessRuleRevisionMySQL, rule_id)
        old_rule.status = "disabled"
        second_rule = _rule(str(uuid.uuid4()), 2, status="published")
        session.add_all([second_query_set, second_rule])
        await session.commit()

        effective_query_set, release = await QuerySetRepository(
            session
        ).get_effective_published(domain=DOMAIN, datasource=DATASOURCE)
        effective_rules, _, rule_set = await BusinessRuleRepository(
            session
        ).list_effective_for_scope(domain=DOMAIN, datasource=DATASOURCE)

        assert release is not None
        assert effective_query_set.id == query_set_id
        assert [rule.id for rule in effective_rules] == [rule_id]
        assert rule_set is not None and rule_set.version == 1


@pytest.mark.asyncio
async def test_one_click_rollback_creates_new_auditable_release(
    release_session_factory,
):
    async with release_session_factory() as session:
        _, first_build_id, first_query_set_id, first_rule_id = await _seed(session)
        service, aliases = _service(session)
        first_release = await service.activate(
            build_id=first_build_id,
            domain=DOMAIN,
            datasource=DATASOURCE,
            created_by="admin",
        )

        second_build_id = str(uuid.uuid4())
        second_query_set = _query_set(str(uuid.uuid4()), 2)
        first_rule = await session.get(BusinessRuleRevisionMySQL, first_rule_id)
        first_rule.status = "disabled"
        session.add_all(
            [
                _build(second_build_id, 2, status="building"),
                _validation(second_build_id),
                second_query_set,
                _rule(str(uuid.uuid4()), 2, status="published"),
            ]
        )
        await session.commit()
        second_release = await service.activate(
            build_id=second_build_id,
            domain=DOMAIN,
            datasource=DATASOURCE,
            created_by="admin",
        )
        assert second_release.version == 2

        rollback_release = await service.rollback(
            first_release.id,
            created_by="admin",
        )

        assert rollback_release.version == 3
        assert rollback_release.release_kind == "rollback"
        assert rollback_release.source_release_id == first_release.id
        assert rollback_release.knowledge_build_id == first_build_id
        assert rollback_release.query_set_id == first_query_set_id
        assert [alias.current for alias in aliases] == [
            "column-1",
            "metric-1",
            "value-1",
        ]
        items = await service.list_release_items(
            domain=DOMAIN,
            datasource=DATASOURCE,
        )
        assert [item["version"] for item in items] == [3, 2, 1]
        assert items[0]["active"] is True
