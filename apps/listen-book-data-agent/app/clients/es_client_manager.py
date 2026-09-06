import asyncio
import uuid
from typing import Optional

from elasticsearch import AsyncElasticsearch

from app.conf.app_config import ESConfig, app_config
from app.core.context import set_request_id
from app.core.log import logger


class ESClientManager:

    def __init__(self, config: ESConfig):
        self.config = config
        self.client: Optional[AsyncElasticsearch] = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        self.client = AsyncElasticsearch(
            hosts=[self._get_url()]
        )

    async def close(self):
        if self.client:
            await self.client.close()


# 对外提供es客户端管理器对象
es_client_manager = ESClientManager(app_config.es)

if __name__ == '__main__':
    # 1.初始化客户端
    es_client_manager.init()
    client = es_client_manager.client


    # 2.基于client对象 操作索引库、文档

    async def test_index():
        flag = await client.indices.exists(index="test_index_2")
        print("判断是否存在：", flag)
        if not flag:
            response = await client.indices.create(
                index="test_index_2",
                mappings={
                    "properties": {
                        "price": {
                            "type": "integer"
                        },
                        "brand": {
                            "type": "keyword"
                        },
                        "image": {
                            "type": "keyword",
                            "index": False
                        },
                        "title": {
                            "type": "text",
                            "analyzer": "ik_max_word"
                        }
                    }
                }
            )
            print(response)
        # 关闭客户端
        await es_client_manager.close()


    # asyncio.run(test_index())

    async def test_doct():
        result = await client.index(
            index="test_index_2",
            id="1",
            document={
                "title": "苹果IPhone16PamMax 1TB 黑色 降价促销",
                "brand": "Apple",
                "image": "http://xxxx.com/apple.png",
                "price": 7999
            }
        )
        print(result)
        await es_client_manager.close()


    # asyncio.run(test_doct())

    async def test_docs_bulk():
        operations = []
        operations.append({
            "index": {
                "_index": "test_index_2", "_id": "2"
            }
        })
        operations.append({
            "title": "苹果IPhone17PamMax 1TB 黑色 降价促销",
            "brand": "Apple",
            "image": "http://xxxx.com/apple.png",
            "price": 9999
        })
        operations.append({
            "index": {
                "_index": "test_index_2", "_id":"3"
            }
        })
        operations.append({
            "title": "苹果IPhone17PamMax 2TB 黑色 降价促销",
            "brand": "Apple",
            "image": "http://xxxx.com/apple.png",
            "price": 9999
        })
        result = await client.bulk(
            operations=operations
        )
        set_request_id(str(uuid.uuid4()))
        logger.debug("debug")
        logger.info(result)
        await es_client_manager.close()
    asyncio.run(test_docs_bulk())
