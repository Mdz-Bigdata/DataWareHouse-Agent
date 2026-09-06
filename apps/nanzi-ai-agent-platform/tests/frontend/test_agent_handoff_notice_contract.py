from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_handoff_notice_is_shared_and_rendered_before_message_body():
    reducer = _source("frontend/src/utils/agentHandoff.ts")
    component = _source("frontend/src/components/chat/AgentHandoffNotice.vue")
    assert 'event?.type !== "agent_handoff"' in reducer
    assert "已转交给" in component
    for view_path in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(view_path)
        assert "applyAgentHandoffEvent" in source
        notice_at = source.index("<AgentHandoffNotice")
        renderer_at = source.index("<MessageRenderer", notice_at)
        assert notice_at < renderer_at
        assert "msg.agentHandoff" in source
