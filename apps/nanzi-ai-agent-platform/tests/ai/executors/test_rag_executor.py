import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.executors.rag_executor import RAGExecutor
from app.schemas.agent import ChatConfig

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override the global fixture to avoid real DB/Redis connection in unit tests."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield

@pytest.fixture
def rag_config():
    return ChatConfig(
        agent_id="rag-agent-id",
        agent_name="RagAgent",
        agent_version=None,
        model_name="ragflow",
        temperature=0.0,
        system_prompt="",
        tools=[],
        engine_type="RAGFLOW",
        engine_config={"dataset_id": "test_ds"}
    )

# --- Tests ---

@pytest.mark.asyncio
async def test_rag_executor_uses_conversation_id(rag_config):
    """RAGFlow 会话 ID 应与平台 conversation_id 对齐，而非每轮 trace_id。"""
    executor = RAGExecutor(
        agent_config=rag_config,
        trace_id="trace-abc",
        trace_buffer=[],
        conversation_id="conv-xyz",
    )

    async def mock_stream(*args, **kwargs):
        assert kwargs.get("conversation_id") == "conv-xyz"
        yield {"type": "answer", "content": "ok"}

    with patch("app.services.ai.ragflow_client.RagFlowClient.chat_stream", side_effect=mock_stream), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        history = [{"role": "user", "content": "Hi"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)

    assert any(e.get("content") == "ok" for e in events if "content" in e)

@pytest.mark.asyncio
async def test_rag_executor_success(rag_config):
    """测试 RAG 执行器的成功流式输出和引用记录"""
    executor = RAGExecutor(agent_config=rag_config, trace_id="test-rag-1", trace_buffer=[])
    
    # Mock data from RagFlowClient
    async def mock_stream(*args, **kwargs):
        yield {"type": "answer", "content": "Hello"}
        yield {"type": "citation", "data": [{"id": 1, "text": "Source 1"}]}
        yield {"type": "answer", "content": " world"}
        
    with patch("app.services.ai.ragflow_client.RagFlowClient.chat_stream", side_effect=mock_stream):
        history = [{"role": "user", "content": "Tell me something"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        # Verify content
        content = "".join([e["content"] for e in events if "content" in e])
        assert content == "Hello world"
        
        # Verify citation and synthesis logs
        logs = [e for e in events if e.get("type") == "log"]
        # Expected: 
        # 1. 🧠 语义理解 (Success)
        # 2. 🔍 知识库检索 (Pending)
        # 3. ✅ 检索完成 (Success)
        # 4. ✨ 开始生成回复 (Success)
        # 5. 📚 引用来源 (Success)
        assert len(logs) == 5
        assert any("语义理解" in l["title"] for l in logs)
        assert any("知识库检索" in l["title"] for l in logs)
        assert any("检索完成" in l["title"] for l in logs)
        assert any("开始生成回复" in l["title"] for l in logs)
        assert any("关联知识库原文" in l["title"] for l in logs)
        assert "已成功匹配" in next(l["details"] for l in logs if "关联知识库原文" in l["title"])
        
        # Verify citation event
        citation_events = [e for e in events if e.get("type") == "citation"]
        assert len(citation_events) == 1
        assert citation_events[0]["data"][0]["text"] == "Source 1"

@pytest.mark.asyncio
async def test_rag_executor_error_chunk(rag_config):
    """测试 RAGFlow 返回的错误区块"""
    executor = RAGExecutor(agent_config=rag_config, trace_id="test-rag-err", trace_buffer=[])
    
    async def mock_stream_error(*args, **kwargs):
        yield {"type": "error", "content": "Rate limit exceeded"}
        
    with patch("app.services.ai.ragflow_client.RagFlowClient.chat_stream", side_effect=mock_stream_error):
        history = [{"role": "user", "content": "Hi"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        assert any("[RAGFlow Error] Rate limit exceeded" in e.get("content", "") for e in events)

@pytest.mark.asyncio
async def test_rag_executor_retry_logic(rag_config):
    """测试连接失败时的重试逻辑"""
    executor = RAGExecutor(agent_config=rag_config, trace_id="test-rag-retry", trace_buffer=[])
    
    call_count = 0
    async def mock_stream_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Failed to connect")
        yield {"type": "answer", "content": "Success after retry"}
        
    with patch("app.services.ai.ragflow_client.RagFlowClient.chat_stream", side_effect=mock_stream_retry), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
         
        history = [{"role": "user", "content": "Retry test"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        assert call_count == 2
        assert any("Success after retry" in e.get("content", "") for e in events)
        # 1 sleep for UX simulation, 1 sleep for retry backoff
        assert mock_sleep.call_count == 2

@pytest.mark.asyncio
async def test_rag_executor_mid_stream_failure(rag_config):
    """测试流式过程中断失败（不应重试，直接报错）"""
    executor = RAGExecutor(agent_config=rag_config, trace_id="test-rag-mid", trace_buffer=[])
    
    async def mock_stream_mid_fail(*args, **kwargs):
        yield {"type": "answer", "content": "Part 1"}
        raise RuntimeError("Something went wrong mid-stream")
        
    with patch("app.services.ai.ragflow_client.RagFlowClient.chat_stream", side_effect=mock_stream_mid_fail):
        history = [{"role": "user", "content": "Mid stream test"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        content = "".join([e["content"] for e in events if "content" in e])
        assert "Part 1" in content
        assert "知识库连接中断" in content
        # Check that status is set to error in the last chunk
        assert events[-1].get("status") == "error"


@pytest.mark.asyncio
async def test_rag_executor_retries_on_index_error(rag_config):
    """IndexError 等 transient 解析错误在尚未输出内容时应重试。"""
    executor = RAGExecutor(agent_config=rag_config, trace_id="test-rag-index-retry", trace_buffer=[])

    call_count = 0

    async def mock_stream_index_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise IndexError("list index out of range")
        yield {"type": "answer", "content": "Recovered after index error"}

    with patch("app.services.ai.ragflow_client.RagFlowClient.chat_stream", side_effect=mock_stream_index_retry), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Index retry test"}]):
            events.append(chunk)

    assert call_count == 2
    assert any("Recovered after index error" in e.get("content", "") for e in events)
    assert any(
        e.get("type") == "log" and "正在重试" in e.get("title", "")
        for e in events
    )
