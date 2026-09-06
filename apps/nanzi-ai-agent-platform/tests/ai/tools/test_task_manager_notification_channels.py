from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.tools import task_manager_tools as tools


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_create_recurring_task_persists_notification_channels():
    created = SimpleNamespace(id=9, next_run_at="2026-07-21 08:00:00")
    create_task = AsyncMock(return_value=created)
    ctx = SimpleNamespace(user_id=7, agent_id="main", is_admin=False)

    with patch.object(tools, "get_current_agent_context", return_value=ctx), patch.object(
        tools.TaskCenterService, "create_task", create_task
    ), patch.object(tools, "AsyncSessionLocal") as session_cm:
        session = MagicMock()
        session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
        session_cm.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await tools.create_recurring_task.ainvoke(
            {
                "name": "巡检",
                "cron": "0 8 * * *",
                "prompt": "检查负载",
                "notification_channels": ["portal", "dingtalk", "portal"],
            }
        )

    assert "Successfully created" in result
    assert "portal" in result and "dingtalk" in result
    kwargs = create_task.await_args.kwargs
    assert kwargs["prompt"] == "检查负载"
    assert kwargs["source"] == "agent"
    assert kwargs["config"] == {"notification_channels": ["portal", "dingtalk"]}


@pytest.mark.asyncio
async def test_create_recurring_task_without_channels_keeps_empty_config():
    created = SimpleNamespace(id=10, next_run_at="N/A")
    create_task = AsyncMock(return_value=created)
    ctx = SimpleNamespace(user_id=7, agent_id="main", is_admin=False)

    with patch.object(tools, "get_current_agent_context", return_value=ctx), patch.object(
        tools.TaskCenterService, "create_task", create_task
    ), patch.object(tools, "AsyncSessionLocal") as session_cm:
        session = MagicMock()
        session_cm.return_value.__aenter__ = AsyncMock(return_value=session)
        session_cm.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await tools.create_recurring_task.ainvoke(
            {
                "name": "巡检",
                "cron": "0 8 * * *",
                "prompt": "检查负载并发送站内消息",
            }
        )

    assert "Successfully created" in result
    kwargs = create_task.await_args.kwargs
    assert kwargs["config"] is None
