from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_metadata_guide_shared_reducer_and_both_chat_surfaces():
    reducer = _source("frontend/src/utils/chatbiMetadataGuide.ts")
    component = _source("frontend/src/components/chatbi/ChatBIMetadataGuide.vue")
    embed = _source("frontend/src/views/EmbedChat.vue")
    debug = _source("frontend/src/views/AgentDebug.vue")

    assert 'event?.type !== "chatbi_metadata_guide"' in reducer
    assert "guide.suggestions" in component
    assert "emit('select', item.query)" in component
    for source in (embed, debug):
        assert "applyChatBIMetadataGuideEvent" in source
        assert "<ChatBIMetadataGuide" in source
