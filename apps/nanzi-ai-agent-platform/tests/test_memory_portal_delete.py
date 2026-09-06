"""Memory portal deletion behavior."""
from unittest.mock import AsyncMock, patch

import pytest

from app.api.portal.endpoints.memory import delete_summary

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_delete_summary_deletes_only_summary_not_history():
    with patch(
        "app.api.portal.endpoints.memory._can_view_user",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.api.portal.endpoints.memory.MemoryIndexService.delete_summary",
        new_callable=AsyncMock,
    ) as mock_delete_summary, patch(
        "app.api.portal.endpoints.memory.memory_service.delete_session_memory",
        new_callable=AsyncMock,
    ) as mock_delete_session:
        result = await delete_summary(
            target_user_id=7,
            conversation_id="conv-1",
            user={"user_id": 7, "role": "user"},
            _health={"ok": True},
        )

    assert result["status"] == "success"
    mock_delete_summary.assert_awaited_once_with("7", "conv-1")
    mock_delete_session.assert_not_awaited()
