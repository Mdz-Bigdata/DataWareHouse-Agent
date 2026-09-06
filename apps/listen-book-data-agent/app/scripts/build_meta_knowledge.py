from __future__ import annotations

import asyncio
from argparse import ArgumentParser
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.log import logger
from app.models import mysql as mysql_models
from app.models.mysql.base import Base
from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.meta_knowledge_service import MetaKnowledgeService

PROJECT_ROOT = Path(__file__).parents[2]
_REGISTERED_MODELS = mysql_models.__all__


async def _create_meta_schema() -> None:
    if meta_mysql_client_manager.client is None:
        raise RuntimeError("metadata database client is not initialized")
    async with meta_mysql_client_manager.client.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def build(
    *,
    ddl_path: Path,
    config_paths: list[Path],
    force: bool = False,
) -> str:
    logger.info("开始构建听书元数据知识库")
    dw_mysql_client_manager.init_client()
    meta_mysql_client_manager.init_client()
    embedding_client_manager.init_client()
    qdrant_client_manager.init_client()
    es_client_manager.init()

    try:
        await _create_meta_schema()
        if not all(
            (
                dw_mysql_client_manager.session_factory,
                meta_mysql_client_manager.session_factory,
                embedding_client_manager.client,
                qdrant_client_manager.client,
                es_client_manager.client,
            )
        ):
            raise RuntimeError("one or more knowledge-build clients are unavailable")

        async with (
            dw_mysql_client_manager.session_factory() as dw_session,
            meta_mysql_client_manager.session_factory() as meta_session,
        ):
            service = MetaKnowledgeService(
                dw_mysql_repository=DWMySQlRepository(dw_session),
                meta_mysql_repository=MetaMySQlRepository(meta_session),
                embedding_client=embedding_client_manager.client,
                column_qdrant_repository=ColumnQdrantRepository(
                    qdrant_client_manager.client
                ),
                value_es_repository=ValueInfoRepository(es_client_manager.client),
                metric_qdrant_repository=MetricQdrantRepository(
                    qdrant_client_manager.client
                ),
            )
            _dw_session: AsyncSession = dw_session
            _meta_session: AsyncSession = meta_session
            try:
                return await service.build_domain(
                    ddl_path=ddl_path,
                    config_paths=config_paths,
                    force=force,
                )
            except Exception:
                await _dw_session.rollback()
                await _meta_session.rollback()
                raise
    finally:
        await dw_mysql_client_manager.close()
        await meta_mysql_client_manager.close()
        await es_client_manager.close()
        await qdrant_client_manager.close()


def parse_args():
    parser = ArgumentParser(description="Build versioned audiobook knowledge indexes")
    parser.add_argument("--domain", choices=("audio",), default="audio")
    parser.add_argument("--force", action="store_true", help="rebuild unchanged config")
    parser.add_argument(
        "--ddl",
        type=Path,
        default=PROJECT_ROOT / "tools" / "audio_data" / "sql" / "audio.sql",
    )
    parser.add_argument(
        "--config",
        action="append",
        type=Path,
        dest="config_paths",
        help="domain YAML; may be specified multiple times",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_paths = args.config_paths or [
        PROJECT_ROOT / "conf" / "domains" / args.domain / "semantics.yaml",
        PROJECT_ROOT / "conf" / "domains" / args.domain / "relationships.yaml",
        PROJECT_ROOT / "conf" / "domains" / args.domain / "metrics.yaml",
    ]
    build_id = asyncio.run(
        build(
            ddl_path=args.ddl,
            config_paths=config_paths,
            force=args.force,
        )
    )
    print(f"active knowledge build: {build_id}")


if __name__ == "__main__":
    main()
