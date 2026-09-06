import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.agent_service import AgentService
from app.schemas.agent import ChatConfig
from app.schemas.trace import AgentExecutionStep

@pytest.mark.asyncio
async def test_ltm_applied_in_stream_meta():
    """
    测试 LTM 被成功加载并注入 SSE stream meta 块的场景。
    """
    agent_service = AgentService()
    
    # 模拟 agent 相关的数据库配置和初始化
    mock_agent_config = ChatConfig(
        agent_id="test-ltm-agent",
        agent_name="LTMTestAgent",
        agent_display_name="LTM Test Agent",
        model_name="test-model",
        temperature=0.0,
        system_prompt="You are a helper.",
    )
    
    # 模拟 LTM 记忆
    mock_ltm_data = {
        "user_preferred_city": "临港",
        "work_style": "premium"
    }

    # 模拟 _run_chat_turn_stream 中的前置依赖
    # 包括获取配置、检测分类、运行 executor 逻辑
    mock_turn_classification = MagicMock()
    mock_turn_classification.turn_type.value = "chat"
    mock_turn_classification.turn_type = MagicMock() # Mock TurnType enum
    
    # 我们用 patches mock 掉所有耗时或需要真实基础设施的模块
    with patch("app.services.ai.agent_service.AgentConfigProvider.get_config", new_callable=AsyncMock, return_value=mock_agent_config), \
         patch("app.services.ai.agent_service.classify_turn", new_callable=AsyncMock) as mock_classify, \
         patch("app.services.ai.agent_service.should_inject_ltm", return_value=True), \
         patch("app.services.ai.memory_service.ltm_service.fetch_memory", new_callable=AsyncMock, return_value=mock_ltm_data), \
         patch("app.services.ai.agent_service.AssistantExecutor", autospec=True) as mock_executor_cls, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock, return_value="test-model"), \
         patch("app.services.ai.agent_service.logger") as mock_logger:
        
        # 模拟 turn_classification 返回
        mock_classify.return_value = mock_turn_classification
        
        # 模拟 executor 的流式生成
        mock_executor_instance = AsyncMock()
        mock_executor_instance.execute.return_value = (e for e in [{"content": "hello"}])
        mock_executor_cls.return_value = mock_executor_instance
        
        # 调用 _run_chat_turn_stream 
        stream = agent_service._run_chat_turn_stream(
            messages=[{"role": "user", "content": "你好"}],
            user_query="你好",
            agent_id="test-ltm-agent",
            agent_name="LTMTestAgent",
            version_id=None,
            conversation_id="test-conv-id",
            user_info={"user_id": "test_user_99"},
            api_key=None,
            enable_multi_agent=False,
            debug_options=None, # 测试正常开启 LTM
            permission_options=None,
            knowledge_dataset_ids=None,
            trace_id="test-trace-id",
            trace_buffer=[],
            start_time=1.0,
        )
        
        events = []
        async for chunk in stream:
            events.append(chunk)
            
        # 查找 meta 事件
        meta_events = [e for e in events if e.get("type") == "meta"]
        assert len(meta_events) == 1
        meta = meta_events[0]
        
        # 验证 LTM 偏好成功注入
        assert meta.get("ltm_applied") is True
        assert meta.get("ltm_data") == mock_ltm_data


@pytest.mark.asyncio
async def test_ignore_ltm_in_stream_meta():
    """
    测试当在 debug_options 中传入 ignore_ltm=True 时，跳过加载和注入 LTM。
    """
    agent_service = AgentService()
    
    mock_agent_config = ChatConfig(
        agent_id="test-ltm-agent",
        agent_name="LTMTestAgent",
        agent_display_name="LTM Test Agent",
        model_name="test-model",
        temperature=0.0,
        system_prompt="You are a helper.",
    )
    
    mock_turn_classification = MagicMock()
    mock_turn_classification.turn_type.value = "chat"
    mock_turn_classification.turn_type = MagicMock()

    with patch("app.services.ai.agent_service.AgentConfigProvider.get_config", new_callable=AsyncMock, return_value=mock_agent_config), \
         patch("app.services.ai.agent_service.classify_turn", new_callable=AsyncMock) as mock_classify, \
         patch("app.services.ai.agent_service.should_inject_ltm", return_value=True), \
         patch("app.services.ai.memory_service.ltm_service.fetch_memory", new_callable=AsyncMock) as mock_fetch_memory, \
         patch("app.services.ai.agent_service.AssistantExecutor", autospec=True) as mock_executor_cls, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock, return_value="test-model"), \
         patch("app.services.ai.agent_service.logger") as mock_logger:
        
        mock_classify.return_value = mock_turn_classification
        
        mock_executor_instance = AsyncMock()
        mock_executor_instance.execute.return_value = (e for e in [{"content": "hello"}])
        mock_executor_cls.return_value = mock_executor_instance
        
        # 传入 ignore_ltm=True 的 debug_options 
        stream = agent_service._run_chat_turn_stream(
            messages=[{"role": "user", "content": "你好"}],
            user_query="你好",
            agent_id="test-ltm-agent",
            agent_name="LTMTestAgent",
            version_id=None,
            conversation_id="test-conv-id",
            user_info={"user_id": "test_user_99"},
            api_key=None,
            enable_multi_agent=False,
            debug_options={"ignore_ltm": True}, # 忽略 LTM
            permission_options=None,
            knowledge_dataset_ids=None,
            trace_id="test-trace-id",
            trace_buffer=[],
            start_time=1.0,
        )
        
        events = []
        async for chunk in stream:
            events.append(chunk)
            
        # 查找 meta 事件
        meta_events = [e for e in events if e.get("type") == "meta"]
        assert len(meta_events) == 1
        meta = meta_events[0]
        
        # 验证没有注入 LTM，且没有调用过 fetch_memory
        assert "ltm_applied" not in meta
        assert "ltm_data" not in meta
        mock_fetch_memory.assert_not_called()
