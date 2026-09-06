import pytest

from app.api.v1.endpoints.chat import ChatMessage


pytestmark = pytest.mark.no_infrastructure


def test_chat_message_preserves_skill_file_type():
    """
    Skill selections must survive request parsing so the service can load SKILL.md
    instead of treating the skill id as an uploaded file path.
    """
    message = ChatMessage(
        role="user",
        content="",
        files=[
            {
                "type": "skill",
                "url": "brainstorming",
                "filename": "brainstorming (技能)",
                "size": 0,
                "ext": "skill",
            }
        ],
    )

    dumped = message.dict()

    assert dumped["files"][0]["type"] == "skill"
    assert dumped["files"][0]["url"] == "brainstorming"
