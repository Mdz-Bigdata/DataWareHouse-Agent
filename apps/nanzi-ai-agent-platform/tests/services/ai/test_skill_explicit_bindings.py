"""公共 Skill 显式绑定智能体反查。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ai.agent_manager import AgentManagerService

pytestmark = pytest.mark.no_infrastructure


def run(coro):
    return asyncio.run(coro)


def _result(rows):
    mock = MagicMock()
    mock.all.return_value = rows
    return mock


def test_skill_bindings_prefers_draft_and_skips_all_skills_mode():
    draft = SimpleNamespace(
        agent_id="a1",
        status="DRAFT",
        version_number=2,
        skills=["web-search", "csv-helper"],
        skills_custom=True,
    )
    published = SimpleNamespace(
        agent_id="a1",
        status="PUBLISHED",
        version_number=1,
        skills=["web-search"],
        skills_custom=True,
    )
    other = SimpleNamespace(
        agent_id="a3",
        status="PUBLISHED",
        version_number=1,
        skills=["web-search"],
        skills_custom=True,
    )
    agent1 = SimpleNamespace(id="a1", name="agent-1", display_name="客服助手")
    agent3 = SimpleNamespace(id="a3", name="agent-3", display_name="研报分析")

    session = AsyncMock()
    # SQL 侧已过滤 skills_custom=true；此处仅传自定义白名单版本
    session.execute = AsyncMock(
        return_value=_result(
            [
                (draft, agent1),
                (published, agent1),
                (other, agent3),
            ]
        )
    )

    bindings = run(AgentManagerService.get_skill_explicit_bindings(session))

    assert bindings["web-search"]["count"] == 2
    names = [a["name"] for a in bindings["web-search"]["agents"]]
    assert names == ["客服助手", "研报分析"]
    assert bindings["web-search"]["agents"][0]["version_status"] == "DRAFT"
    assert bindings["web-search"]["agents"][0]["version_number"] == 2
    assert bindings["csv-helper"]["count"] == 1
    assert bindings["csv-helper"]["agents"][0]["id"] == "a1"


def test_skill_bindings_empty():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result([]))
    bindings = run(AgentManagerService.get_skill_explicit_bindings(session))
    assert bindings == {}
