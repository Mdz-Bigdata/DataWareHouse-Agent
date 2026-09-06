import pytest
import json
from httpx import AsyncClient
from sqlalchemy import select, delete
from app.models.audit import AgentExecutionHistory, AgentExecutionTrace
from app.models.chatbi_example import ChatBIExample
from app.core.orm import AsyncSessionLocal
from unittest.mock import AsyncMock, patch

@pytest.fixture
async def setup_feedback_data():
    """准备测试数据：History + Trace (execute_sql_query)"""
    trace_id = "trace-test-chatbi-feedback-001"
    agent_id = "agent-test-id"
    
    async with AsyncSessionLocal() as session:
        # 1. 清理环境
        await session.execute(delete(ChatBIExample).where(ChatBIExample.trace_id == trace_id))
        await session.execute(delete(AgentExecutionHistory).where(AgentExecutionHistory.trace_id == trace_id))
        await session.execute(delete(AgentExecutionTrace).where(AgentExecutionTrace.trace_id == trace_id))
        
        # 2. 插入执行历史
        history = AgentExecutionHistory(
            agent_id=agent_id,
            trace_id=trace_id,
            query="查询上月销售额",
            summary="上月销售额为 100 万。",
            status="success"
        )
        session.add(history)
        
        # 3. 插入 SQL Trace (模拟成功的执行步骤)
        trace = AgentExecutionTrace(
            trace_id=trace_id,
            step_number=1,
            event_type="tool_result",
            tool_name="execute_sql_query",
            tool_input=json.dumps({
                "sql": "SELECT sum(amount) FROM orders WHERE date = 'last_month'",
                "dataset_name": "sales_dataset"
            }),
            status="success"
        )
        session.add(trace)
        
        await session.commit()
    
    return trace_id

@pytest.mark.asyncio
async def test_feedback_collection_and_example_creation(client: AsyncClient, setup_feedback_data, admin_api_key):
    """验证点赞反馈后，是否自动创建了 ChatBI 经验库条目。"""
    trace_id = setup_feedback_data
    
    payload = {
        "trace_id": trace_id,
        "feedback": "up",
        "user_id": "test-user-001"
    }
    
    headers = {"X-API-Key": admin_api_key}
    response = await client.post("/api/portal/chat/feedback", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["code"] == 200
    
    # 检查数据库中是否生成了经验记录
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(ChatBIExample).where(ChatBIExample.trace_id == trace_id))
        example = res.scalars().first()
        
        assert example is not None
        assert example.feedback_type == "up"
        assert example.status == "pending"
        assert example.sql_text == "SELECT sum(amount) FROM orders WHERE date = 'last_month'"
        assert example.user_query == "查询上月销售额"

@pytest.mark.asyncio
async def test_audit_and_sync_trigger(client: AsyncClient, setup_feedback_data, admin_api_key):
    """验证管理端审核经验并手动触发同步。"""
    trace_id = setup_feedback_data
    
    headers = {"X-API-Key": admin_api_key}
    # 首先触发点赞创建经验
    await client.post("/api/portal/chat/feedback", json={"trace_id": trace_id, "feedback": "up"}, headers=headers)
    
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(ChatBIExample).where(ChatBIExample.trace_id == trace_id))
        example = res.scalars().first()
        example_id = example.id

    # 1. 模拟审核通过
    audit_payload = {"id": example_id, "status": "approved"}
    audit_resp = await client.post("/api/portal/chatbi-examples/audit", json=audit_payload, headers=headers)
    assert audit_resp.status_code == 200
    
    async with AsyncSessionLocal() as session:
        res_after = await session.execute(select(ChatBIExample).where(ChatBIExample.id == example_id))
        example_after = res_after.scalars().first()
        assert example_after.status == "approved"

    # 2. 模拟手动触发同步
    with patch("app.services.ai.ragflow_client.RagFlowClient.upload_document", new_callable=AsyncMock) as mock_upload:
        mock_upload.return_value = {"id": "rag-doc-123"}
        with patch("app.services.ai.ragflow_client.RagFlowClient.parse_documents", new_callable=AsyncMock):
            # 模拟系统配置已设置
            with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
                mock_config.return_value = "target-dataset-id"
                
                sync_resp = await client.post(f"/api/portal/chatbi-examples/sync/{example_id}", headers=headers)
                assert sync_resp.status_code == 200
                
                from app.services.chatbi_example_service import ExampleService
                await ExampleService.sync_to_ragflow(example_id)
                
                async with AsyncSessionLocal() as session:
                    res_final = await session.execute(select(ChatBIExample).where(ChatBIExample.id == example_id))
                    final_example = res_final.scalars().first()
                    assert final_example.rag_sync_status == "synced"
                    assert final_example.rag_doc_id == "rag-doc-123"

@pytest.mark.asyncio
async def test_feedback_idempotency(client: AsyncClient, setup_feedback_data, admin_api_key):
    """验证反馈操作的幂等性：从点赞改为点踩。"""
    trace_id = setup_feedback_data
    headers = {"X-API-Key": admin_api_key}
    
    await client.post("/api/portal/chat/feedback", json={"trace_id": trace_id, "feedback": "up"}, headers=headers)
    await client.post("/api/portal/chat/feedback", json={"trace_id": trace_id, "feedback": "down"}, headers=headers)
    
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(ChatBIExample).where(ChatBIExample.trace_id == trace_id))
        example = res.scalars().first()
        assert example.feedback_type == "down"
        # 逻辑已修改：不论点赞还是点踩，初始均为待审核状态
        assert example.status == "pending"
