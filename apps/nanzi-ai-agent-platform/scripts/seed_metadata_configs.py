import asyncio
import logging
from app.services.config_service import ConfigService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def seed_metadata_configs():
    configs = [
        {
            "key": "metadata_provider",
            "value": "local",
            "description": "元数据提供者类型 (local: 本地元数据管理, ragflow: RAGFlow 语义检索)",
            "category": "metadata",
            "is_secret": False
        },
        {
            "key": "ragflow_api_url",
            "value": "http://your-ragflow-url:8001",
            "description": "RAGFlow 服务地址",
            "category": "metadata",
            "is_secret": False
        },
        {
            "key": "ragflow_api_key",
            "value": "",
            "description": "RAGFlow API Key",
            "category": "metadata",
            "is_secret": True
        }
    ]
    
    logger.info("Starting to seed metadata configuration keys...")
    for item in configs:
        await ConfigService.set_config(
            key=item["key"],
            value=item["value"],
            description=item["description"],
            category=item["category"],
            is_secret=item["is_secret"]
        )
        logger.info(f"Seeded config: {item['key']}")
    
    logger.info("Metadata configuration seeding completed!")

if __name__ == "__main__":
    asyncio.run(seed_metadata_configs())
