from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.conf.app_config import app_config
from app.conf.meta_config import MetaConfig
from app.core.log import logger
from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo
from app.metadata.schema_catalog import (
    build_domain_metadata,
    load_meta_config,
    parse_mysql_ddl,
    validate_domain_catalog,
)
from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.mysql.verified_query_repository import (
    QuerySetRepository,
    VerifiedQueryRepository,
)
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.repositories.qdrant.verified_query_qdrant_repository import (
    VerifiedQueryQdrantRepository,
)
from app.services.golden_suite_service import (
    GoldenSuiteService,
    GoldenSuiteSubject,
    load_golden_suite,
    require_golden_suite_pass,
)
from app.services.query_set_service import QuerySetService
from app.services.semantic_release_service import (
    SemanticReleaseRecoveryError,
    SemanticReleaseService,
)

T = TypeVar("T")
EMBEDDING_BATCH_SIZE = 4


class MetaKnowledgeService:
    def __init__(
        self,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueInfoRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        column_qdrant_repository: ColumnQdrantRepository,
        dw_mysql_repository: DWMySQlRepository,
        meta_mysql_repository: MetaMySQlRepository,
    ):
        self.dw_mysql_repository = dw_mysql_repository
        self.meta_mysql_repository = meta_mysql_repository
        self.column_qdrant_repository = column_qdrant_repository
        self.embedding_client = embedding_client
        self.value_es_repository = value_es_repository
        self.metric_qdrant_repository = metric_qdrant_repository

    async def build_domain(
        self,
        *,
        ddl_path: Path,
        config_paths: Sequence[Path],
        force: bool = False,
    ) -> str:
        physical = parse_mysql_ddl(ddl_path)
        config = load_meta_config(*config_paths)
        validation = validate_domain_catalog(physical, config)
        golden_suite_path = config_paths[0].parent / "golden_suite.yaml"
        suite = load_golden_suite(golden_suite_path)
        config_hash = self._config_hash(
            ddl_path,
            [*config_paths, golden_suite_path],
        )

        existing = await self.meta_mysql_repository.find_active_build_by_hash(
            config.domain, config_hash
        )
        if existing and not force:
            logger.info("元数据配置未变化，复用活动构建:{}", existing.id)
            return existing.id

        build_id = uuid.uuid4().hex
        suffix = build_id[:12]
        column_collection = f"{self.column_qdrant_repository.alias_name}-{suffix}"
        metric_collection = f"{self.metric_qdrant_repository.alias_name}-{suffix}"
        value_index = f"{self.value_es_repository.alias_name}-{suffix}"

        domain_metadata = build_domain_metadata(physical, config)
        metric_infos = self._metric_infos(config, build_id)
        for table in domain_metadata.tables:
            table.build_id = build_id
        for column in domain_metadata.columns:
            column.build_id = build_id
        for relationship in domain_metadata.relationships:
            relationship.build_id = build_id

        column_repository = ColumnQdrantRepository(
            self.column_qdrant_repository.client,
            column_collection,
        )
        metric_repository = MetricQdrantRepository(
            self.metric_qdrant_repository.client,
            metric_collection,
        )
        value_repository = ValueInfoRepository(
            self.value_es_repository.client,
            value_index,
        )

        await self.meta_mysql_repository.create_knowledge_build(
            build_id=build_id,
            domain=config.domain,
            config_hash=config_hash,
            column_collection=column_collection,
            metric_collection=metric_collection,
            value_index=value_index,
            table_count=validation.table_count,
            column_count=validation.column_count,
            metric_count=validation.metric_count,
            relationship_count=(
                validation.physical_relationship_count
                + validation.virtual_relationship_count
            ),
        )
        await self.meta_mysql_repository.session.commit()

        try:
            value_infos = await self._load_safe_examples(
                domain_metadata.columns, build_id
            )
            await column_repository.ensure_collection()
            await metric_repository.ensure_collection()
            await value_repository.ensure_index()

            indexable_columns = [
                column for column in domain_metadata.columns if not column.sensitive
            ]
            await self._save_vectors(
                indexable_columns,
                build_id=build_id,
                kind="column",
                text_factory=self._column_embedding_text,
                upsert=column_repository.upsert,
            )
            await self._save_vectors(
                metric_infos,
                build_id=build_id,
                kind="metric",
                text_factory=self._metric_embedding_text,
                upsert=metric_repository.upsert,
            )
            if value_infos:
                await value_repository.upsert(value_infos)

            column_metrics = [
                ColumnMetric(
                    column_id=column_id,
                    metric_id=metric.id,
                    build_id=build_id,
                )
                for metric in metric_infos
                for column_id in metric.relevant_columns
            ]
            await self.meta_mysql_repository.save_table_infos(domain_metadata.tables)
            await self.meta_mysql_repository.save_column_infos(domain_metadata.columns)
            await self.meta_mysql_repository.save_relationship_infos(
                domain_metadata.relationships
            )
            await self.meta_mysql_repository.save_metric_infos(metric_infos)
            await self.meta_mysql_repository.save_column_metrics(column_metrics)
            await self.meta_mysql_repository.session.commit()

            active_build_id = await self.meta_mysql_repository.get_active_build_id(
                config.domain
            )
            baseline = None
            if active_build_id is not None:
                baseline = GoldenSuiteSubject(
                    build_id=active_build_id,
                    tables=await self.meta_mysql_repository.list_table_infos(
                        active_build_id
                    ),
                    columns=await self.meta_mysql_repository.list_allowed_column_infos(
                        active_build_id
                    ),
                    metrics=await self.meta_mysql_repository.list_metric_infos(
                        active_build_id
                    ),
                    relationships=await self.meta_mysql_repository.get_all_relationships(
                        active_build_id
                    ),
                    column_repository=self.column_qdrant_repository,
                    metric_repository=self.metric_qdrant_repository,
                )
            candidate = GoldenSuiteSubject(
                build_id=build_id,
                tables=domain_metadata.tables,
                columns=domain_metadata.columns,
                metrics=metric_infos,
                relationships=domain_metadata.relationships,
                column_repository=column_repository,
                metric_repository=metric_repository,
            )
            gate_report = await GoldenSuiteService(self.embedding_client).evaluate(
                suite=suite,
                candidate=candidate,
                baseline=baseline,
            )
            await self.meta_mysql_repository.save_build_validation(gate_report)
            await self.meta_mysql_repository.session.commit()
            require_golden_suite_pass(gate_report)

            verified_vector_repository = VerifiedQueryQdrantRepository(
                self.column_qdrant_repository.client
            )
            query_set = await QuerySetService(
                VerifiedQueryRepository(self.meta_mysql_repository.session),
                QuerySetRepository(self.meta_mysql_repository.session),
                verified_vector_repository,
                self.embedding_client,
            ).ensure_builtin_seed_published(
                config_paths[0].parent / "queries.yaml",
                domain=config.domain,
                datasource=app_config.db_dw.database,
            )

            release = await SemanticReleaseService(
                meta_repository=self.meta_mysql_repository,
                column_repository=self.column_qdrant_repository,
                metric_repository=self.metric_qdrant_repository,
                value_repository=self.value_es_repository,
            ).activate(
                build_id=build_id,
                domain=config.domain,
                datasource=app_config.db_dw.database,
                created_by="internal-system",
                query_set_id=query_set.id,
            )
            logger.info(
                "听书知识库构建并原子发布完成 build_id={} release_id={} "
                "tables={} columns={} metrics={}",
                build_id,
                release.id,
                validation.table_count,
                validation.column_count,
                validation.metric_count,
            )
            return build_id
        except Exception as exc:
            await self.meta_mysql_repository.session.rollback()
            if not isinstance(exc, SemanticReleaseRecoveryError):
                await self._remove_failed_indexes(
                    column_repository,
                    metric_repository,
                    value_repository,
                )
            await self.meta_mysql_repository.mark_build_failed(build_id, str(exc))
            await self.meta_mysql_repository.session.commit()
            logger.exception("听书知识库构建失败 build_id={}", build_id)
            raise

    @staticmethod
    def _config_hash(ddl_path: Path, config_paths: Sequence[Path]) -> str:
        digest = hashlib.sha256()
        for path in (ddl_path, *sorted(config_paths, key=lambda item: str(item))):
            digest.update(str(path.name).encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _metric_infos(config: MetaConfig, build_id: str) -> list[MetricInfo]:
        return [
            MetricInfo(
                id=metric.name,
                name=metric.name,
                description=metric.description,
                relevant_columns=metric.relevant_columns,
                alias=metric.alias,
                formula=metric.formula,
                filters=metric.filters,
                time_column=metric.time_column,
                unit=metric.unit,
                currency_column=metric.currency_column,
                dimensions=metric.dimensions,
                snapshot=metric.snapshot,
                build_id=build_id,
            )
            for metric in config.metrics or []
        ]

    async def _load_safe_examples(
        self,
        columns: list[ColumnInfo],
        build_id: str,
    ) -> list[ValueInfo]:
        value_infos: list[ValueInfo] = []
        for column in columns:
            if not column.sync or column.sensitive:
                continue
            values = await self.dw_mysql_repository.get_column_values(
                column.table_id,
                column.name,
                limit=1000,
            )
            normalized = [str(value) for value in values if value is not None]
            column.examples = normalized[:10]
            for value in normalized:
                value_infos.append(
                    ValueInfo(
                        id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{build_id}:value:{column.id}:{value}",
                            )
                        ),
                        value=value,
                        column_id=column.id,
                        build_id=build_id,
                    )
                )
        return value_infos

    async def _save_vectors(
        self,
        items: list[T],
        *,
        build_id: str,
        kind: str,
        text_factory: Callable[[T], str],
        upsert: Callable[..., Any],
    ) -> None:
        ids: list[str] = []
        embeddings: list[list[float]] = []
        texts = [text_factory(item) for item in items]
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            embeddings.extend(
                await self._embed_with_payload_fallback(
                    texts[start : start + EMBEDDING_BATCH_SIZE]
                )
            )
        for item in items:
            item_id = item.id
            ids.append(
                str(uuid.uuid5(uuid.NAMESPACE_URL, f"{build_id}:{kind}:{item_id}"))
            )
        await upsert(ids, items, embeddings, batch_size=64)

    async def _embed_with_payload_fallback(
        self, texts: list[str]
    ) -> list[list[float]]:
        try:
            return await self.embedding_client.aembed_documents(texts)
        except Exception as exc:
            if getattr(exc, "status", None) != 413 or len(texts) == 1:
                raise
            midpoint = len(texts) // 2
            logger.warning(
                "Embedding 请求体过大，将批次从 {} 拆分为 {} 和 {}",
                len(texts),
                midpoint,
                len(texts) - midpoint,
            )
            left = await self._embed_with_payload_fallback(texts[:midpoint])
            right = await self._embed_with_payload_fallback(texts[midpoint:])
            return left + right

    @staticmethod
    def _column_embedding_text(column: ColumnInfo) -> str:
        return " ".join(
            value
            for value in (
                column.id,
                column.name,
                " ".join(column.alias),
                column.description,
                " ".join(column.enum_values),
            )
            if value
        )

    @staticmethod
    def _metric_embedding_text(metric: MetricInfo) -> str:
        return " ".join(
            (
                metric.name,
                " ".join(metric.alias),
                metric.description,
                metric.formula,
            )
        )

    @staticmethod
    async def _remove_failed_indexes(
        column_repository: ColumnQdrantRepository,
        metric_repository: MetricQdrantRepository,
        value_repository: ValueInfoRepository,
    ) -> None:
        try:
            await column_repository.delete_collection()
        except Exception:
            logger.warning("清理失败字段向量集合失败", exc_info=True)
        try:
            await metric_repository.delete_collection()
        except Exception:
            logger.warning("清理失败指标向量集合失败", exc_info=True)
        try:
            await value_repository.delete_index()
        except Exception:
            logger.warning("清理失败字段值索引失败", exc_info=True)
