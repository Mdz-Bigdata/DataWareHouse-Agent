from __future__ import annotations

import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.semantic_term import SemanticTerm
from app.models.mysql.semantic_term_mysql import SemanticTermMySQL


class SemanticTermRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, term_id: str) -> SemanticTermMySQL | None:
        return await self.session.get(SemanticTermMySQL, term_id)

    async def list_for_scope(
        self,
        *,
        domain: str,
        datasource: str,
        status: str | None = None,
    ) -> list[SemanticTermMySQL]:
        statement = select(SemanticTermMySQL).where(
            SemanticTermMySQL.domain == domain,
            SemanticTermMySQL.datasource == datasource,
        )
        if status is not None:
            statement = statement.where(SemanticTermMySQL.status == status)
        result = await self.session.execute(
            statement.order_by(
                SemanticTermMySQL.standard_term,
                SemanticTermMySQL.version.desc(),
            )
        )
        return list(result.scalars().all())

    async def exact_match(self, query: str, *, domain: str, datasource: str) -> list[SemanticTerm]:
        normalized_query = normalize_term_text(query)
        if not normalized_query:
            return []
        rows = await self.list_for_scope(
            domain=domain,
            datasource=datasource,
            status="published",
        )
        return [
            to_entity(row)
            for row in rows
            if normalized_query
            in {
                normalize_term_text(row.standard_term),
                *(normalize_term_text(item) for item in (row.synonyms or [])),
            }
        ]

    async def next_version(self, *, term_key: str, domain: str, datasource: str) -> int:
        result = await self.session.execute(
            select(func.max(SemanticTermMySQL.version)).where(
                SemanticTermMySQL.term_key == term_key,
                SemanticTermMySQL.domain == domain,
                SemanticTermMySQL.datasource == datasource,
            )
        )
        return int(result.scalar_one_or_none() or 0) + 1

    async def add(self, term: SemanticTermMySQL) -> None:
        self.session.add(term)
        await self.session.flush()

    async def disable_other_published_revisions(
        self, term: SemanticTermMySQL
    ) -> list[SemanticTermMySQL]:
        result = await self.session.execute(
            select(SemanticTermMySQL).where(
                SemanticTermMySQL.term_key == term.term_key,
                SemanticTermMySQL.domain == term.domain,
                SemanticTermMySQL.datasource == term.datasource,
                SemanticTermMySQL.status == "published",
                SemanticTermMySQL.id != term.id,
            )
        )
        disabled = list(result.scalars().all())
        for current in disabled:
            current.status = "disabled"
        return disabled


def normalize_term_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def to_entity(row: SemanticTermMySQL) -> SemanticTerm:
    return SemanticTerm(
        id=row.id,
        term_key=row.term_key,
        standard_term=row.standard_term,
        synonyms=list(row.synonyms or []),
        description=row.description or "",
        bindings=list(row.bindings or []),
        domain=row.domain,
        datasource=row.datasource,
        status=row.status,
        version=row.version,
        created_by=row.created_by,
    )
