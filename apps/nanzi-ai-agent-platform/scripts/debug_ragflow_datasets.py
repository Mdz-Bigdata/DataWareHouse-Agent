import asyncio
import sys
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import httpx

# Add app to path
sys.path.append(os.getcwd())

from app.core.config import settings

async def main():
    print("Connecting to DB...")
    database_uri = f"mysql+aiomysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DB}"
    engine = create_async_engine(database_uri, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("Fetching Config...")
        # Assume sys_config table or similar
        # Based on ConfigService query: SELECT value FROM sys_config WHERE key = :key
        
        async def get_config(key):
            result = await session.execute(text("SELECT value FROM system_configs WHERE `key` = :key"), {"key": key})
            row = result.fetchone()
            return row[0] if row else None

        base_url = await get_config("ragflow_api_url")
        api_key = await get_config("ragflow_api_key")

        if not base_url or not api_key:
            print("Error: RAGFlow config not found in DB.")
            return

        print(f"URL: {base_url}")
        # print(f"Key: {api_key}") 

        url = f"{base_url.rstrip('/')}/api/v1/datasets"
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {"page": 1, "page_size": 100}

        print(f"Requesting: {url}")
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params=params)
            print(f"Status: {resp.status_code}")
            print(f"Raw Response Body:\n{resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
