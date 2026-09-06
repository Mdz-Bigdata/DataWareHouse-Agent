"""Cross-session recall query heuristics."""
import pytest

from app.services.ai.memory_recall_policy import (
    looks_like_cross_session_recall_query,
    last_user_message_text,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("今天我们聊了啥", True),
        ("最近我们聊了什么", True),
        ("上次讨论的内容还记得吗", True),
        ("回顾一下历史对话", True),
        ("帮我查机房列表", False),
        ("你好", False),
    ],
)
def test_looks_like_cross_session_recall_query(text, expected):
    assert looks_like_cross_session_recall_query(text) is expected


def test_last_user_message_text():
    history = [
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "今天我们聊了啥"},
    ]
    assert last_user_message_text(history) == "今天我们聊了啥"
