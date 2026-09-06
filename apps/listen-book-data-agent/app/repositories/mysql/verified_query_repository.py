from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.verified_query import QuerySetVersion, VerifiedQueryRevision
from app.models.mysql.semantic_release_mysql import SemanticReleaseMySQL
from app.models.mysql.verified_query_mysql import (
    QuerySetCaseMySQL,
    QuerySetVersionMySQL,
    VerifiedQueryRevisionMySQL,
)
from app.repositories.mysql.semantic_release_repository import SemanticReleaseRepository


class VerifiedQueryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_revision(self, revision_id: str) -> VerifiedQueryRevisionMySQL | None:
        return await self.session.get(VerifiedQueryRevisionMySQL, revision_id)

    async def next_revision(self, *, case_key: str, domain: str, datasource: str) -> int:
        result = await self.session.execute(
            select(func.max(VerifiedQueryRevisionMySQL.revision)).where(
                VerifiedQueryRevisionMySQL.case_key == case_key,
                VerifiedQueryRevisionMySQL.domain == domain,
                VerifiedQueryRevisionMySQL.datasource == datasource,
            )
        )
        return int(result.scalar_one_or_none() or 0) + 1

    async def add_revision(self, row: VerifiedQueryRevisionMySQL) -> None:
        self.session.add(row)
        await self.session.flush()

    async def list_revisions(
        self,
        *,
        domain: str,
        datasource: str,
        lifecycle: str | None = None,
    ) -> list[VerifiedQueryRevisionMySQL]:
        statement = select(VerifiedQueryRevisionMySQL).where(
            VerifiedQueryRevisionMySQL.domain == domain,
            VerifiedQueryRevisionMySQL.datasource == datasource,
        )
        if lifecycle is not None:
            statement = statement.where(VerifiedQueryRevisionMySQL.lifecycle == lifecycle)
        result = await self.session.execute(
            statement.order_by(
                VerifiedQueryRevisionMySQL.case_key,
                VerifiedQueryRevisionMySQL.revision.desc(),
            )
        )
        return list(result.scalars().all())


class QuerySetRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_version(self, *, domain: str, datasource: str) -> int:
        result = await self.session.execute(
            select(func.max(QuerySetVersionMySQL.version)).where(
                QuerySetVersionMySQL.domain == domain,
                QuerySetVersionMySQL.datasource == datasource,
            )
        )
        return int(result.scalar_one_or_none() or 0) + 1

    async def add_snapshot(
        self,
        version: QuerySetVersionMySQL,
        cases: list[QuerySetCaseMySQL],
    ) -> None:
        self.session.add(version)
        self.session.add_all(cases)
        await self.session.flush()

    async def get_version(self, query_set_id: str) -> QuerySetVersionMySQL | None:
        return await self.session.get(QuerySetVersionMySQL, query_set_id)

    async def get_by_content_hash(self, content_hash: str) -> QuerySetVersionMySQL | None:
        result = await self.session.execute(
            select(QuerySetVersionMySQL).where(QuerySetVersionMySQL.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def get_latest_published(
        self,
        *,
        domain: str,
        datasource: str,
    ) -> QuerySetVersionMySQL | None:
        result = await self.session.execute(
            select(QuerySetVersionMySQL)
            .where(
                QuerySetVersionMySQL.domain == domain,
                QuerySetVersionMySQL.datasource == datasource,
                QuerySetVersionMySQL.status == "published",
            )
            .order_by(QuerySetVersionMySQL.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_effective_published(
        self,
        *,
        domain: str,
        datasource: str,
    ) -> tuple[QuerySetVersionMySQL | None, SemanticReleaseMySQL | None]:
        """Resolve the release-pinned Query Set, with legacy fallback before v1."""

        release = await SemanticReleaseRepository(self.session).get_active_release(
            domain=domain,
            datasource=datasource,
        )
        if release is None:
            return (
                await self.get_latest_published(
                    domain=domain,
                    datasource=datasource,
                ),
                None,
            )
        row = await self.get_version(release.query_set_id)
        if row is None or row.status != "published":
            return None, release
        return row, release

    async def list_versions(self, *, domain: str, datasource: str) -> list[QuerySetVersionMySQL]:
        result = await self.session.execute(
            select(QuerySetVersionMySQL)
            .where(
                QuerySetVersionMySQL.domain == domain,
                QuerySetVersionMySQL.datasource == datasource,
            )
            .order_by(QuerySetVersionMySQL.version.desc())
        )
        return list(result.scalars().all())

    async def list_cases(self, query_set_id: str) -> list[QuerySetCaseMySQL]:
        result = await self.session.execute(
            select(QuerySetCaseMySQL)
            .where(QuerySetCaseMySQL.query_set_id == query_set_id)
            .order_by(QuerySetCaseMySQL.sequence)
        )
        return list(result.scalars().all())


def revision_to_entity(row: VerifiedQueryRevisionMySQL) -> VerifiedQueryRevision:
    return VerifiedQueryRevision(
        id=row.id,
        case_key=row.case_key,
        revision=row.revision,
        domain=row.domain,
        datasource=row.datasource,
        question=row.question,
        dialect=row.dialect,
        sql_template=row.sql_template,
        parameter_schema=list(row.parameter_schema or []),
        expected_fields=list(row.expected_fields or []),
        expected_metrics=list(row.expected_metrics or []),
        assertions=list(row.assertions or []),
        source_trace_id=row.source_trace_id,
        source=row.source,
        lifecycle=row.lifecycle,
        created_by=row.created_by,
        reviewer_id=row.reviewer_id,
    )


def query_set_to_entity(row: QuerySetVersionMySQL) -> QuerySetVersion:
    return QuerySetVersion(
        id=row.id,
        version=row.version,
        version_label=row.version_label,
        domain=row.domain,
        datasource=row.datasource,
        content_hash=row.content_hash,
        manifest=list(row.manifest or []),
        status=row.status,
        created_by=row.created_by,
        reviewer_id=row.reviewer_id,
    )
