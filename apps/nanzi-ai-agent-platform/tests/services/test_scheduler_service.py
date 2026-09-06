import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from datetime import datetime
from sqlalchemy import select, delete

from app.models.task import AgentScheduledTask
from app.services.ai.scheduler_service import scheduler_service
from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.ai.scheduler_service import (
    _build_scheduled_task_prompt,
    _is_incomplete_task_result,
    _new_task_run_conversation_id,
    _normalize_task_metrics,
    _should_alert_failure,
    _task_permission_options,
)


@pytest.mark.no_infrastructure
def test_task_permission_options_auto_allow_runtime_tools():
    assert _task_permission_options() == {"approval_mode": "allow"}


@pytest.mark.no_infrastructure
def test_task_run_conversation_id_is_unique_child_conversation():
    first = _new_task_run_conversation_id("task_conv_abc123")
    second = _new_task_run_conversation_id("task_conv_abc123")

    assert first.startswith("task_conv_abc123_run_")
    assert second.startswith("task_conv_abc123_run_")
    assert first != second
    assert len(first) <= 50


@pytest.mark.no_infrastructure
def test_scheduled_task_prompt_requires_real_tool_execution():
    prompt = _build_scheduled_task_prompt(24, "主助手(Main)", "检查机器负载并发送钉钉")

    assert "【自动化指令-任务ID: 24】@主助手(Main)" in prompt
    assert "请立即实际执行任务" in prompt
    assert "不要只回复计划" in prompt
    assert "必须调用对应工具完成" in prompt
    assert "任务内容：检查机器负载并发送钉钉" in prompt
    assert "【结果通知要求】" not in prompt


@pytest.mark.no_infrastructure
def test_scheduled_task_prompt_appends_notification_channels_supplement():
    prompt = _build_scheduled_task_prompt(
        24,
        "主助手(Main)",
        "检查机器负载",
        notification_channels=["portal", "dingtalk"],
    )

    assert "任务内容：检查机器负载" in prompt
    assert "【结果通知要求】" in prompt
    assert "send_portal_notification" in prompt
    assert "send_dingtalk_message" in prompt
    assert "每个渠道只发送一次" in prompt


@pytest.mark.no_infrastructure
def test_incomplete_task_result_detects_pending_and_busy_states():
    assert _is_incomplete_task_result({
        "status": "awaiting_external_execution",
        "content": "需要外部执行",
    })
    assert _is_incomplete_task_result({
        "status": "awaiting_permission",
        "content": "需要确认",
    })
    assert _is_incomplete_task_result({
        "status": "error",
        "content": "当前会话正在处理中，请稍后再试。",
    })
    assert _is_incomplete_task_result({
        "status": "error",
        "content": "自动任务未实际调用任何工具，本次只产生了模型回复。",
    })
    assert not _is_incomplete_task_result({
        "status": "success",
        "content": "执行完成",
    })


@pytest.mark.no_infrastructure
def test_task_metrics_normalization_and_alert_cadence():
    metrics = _normalize_task_metrics({
        "task_metrics": {
            "trigger_count": "2",
            "failure_count": "1",
            "consecutive_failures": "3",
            "health_status": "error",
        }
    })

    assert metrics["trigger_count"] == 2
    assert metrics["success_count"] == 0
    assert metrics["failure_count"] == 1
    assert metrics["consecutive_failures"] == 3
    assert metrics["health_status"] == "error"
    assert _should_alert_failure(metrics)
    assert _should_alert_failure({"consecutive_failures": 1})
    assert not _should_alert_failure({"consecutive_failures": 2})

@pytest.fixture
async def cleanup_tasks():
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentScheduledTask))
        await session.commit()

@pytest.mark.asyncio
async def test_scheduler_lifecycle():
    """Test start and stop of scheduler."""
    try:
        await scheduler_service.start()
        assert scheduler_service._scheduler.running is True
        
        await scheduler_service.stop()
        assert scheduler_service._scheduler is None
    finally:
        if scheduler_service._scheduler and scheduler_service._scheduler.running:
            await scheduler_service.stop()

