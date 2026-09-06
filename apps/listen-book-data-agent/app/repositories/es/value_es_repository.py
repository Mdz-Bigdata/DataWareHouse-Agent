from dataclasses import asdict

from elastic_transport import ObjectApiResponse
from elasticsearch import AsyncElasticsearch, NotFoundError

from app.conf.app_config import app_config

from app.entities.value_info import ValueInfo


class ValueInfoRepository:
    alias_name = f"{app_config.es.index_name}-value"
    es_index_mappings = {
        "dynamic": False,
        "properties": {
            "id": {"type": "keyword"},
            "value": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_max_word"},
            "column_id": {"type": "keyword"},
            "build_id": {"type": "keyword"},
        }
    }

    def __init__(
        self,
        client: AsyncElasticsearch,
        index_name: str | None = None,
    ):
        self.client = client
        self.index_name = index_name or self.alias_name

    async def ensure_index(self):
        if not await self.client.indices.exists(index=self.index_name):
            await self.client.indices.create(
                index=self.index_name,
                mappings=self.es_index_mappings
            )

    async def get_alias_target(self, alias_name: str | None = None) -> str | None:
        alias = alias_name or self.alias_name
        try:
            response = await self.client.indices.get_alias(name=alias)
        except NotFoundError:
            return None
        return next(iter(response.keys()), None)

    async def set_alias(
        self,
        target_index: str | None = None,
        alias_name: str | None = None,
    ) -> None:
        alias = alias_name or self.alias_name
        target = self.index_name if target_index is None else target_index
        try:
            current_indexes = list(
                (await self.client.indices.get_alias(name=alias)).keys()
            )
        except NotFoundError:
            current_indexes = []
        if current_indexes == ([target] if target else []):
            return
        actions = [
            {"remove": {"index": index, "alias": alias}}
            for index in current_indexes
        ]
        if target:
            actions.append({"add": {"index": target, "alias": alias}})
        if actions:
            await self.client.indices.update_aliases(actions=actions)

    async def delete_index(self) -> None:
        if await self.client.indices.exists(index=self.index_name):
            await self.client.indices.delete(index=self.index_name)

    async def upsert(self, value_infos: list[ValueInfo], batch_size: int = 100):
        for i in range(0, len(value_infos), batch_size):
            operations = []
            values = value_infos[i:i + batch_size]
            for value_info in values:
                operations.append({
                    "index": {
                        "_index": self.index_name, "_id": value_info.id
                    }
                })
                operations.append(asdict(value_info))
            await self.client.bulk(operations=operations)
        await self.client.indices.refresh(index=self.index_name)

    async def search(self, keyword: str, score_threshold: float = 0.6, limit: int = 10) -> list[ValueInfo]:
        result:ObjectApiResponse = await self.client.search(
            index=self.index_name,
            query={
                "match": {
                    "value": keyword
                }
            },
            min_score=score_threshold,
            size=limit
        )
        #解析ES检索结果
        return [ValueInfo(**hit["_source"]) for hit in result["hits"]["hits"]]
