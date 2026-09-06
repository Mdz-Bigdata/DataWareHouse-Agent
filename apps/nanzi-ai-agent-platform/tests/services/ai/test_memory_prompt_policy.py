import pytest


pytestmark = pytest.mark.no_infrastructure


def test_cross_session_memory_hint_is_conditioned_on_tool_availability():
    from app.services.ai.memory_recall_policy import CROSS_SESSION_MEMORY_SYSTEM_HINT

    assert "如果当前工具集中提供 memory_search" in CROSS_SESSION_MEMORY_SYSTEM_HINT
    assert "不要声称已经调用或检查了 memory_search" in CROSS_SESSION_MEMORY_SYSTEM_HINT
