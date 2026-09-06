from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from app.entities.verified_query import QuerySetVersion
from app.models.mysql.verified_query_mysql import (
    QuerySetCaseMySQL,
    QuerySetVersionMySQL,
)
from app.repositories.mysql.verified_query_repository import (
    QuerySetRepository,
    VerifiedQueryRepository,
    query_set_to_entity,
)
from app.repositories.qdrant.verified_query_qdrant_repository import (
    VerifiedQueryQdrantRepository,
)
from app.services.embedding_batch_service import embed_documents_batched
from app.services.query_set_match_service import query_set_examples
from app.services.verified_query_service import VerifiedQueryService


class QuerySetService:
    def __init__(
        self,
        verified_repository: VerifiedQueryRepository,
        query_set_repository: QuerySetRepository,
        vector_repository: VerifiedQueryQdrantRepository | None = None,
        embedding_client=None,
    ):
        self.verified_repository = verified_repository
        self.query_set_repository = query_set_repository
        self.vector_repository = vector_repository
        self.embedding_client = embedding_client

    async def publish(
        self,
        *,
        domain: str,
        datasource: str,
        created_by: str,
        reviewer_id: str,
    ) -> QuerySetVersion:
        reviewed = await self.verified_repository.list_revisions(
            domain=domain,
            datasource=datasource,
            lifecycle="reviewed",
        )
        latest_by_case: dict[str, object] = {}
        for row in reviewed:
            latest_by_case.setdefault(row.case_key, row)
        selected = sorted(latest_by_case.values(), key=lambda row: row.case_key)  # type: ignore[attr-defined]
        if not selected:
            raise ValueError("没有可发布的已审核验证查询")

        manifest = [_manifest_item(row) for row in selected]
        content_hash = _content_hash(manifest, domain=domain, datasource=datasource)
        existing = await self.query_set_repository.get_by_content_hash(content_hash)
        if existing is not None:
            entity = query_set_to_entity(existing)
            await self._index_published_set(entity)
            return entity

        version = await self.query_set_repository.next_version(domain=domain, datasource=datasource)
        query_set_id = str(uuid.uuid4())
        row = QuerySetVersionMySQL(
            id=query_set_id,
            version=version,
            version_label=f"{domain}-query-set-v{version}",
            domain=domain,
            datasource=datasource,
            content_hash=content_hash,
            manifest=manifest,
            status="published",
            created_by=created_by,
            reviewer_id=reviewer_id,
        )
        cases = [
            QuerySetCaseMySQL(
                query_set_id=query_set_id,
                sequence=index,
                verified_revision_id=item.id,
            )
            for index, item in enumerate(selected, 1)
        ]
        await self.query_set_repository.add_snapshot(row, cases)
        entity = query_set_to_entity(row)
        await self._index_published_set(entity)
        await self.query_set_repository.session.commit()
        return entity

    async def ensure_builtin_seed_published(
        self,
        path: Path,
        *,
        domain: str,
        datasource: str,
        actor_id: str = "internal-system",
    ) -> QuerySetVersion:
        """Bootstrap the reviewed Git seed for a first atomic semantic release."""

        existing = await self.query_set_repository.get_latest_published(
            domain=domain,
            datasource=datasource,
        )
        imported = await self.import_seed_file(
            path,
            domain=domain,
            datasource=datasource,
            created_by=actor_id,
        )
        revisions = await self.verified_repository.list_revisions(
            domain=domain,
            datasource=datasource,
        )
        review_time = datetime.now()
        reviewed_seed = False
        for row in revisions:
            if row.source == "seed" and row.lifecycle == "candidate":
                row.lifecycle = "reviewed"
                row.reviewer_id = actor_id
                row.reviewed_at = review_time
                reviewed_seed = True
        await self.query_set_repository.session.commit()
        if self.vector_repository is not None:
            await self.vector_repository.ensure_collection()
        if existing is not None and not imported and not reviewed_seed:
            entity = query_set_to_entity(existing)
            await self._index_published_set(entity)
            return entity
        return await self.publish(
            domain=domain,
            datasource=datasource,
            created_by=actor_id,
            reviewer_id=actor_id,
        )

    async def export_yaml(self, query_set_id: str) -> str:
        row = await self.query_set_repository.get_version(query_set_id)
        if row is None:
            raise LookupError("Query Set 版本不存在")
        document = {
            "schema_version": "query-set/v1",
            "id": row.id,
            "version": row.version,
            "version_label": row.version_label,
            "domain": row.domain,
            "datasource": row.datasource,
            "content_hash": row.content_hash,
            "queries": row.manifest,
        }
        return yaml.safe_dump(document, allow_unicode=True, sort_keys=False)

    async def import_seed_file(
        self,
        path: Path,
        *,
        domain: str,
        datasource: str,
        created_by: str,
    ) -> list[str]:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema_version") != "queries/v1":
            raise ValueError("queries.yaml schema_version 无效")
        cases = document.get("queries")
        if not isinstance(cases, list):
            raise ValueError("queries.yaml 缺少 queries 列表")
        existing_rows = await self.verified_repository.list_revisions(
            domain=domain,
            datasource=datasource,
        )
        existing_signatures = {(row.case_key, row.sql_template.strip()) for row in existing_rows}
        service = VerifiedQueryService(self.verified_repository)
        imported: list[str] = []
        for item in cases:
            if not isinstance(item, dict):
                raise ValueError("queries.yaml 用例必须是对象")
            signature = (str(item.get("case_key", "")), str(item.get("sql_template", "")).strip())
            if signature in existing_signatures:
                continue
            revision = await service.create_revision(
                case_key=signature[0],
                question=str(item.get("question", "")),
                dialect=str(item.get("dialect", "mysql")),
                sql_template=signature[1],
                parameter_schema=list(item.get("parameter_schema") or []),
                expected_fields=list(item.get("expected_fields") or []),
                expected_metrics=list(item.get("expected_metrics") or []),
                assertions=list(item.get("assertions") or []),
                domain=domain,
                datasource=datasource,
                source_trace_id=None,
                source="seed",
                created_by=created_by,
            )
            imported.append(revision.id)
            existing_signatures.add(signature)
        return imported

    async def _index_published_set(self, query_set: QuerySetVersion) -> None:
        if self.vector_repository is None or self.embedding_client is None:
            return
        examples = query_set_examples(query_set)
        embeddings = await embed_documents_batched(
            self.embedding_client,
            [example.question for example in examples]
        )
        await self.vector_repository.upsert_many(examples, embeddings)


def _manifest_item(row) -> dict:
    return {
        "revision_id": row.id,
        "case_key": row.case_key,
        "revision": row.revision,
        "question": row.question,
        "dialect": row.dialect,
        "sql_template": row.sql_template,
        "parameter_schema": list(row.parameter_schema or []),
        "expected_fields": list(row.expected_fields or []),
        "expected_metrics": list(row.expected_metrics or []),
        "assertions": list(row.assertions or []),
        "source_trace_id": row.source_trace_id,
    }


def _content_hash(manifest: list[dict], *, domain: str, datasource: str) -> str:
    canonical = json.dumps(
        {"domain": domain, "datasource": datasource, "manifest": manifest},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
