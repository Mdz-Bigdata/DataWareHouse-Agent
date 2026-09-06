import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.agent_service import AgentService
from app.schemas.agent import ChatConfig, AgentExecutionStep
from app.services.ai.turn_classifier import TurnClassification, TurnType
from app.services.ai.intent_service import IntentType


pytestmark = pytest.mark.no_infrastructure


SESSION_TURN = (
    TurnClassification(
        turn_type=TurnType.GENERAL,
        reasoning="测试",
        skip_intent_llm=True,
        intent=IntentType.GENERAL,
    ),
    None,
    0.0,
)

@pytest.mark.asyncio
async def test_execute_multi_agent_parallel():
    service = AgentService()
    
    # Mock Configs
    primary_config = ChatConfig(
        agent_id="primary", agent_name="PrimaryAgent", system_prompt="P", tools=[], capabilities=[],
        model_name="test-model", temperature=0.0
    )
    
    # Mock Executor Generator
    async def mock_execute(messages):
        yield {"type": "log", "title": "Starting task", "status": "success"}
        await asyncio.sleep(0.1) # Simulate work
        yield {"content": "Task Result"}

    mock_executor = MagicMock()
    mock_executor.execute = mock_execute

    # Mock Dispatcher
    mock_dispatch = AsyncMock(return_value=mock_executor)
    with patch('app.services.ai.dispatcher.AgentDispatcher.dispatch', mock_dispatch):
        # Mock AgentManager to resolve secondary
        mock_secondary_config = ChatConfig(
            agent_id="secondary", agent_name="SecondaryAgent", system_prompt="S", tools=[], capabilities=[],
            model_name="test-model", temperature=0.0
        )
        
        with patch('app.services.ai.agent_manager.AgentManagerService.get_active_agent_config', return_value=mock_secondary_config):
            # Mock Synthesis
            async def mock_synthesis(config, query, outputs, trace_buffer):
                yield {"content": "Final Combined Answer"}
            
            with patch.object(AgentService, '_synthesize_multi_agent_results', side_effect=mock_synthesis):
                
                chunks = []
                async for chunk in service._execute_multi_agent(
                    primary_config, ["secondary-id"], "hello", [], "trace-1", [], {}, None, None, None, SESSION_TURN
                ):
                    chunks.append(chunk)
                
                # Verify logs were yielded
                log_titles = [c["title"] for c in chunks if c.get("type") == "log"]
                assert any("多智能体协作" in t for t in log_titles)
                assert any("[PrimaryAgent] Starting task" in t for t in log_titles)
                assert any("[SecondaryAgent] Starting task" in t for t in log_titles)
                assert any("结果聚合" in t for t in log_titles)
                
                # Verify final content
                assert any(c.get("content") == "Final Combined Answer" for c in chunks)

                # shared_turn 应传给每次 dispatch（2 个 agent）
                assert mock_dispatch.call_count == 2
                for call in mock_dispatch.call_args_list:
                    assert call.kwargs.get("shared_turn") is not None

@pytest.mark.asyncio
async def test_multi_agent_error_handling():
    service = AgentService()
    
    primary_config = ChatConfig(
        agent_id="primary", agent_name="PrimaryAgent", system_prompt="P", tools=[], capabilities=[],
        model_name="test-model", temperature=0.0
    )
    
    # Mock one executor that fails
    async def failing_execute(messages):
        yield {"type": "log", "title": "Starting failing task"}
        raise Exception("Boom!")

    mock_failing_executor = MagicMock()
    mock_failing_executor.execute = failing_execute

    with patch('app.services.ai.dispatcher.AgentDispatcher.dispatch', return_value=mock_failing_executor):
        mock_secondary_config = ChatConfig(
            agent_id="secondary", agent_name="SecondaryAgent", system_prompt="S", tools=[], capabilities=[],
            model_name="test-model", temperature=0.0
        )
        
        with patch('app.services.ai.agent_manager.AgentManagerService.get_active_agent_config', return_value=mock_secondary_config):
            
            # We need to capture what's passed to synthesis
            captured_outputs = []
            async def mock_synthesis(config, query, outputs, trace_buffer):
                nonlocal captured_outputs
                captured_outputs = outputs
                yield {"content": "Synthesis with error"}

            with patch.object(AgentService, '_synthesize_multi_agent_results', side_effect=mock_synthesis):
                chunks = []
                async for chunk in service._execute_multi_agent(
                    primary_config, ["secondary-id"], "hello", [], "trace-1", [], {}, None, None, None, SESSION_TURN
                ):
                    chunks.append(chunk)
                
                # Verify error log was yielded
                log_statuses = [c.get("status") for c in chunks if c.get("type") == "log"]
                assert "error" in log_statuses
                
                # Verify outputs passed to synthesis contains error text instead of crashing
                assert any("执行失败" in out["content"] for out in captured_outputs)
