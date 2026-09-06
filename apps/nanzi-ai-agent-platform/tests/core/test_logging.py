import pytest
from httpx import AsyncClient
from sqlalchemy import text
from app.services.audit_service import AuditService
from app.core.orm import AsyncSessionLocal

@pytest.mark.asyncio
async def test_access_log_recording(client: AsyncClient, valid_api_key: str):
    """验证请求会被记录到 MySQL ai_agent_access_logs 表"""
    
    # 1. 发起请求
    response = await client.get(
        "/api/portal/auth/me",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    
    # 验证响应头中包含 X-Trace-Id
    assert "X-Trace-Id" in response.headers
    trace_id = response.headers["X-Trace-Id"]
    
    # 2. 验证数据库 (Flush async queue first)
    await AuditService.flush()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT id, user_name, status_code, trace_id FROM ai_agent_access_logs WHERE trace_id = :trace_id"),
            {"trace_id": trace_id}
        )
        row = result.fetchone()
        
        assert row is not None
        assert row[3] == trace_id
        assert row[2] == 200
        # user_name should be present if authenticated
        assert row[1] is not None

@pytest.mark.asyncio
async def test_access_log_unauthorized(client: AsyncClient):
    """验证未授权请求也会记录（user_id 为空）"""
    response = await client.get("/api/portal/auth/me")
    assert response.status_code == 401
    
    trace_id = response.headers.get("X-Trace-Id")
    assert trace_id is not None
    
    # 2. 验证数据库
    await AuditService.flush()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT id, user_name, status_code FROM ai_agent_access_logs WHERE trace_id = :trace_id"),
            {"trace_id": trace_id}
        )
        row = result.fetchone()
        
        assert row is not None
        assert row[2] == 401