import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.tools.notification_tools import send_portal_notification
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tool_nudge_policy import resolve_tool_nudge

pytestmark = pytest.mark.no_infrastructure


def _tool(name: str, description: str = ""):
    tool = MagicMock()
    tool.name = name
    tool.description = description
    return tool


def test_send_portal_notification_is_system_implicit():
    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}
    assert "send_portal_notification" in tool_names
    assert ToolRegistry._registry["send_portal_notification"].name == "send_portal_notification"


@pytest.mark.asyncio
async def test_send_portal_notification_writes_inbox_for_current_user():
    tool = send_portal_notification()
    ctx = MagicMock()
    ctx.user_id = 42
    ctx.conversation_id = "conv-9"
    ctx.agent_name = "main"

    created = MagicMock()
    created.id = 101

    db = AsyncMock()
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=db)
    db_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.core.context.get_current_agent_context",
        return_value=ctx,
    ), patch(
        "app.core.orm.AsyncSessionLocal",
        return_value=db_cm,
    ), patch(
        "app.services.portal_notification_service.PortalNotificationService.create",
        AsyncMock(return_value=created),
    ) as mock_create:
        result = await tool._arun(
            title="巡检完成",
            content="动环延迟正常",
            level="success",
        )

    mock_create.assert_awaited_once()
    kwargs = mock_create.await_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["title"] == "巡检完成"
    assert kwargs["content"] == "动环延迟正常"
    assert kwargs["level"] == "success"
    assert kwargs["category"] == "agent"
    assert kwargs["resource_type"] == "agent_message"
    db.commit.assert_awaited_once()
    assert "Successfully sent portal notification" in result
    assert "101" in result


@pytest.mark.asyncio
async def test_send_portal_notification_requires_user_context():
    tool = send_portal_notification()
    with patch("app.core.context.get_current_agent_context", return_value=None):
        result = await tool._arun(title="x", content="y")
    assert result.startswith("Error:")


def test_portal_notification_nudge_for_inbox_request():
    tools = [
        _tool("send_portal_notification", "发送站内消息到门户铃铛"),
        _tool("send_dingtalk_message", "发送钉钉消息"),
    ]
    nudge = resolve_tool_nudge("把结果发到站内消息中心", tools)
    assert nudge is not None
    assert nudge.tool_name == "send_portal_notification"
    assert "站内" in nudge.message or "铃铛" in nudge.message
