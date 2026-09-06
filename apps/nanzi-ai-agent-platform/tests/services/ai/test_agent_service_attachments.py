import pytest

from app.services.ai.agent_service import AgentService


pytestmark = pytest.mark.no_infrastructure


def test_current_turn_attachment_paths_only_uses_latest_user_message(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.agent_service._attachment_abs_path",
        lambda file_obj: f"/resolved/{file_obj['url']}",
    )
    messages = [
        {"role": "user", "content": "first", "files": [{"url": "old.xlsx"}]},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "follow-up", "files": [{"url": "new.xlsx"}]},
    ]

    assert AgentService._authorized_attachment_paths(messages) == [
        "/resolved/new.xlsx",
        "/resolved/old.xlsx",
    ]
    assert AgentService._current_turn_attachment_paths(messages) == [
        "/resolved/new.xlsx"
    ]


def test_current_turn_attachment_paths_is_empty_when_latest_user_has_no_files(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.agent_service._attachment_abs_path",
        lambda file_obj: f"/resolved/{file_obj['url']}",
    )
    messages = [
        {"role": "user", "content": "first", "files": [{"url": "old.xlsx"}]},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "unrelated question"},
    ]

    assert AgentService._authorized_attachment_paths(messages) == ["/resolved/old.xlsx"]
    assert AgentService._current_turn_attachment_paths(messages) == []
