"""Memory portal daily summary management."""
from unittest.mock import AsyncMock, patch

import pytest

from app.api.portal.endpoints.memory import (
    delete_daily_summary,
    get_daily_summary_detail,
    list_daily_summaries,
    rebuild_daily_summary,
)

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_list_daily_summaries_attaches_user_labels_and_session_count():
    rows = [
        {
            "user_id": "7",
            "conversation_id": "daily:2026-05-28",
            "summary_type": "daily",
            "date": "2026-05-28",
            "title": "今日记忆",
            "summary": "今天讨论了记忆管理。",
            "last_active": 100,
            "turn_count": 4,
            "has_embedding": True,
        }
    ]
    with patch(
        "app.api.portal.endpoints.memory._resolve_target_user_id",
        new_callable=AsyncMock,
        return_value="7",
    ), patch(
        "app.api.portal.endpoints.memory.DailySummaryService.list_daily_summaries",
        new_callable=AsyncMock,
        return_value=rows,
    ), patch(
        "app.api.portal.endpoints.memory._user_display_names",
        new_callable=AsyncMock,
        return_value={"7": {"user_name": "admin", "display_name": "管理员"}},
    ), patch(
        "app.api.portal.endpoints.memory.MemoryIndexService.count_session_summaries_for_day",
        new_callable=AsyncMock,
        return_value=3,
    ):
        res = await list_daily_summaries(
            user_id=7,
            username=None,
            keyword=None,
            date_from=None,
            date_to=None,
            limit=20,
            user={"user_id": 7, "role": "user"},
            _health={"ok": True},
        )

    assert res["status"] == "success"
    item = res["data"][0]
    assert item["display_name"] == "管理员"
    assert item["session_count"] == 3


@pytest.mark.asyncio
async def test_daily_detail_returns_daily_and_related_sessions():
    daily = {
        "user_id": "7",
        "date": "2026-05-28",
        "conversation_id": "daily:2026-05-28",
        "summary_type": "daily",
    }
    sessions = [{"conversation_id": "conv-1", "summary_type": "session"}]
    with patch(
        "app.api.portal.endpoints.memory._can_view_user",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.api.portal.endpoints.memory.DailySummaryService.get_daily_summary",
        new_callable=AsyncMock,
        return_value=daily,
    ), patch(
        "app.api.portal.endpoints.memory.MemoryIndexService.list_session_summaries_for_day",
        new_callable=AsyncMock,
        return_value=sessions,
    ), patch(
        "app.api.portal.endpoints.memory._user_display_names",
        new_callable=AsyncMock,
        return_value={"7": {"user_name": "admin", "display_name": "管理员"}},
    ), patch(
        "app.api.portal.endpoints.memory.memory_service.history_exists",
        new_callable=AsyncMock,
        return_value=True,
    ):
        res = await get_daily_summary_detail(
            target_user_id=7,
            day="2026-05-28",
            user={"user_id": 7, "role": "user"},
            _health={"ok": True},
        )

    assert res["data"]["summary"]["display_name"] == "管理员"
    assert res["data"]["sessions"] == sessions


@pytest.mark.asyncio
async def test_rebuild_and_delete_daily_summary_do_not_touch_history():
    with patch(
        "app.api.portal.endpoints.memory._can_view_user",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.api.portal.endpoints.memory.DailySummaryService.refresh_for_date",
        new_callable=AsyncMock,
        return_value={"refreshed": True},
    ) as mock_refresh:
        rebuild = await rebuild_daily_summary(
            target_user_id=7,
            day="2026-05-28",
            user={"user_id": 7, "role": "user"},
            _health={"ok": True},
        )

    assert rebuild["data"]["refreshed"] is True
    mock_refresh.assert_awaited_once_with("7", "2026-05-28")

    with patch(
        "app.api.portal.endpoints.memory._can_view_user",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.api.portal.endpoints.memory.DailySummaryService.delete_daily_summary",
        new_callable=AsyncMock,
    ) as mock_delete:
        deleted = await delete_daily_summary(
            target_user_id=7,
            day="2026-05-28",
            user={"user_id": 7, "role": "user"},
            _health={"ok": True},
        )

    assert deleted["message"] == "已删除每日摘要"
    mock_delete.assert_awaited_once_with("7", "2026-05-28")
