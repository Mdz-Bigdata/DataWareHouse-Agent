import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.openclaw_client import summarize_openclaw_payload_for_log


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override global infrastructure initialization; this test only covers payload summarization."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


def test_openclaw_payload_log_summary_omits_message_content_and_auth_context():
    payload = {
        "model": "openclaw-v1",
        "stream": True,
        "user": "chenxiaolong-session",
        "conversation_id": "conv-1",
        "messages": [
            {"role": "system", "content": "<AUTH_CONTEXT>{\"datasets\":[{\"name\":\"secret\"}]}</AUTH_CONTEXT>"},
            {"role": "user", "content": "查一下敏感数据"},
        ],
        "extra_params": {"x": 1},
    }

    summary = summarize_openclaw_payload_for_log(payload)

    assert summary == {
        "model": "openclaw-v1",
        "stream": True,
        "user": "chenxiaolong-session",
        "conversation_id": "conv-1",
        "message_count": 2,
        "extra_param_keys": ["x"],
    }
    assert "messages" not in summary
    assert "secret" not in str(summary)
