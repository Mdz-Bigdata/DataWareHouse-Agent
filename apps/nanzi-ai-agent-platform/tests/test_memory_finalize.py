"""Conversation finalize API and force summary flush."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.session_summary_service import SessionSummaryService


@pytest.mark.asyncio
async def test_finalize_session_empty_history():
    with patch.object(
        SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
    ), patch(
        "app.services.ai.session_summary_service.memory_service.get_history",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await SessionSummaryService.finalize_session("1", "conv-a")
        assert result["finalized"] is False
        assert result["reason"] == "empty_history"


@pytest.mark.asyncio
async def test_finalize_session_calls_merge_with_force():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    with patch.object(
        SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
    ), patch(
        "app.services.ai.session_summary_service.memory_service.get_history",
        new_callable=AsyncMock,
        return_value=history,
    ), patch.object(
        SessionSummaryService,
        "merge_session_summary",
        new_callable=AsyncMock,
    ) as mock_merge:
        result = await SessionSummaryService.finalize_session("42", "conv-b")
        assert result["finalized"] is True
        mock_merge.assert_awaited_once()
        assert mock_merge.call_args.kwargs.get("force") is True


@pytest.mark.asyncio
async def test_finalize_api_endpoint():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.core.dependencies import require_api_key, verify_v1_api_access

    app.dependency_overrides[require_api_key] = lambda: {"user_id": 7, "role": "user"}
    app.dependency_overrides[verify_v1_api_access] = lambda: None

    with patch(
        "app.services.ai.session_summary_service.SessionSummaryService.finalize_session",
        new_callable=AsyncMock,
        return_value={"finalized": True, "conversation_id": "cid-1"},
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/chat/conversation/cid-1/finalize",
                headers={"X-API-Key": "test-key"},
            )

    app.dependency_overrides.clear()
    assert res.status_code == 200
    assert res.json()["data"]["finalized"] is True
