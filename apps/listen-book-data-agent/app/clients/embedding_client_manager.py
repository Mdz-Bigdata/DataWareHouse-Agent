import asyncio
from typing import Optional

from app.conf.app_config import app_config
from app.conf.app_config import EmbeddingConfig
from langchain_huggingface.embeddings import HuggingFaceEndpointEmbeddings


class EmbeddingClientManager:
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.client: Optional[HuggingFaceEndpointEmbeddings] = None

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init_client(self):
        self.client = HuggingFaceEndpointEmbeddings(model=self._get_url())

embedding_client_manager = EmbeddingClientManager(app_config.embedding)


if __name__ == '__main__':
    async def test():
        embedding_client_manager.init_client()

        # 对一个文本生成向量
        result = await embedding_client_manager.client.aembed_query("hello world")
        print(result)
        print(len(result))  #[float1,float]
        # 批量向量化
        results = await embedding_client_manager.client.aembed_documents(["aaaa", "bbb"])
        print(results)
        print(len(results))

    asyncio.run(test())