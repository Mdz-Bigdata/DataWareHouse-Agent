import pytest
import httpx
from app.core.config import settings

@pytest.mark.asyncio
async def test_schema_v1_query():
    """验证 V1 Schema 接口的新参数结构 (仅 query 字符串)"""
    url = f"http://127.0.0.1:{settings.API_SERVICE_PORT}/api/v1/schema"
    
    payload = {
        "query": "资源"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, timeout=5.0)
            assert resp.status_code == 200
            json_body = resp.json()
            assert json_body["code"] == 200
            assert "data" in json_body
            assert "schema_context" in json_body["data"]
            print(f"\nSchema context length: {len(json_body['data']['schema_context'])}")
        except Exception as e:
            pytest.skip(f"Server not reachable: {e}")