@pytest.mark.asyncio
async def test_upsert_and_run_task(seed_data, cleanup_tasks):
    """Test adding a task and manually running it."""
    async with AsyncSessionLocal() as session:
        # Get test user
        res = await session.execute(select(User).where(User.user_name == "test_user"))
        user = res.scalar_one()
        
        # Create a task
        task = AgentScheduledTask(
            name="Test Task",
            user_id=user.id,
            agent_id="test_agent",
            conversation_id="test_conv",
            cron_expr="0 0 * * *", # Daily at midnight
            prompt="Hello world",
            status=1
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        
        # Start scheduler
        await scheduler_service.start()
        
        try:
            # Sync to scheduler
            await scheduler_service.upsert_task(task)
            
            # Verify it's in scheduler
            job = scheduler_service._scheduler.get_job(f"task_{task.id}")
            assert job is not None
            
            # Mock agent_service
            with patch("app.services.ai.agent_service.agent_service.chat_completion", new_callable=AsyncMock) as mock_chat:
                mock_chat.return_value = {"content": "Mocked Response"}
                
                # Run manually
                await scheduler_service.run_task(task.id, is_manual=True)
                
                # Verify mock was called
                mock_chat.assert_called_once()
                args, kwargs = mock_chat.call_args
                assert kwargs["agent_id"] == "test_agent"
                assert kwargs["conversation_id"].startswith("test_conv_run_")
                assert kwargs["conversation_id"] != "test_conv"
                msg_content = kwargs["messages"][0]["content"]
                assert "【自动化指令-任务ID:" in msg_content
                assert "@test_agent" in msg_content
                assert "请立即实际执行任务" in msg_content
                assert "任务内容：Hello world" in msg_content
                assert kwargs["user_info"]["user_id"] == user.id
                assert kwargs["user_info"]["is_scheduled_task"] is True
                assert kwargs["user_info"]["requires_tool_execution"] is True
                
            # Verify DB was updated - use a fresh session to avoid cache
            async with AsyncSessionLocal() as verify_session:
                stmt = select(AgentScheduledTask).where(AgentScheduledTask.id == task.id)
                res = await verify_session.execute(stmt)
                updated_task = res.scalar_one()
                assert updated_task.run_count == 1
                assert updated_task.last_run_at is not None
        finally:
            await scheduler_service.stop()

@pytest.mark.asyncio
async def test_manual_task_busy_does_not_update_run_metadata(seed_data, cleanup_tasks):
    """A busy conversation response means the task did not actually execute."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.user_name == "test_user"))
        user = res.scalar_one()

        task = AgentScheduledTask(
            name="Busy Test",
            user_id=user.id,
            agent_id="test_agent",
            conversation_id="test_conv_busy",
            cron_expr="0 0 * * *",
            prompt="Hello world",
            status=1,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        with patch("app.services.ai.agent_service.agent_service.chat_completion", new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {
                "trace_id": "busy-trace",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }

            await scheduler_service.run_task(task.id, is_manual=True)

        async with AsyncSessionLocal() as verify_session:
            stmt = select(AgentScheduledTask).where(AgentScheduledTask.id == task.id)
            res = await verify_session.execute(stmt)
            updated_task = res.scalar_one()
            assert updated_task.run_count == 0
            assert updated_task.last_run_id is None
            assert updated_task.last_run_at is None

@pytest.mark.asyncio
async def test_disable_task(seed_data, cleanup_tasks):
    """Test disabling a task removes it from scheduler."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.user_name == "test_user"))
        user = res.scalar_one()
        
        task = AgentScheduledTask(
            name="Disable Test",
            user_id=user.id,
            agent_id="test_agent",
            conversation_id="test_conv_disable",
            cron_expr="0 0 * * *",
            prompt="Bye world",
            status=1
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        
        await scheduler_service.start()
        try:
            await scheduler_service.upsert_task(task)
            assert scheduler_service._scheduler.get_job(f"task_{task.id}") is not None
            
            # Disable
            task.status = 0
            await scheduler_service.upsert_task(task)
            assert scheduler_service._scheduler.get_job(f"task_{task.id}") is None
            
        finally:
            await scheduler_service.stop()
