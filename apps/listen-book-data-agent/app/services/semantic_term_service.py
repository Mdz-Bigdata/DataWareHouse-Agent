from __future__ import annotations

import re
import uuid

from app.entities.semantic_term import SemanticTerm
from app.models.mysql.semantic_term_mysql import SemanticTermMySQL
from app.repositories.mysql.semantic_term_repository import (
    SemanticTermRepository,
    normalize_term_text,
    to_entity,
)
from app.repositories.qdrant.semantic_term_qdrant_repository import (
    SemanticTermQdrantRepository,
)

_TERM_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_BINDING_KINDS = {"column", "metric", "table", "value"}
_BINDING_KEYS = {"kind", "semantic_id"}


class SemanticTermService:
    def __init__(
        self,
        mysql_repository: SemanticTermRepository,
        qdrant_repository: SemanticTermQdrantRepository,
        embedding_client,
    ):
        self.mysql_repository = mysql_repository
        self.qdrant_repository = qdrant_repository
        self.embedding_client = embedding_client

    async def create_draft(
        self,
        *,
        term_key: str,
        standard_term: str,
        synonyms: list[str],
        description: str,
        bindings: list[dict],
        domain: str,
        datasource: str,
        created_by: str | None,
    ) -> SemanticTerm:
        _validate_term(
            term_key=term_key,
            standard_term=standard_term,
            synonyms=synonyms,
            bindings=bindings,
            domain=domain,
            datasource=datasource,
        )
        version = await self.mysql_repository.next_version(
            term_key=term_key,
            domain=domain,
            datasource=datasource,
        )
        row = SemanticTermMySQL(
            id=str(uuid.uuid4()),
            term_key=term_key,
            standard_term=standard_term.strip(),
            synonyms=_dedupe_text(synonyms),
            description=description.strip(),
            bindings=[dict(binding) for binding in bindings],
            domain=domain.strip(),
            datasource=datasource.strip(),
            status="draft",
            version=version,
            created_by=created_by,
        )
        await self.mysql_repository.add(row)
        await self.mysql_repository.session.commit()
        return to_entity(row)

    async def publish(self, term_id: str) -> SemanticTerm:
        row = await self.mysql_repository.get(term_id)
        if row is None:
            raise LookupError("语义术语不存在")
        if row.status == "disabled":
            raise ValueError("已停用术语不能直接发布")
        row.status = "published"
        disabled_rows = await self.mysql_repository.disable_other_published_revisions(row)
        changed_rows = [row, *disabled_rows]
        try:
            for changed in changed_rows:
                entity = to_entity(changed)
                embedding = await self.embedding_client.aembed_query(
                    semantic_term_embedding_text(entity)
                )
                await self.qdrant_repository.upsert(entity, embedding)
            await self.mysql_repository.session.commit()
        except Exception:
            await self.mysql_repository.session.rollback()
            raise
        return to_entity(row)

    async def disable(self, term_id: str) -> SemanticTerm:
        row = await self.mysql_repository.get(term_id)
        if row is None:
            raise LookupError("语义术语不存在")
        row.status = "disabled"
        try:
            entity = to_entity(row)
            embedding = await self.embedding_client.aembed_query(
                semantic_term_embedding_text(entity)
            )
            await self.qdrant_repository.upsert(entity, embedding)
            await self.mysql_repository.session.commit()
        except Exception:
            await self.mysql_repository.session.rollback()
            raise
        return to_entity(row)

    async def exact_match(self, query: str, *, domain: str, datasource: str) -> list[SemanticTerm]:
        return await self.mysql_repository.exact_match(query, domain=domain, datasource=datasource)

    async def vector_search(
        self,
        query: str,
        *,
        domain: str,
        datasource: str,
        score_threshold: float = 0.65,
        limit: int = 5,
    ) -> list[SemanticTerm]:
        if not normalize_term_text(query):
            return []
        embedding = await self.embedding_client.aembed_query(query)
        return await self.qdrant_repository.search(
            embedding,
            domain=domain,
            datasource=datasource,
            score_threshold=score_threshold,
            limit=limit,
        )


def semantic_term_embedding_text(term: SemanticTerm) -> str:
    return "\n".join(
        part
        for part in (
            term.standard_term,
            "、".join(term.synonyms),
            term.description,
        )
        if part
    )


def _validate_term(
    *,
    term_key: str,
    standard_term: str,
    synonyms: list[str],
    bindings: list[dict],
    domain: str,
    datasource: str,
) -> None:
    if not _TERM_KEY_PATTERN.fullmatch(term_key):
        raise ValueError("术语编码必须使用小写字母、数字和下划线")
    if not standard_term.strip() or len(standard_term) > 128:
        raise ValueError("标准术语不能为空且不能超过 128 个字符")
    if not domain.strip() or not datasource.strip():
        raise ValueError("术语必须指定业务域和数据源")
    if any(not str(item).strip() or len(str(item)) > 128 for item in synonyms):
        raise ValueError("术语同义词不能为空且不能超过 128 个字符")
    for binding in bindings:
        if set(binding) != _BINDING_KEYS:
            raise ValueError("术语绑定只允许 kind 和 semantic_id")
        if binding.get("kind") not in _BINDING_KINDS:
            raise ValueError("术语绑定类型无效")
        semantic_id = str(binding.get("semantic_id", "")).strip()
        if not semantic_id or len(semantic_id) > 160:
            raise ValueError("术语绑定语义标识无效")


def _dedupe_text(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_term_text(value)
        if normalized and normalized not in seen:
            result.append(value.strip())
            seen.add(normalized)
    return result
