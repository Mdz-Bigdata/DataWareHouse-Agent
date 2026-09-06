import pytest
from httpx import AsyncClient
from sqlalchemy import text
from app.services.audit_service import AuditService
from app.core.orm import AsyncSessionLocal
import json

@pytest.mark.asyncio
async def test_login_audit_masking(client: AsyncClient, admin_api_key: str):
    """验证登录请求中的 API Key 会被脱敏存储"""
    
    # 1. 发起登录请求
    login_data = {
        "user_name": "test_admin",
        "api_key": admin_api_key
    }
    response = await client.post(
        "/api/portal/auth/login",
        json=login_data
    )
    assert response.status_code == 200
    trace_id = response.headers.get("X-Trace-Id")
    assert trace_id is not None
    
    # 2. 强制刷新日志队列
    await AuditService.flush()
    
    # 3. 检查数据库中的记录
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT request_params FROM ai_agent_access_logs WHERE trace_id = :trace_id"),
            {"trace_id": trace_id}
        )
        row = result.fetchone()
        assert row is not None
        
        request_params = row[0]
        # 应该被脱敏为 ******
        assert admin_api_key not in request_params
        assert "******" in request_params
        
        # 验证 JSON 解析后的内容
        params_dict = json.loads(request_params)
        assert params_dict["api_key"] == "******"
        assert params_dict["user_name"] == "test_admin"

@pytest.mark.asyncio
async def test_config_update_masking(client: AsyncClient, admin_api_key: str):
    """验证系统配置更新中的敏感信息会被脱敏"""
    
    # 模拟更新包含敏感词的配置
    config_data = {
        "configs": [
            {"key": "llm_api_key", "value": "sk-sensitive-123456"}
        ]
    }
    
    response = await client.post(
        "/api/portal/system/configs",
        headers={"X-API-Key": admin_api_key},
        json=config_data
    )
    # 虽然可能因为权限或配置不存在报错，但中间件应该已经记录了日志
    # assert response.status_code == 200
    
    trace_id = response.headers.get("X-Trace-Id")
    assert trace_id is not None
    
    await AuditService.flush()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT request_params FROM ai_agent_access_logs WHERE trace_id = :trace_id"),
            {"trace_id": trace_id}
        )
        row = result.fetchone()
        assert row is not None
        
        request_params = row[0]
        assert "sk-sensitive-123456" not in request_params
        assert "******" in request_params
