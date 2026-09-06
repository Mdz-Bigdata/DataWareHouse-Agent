import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.runners.chatbi.non_data_policy import (
    NonDataDisposition,
    resolve_non_data_disposition,
)


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.mark.parametrize(
    ("query", "has_result", "expected"),
    [
        ("你好，你能做什么", False, NonDataDisposition.LOCAL_HELP),
        ("同比是什么意思", False, NonDataDisposition.LOCAL_HELP),
        ("把刚才的结果导出成 Excel", True, NonDataDisposition.RESULT_ACTION),
        ("把这个结果写成一封邮件", True, NonDataDisposition.RESULT_ACTION),
        ("帮我翻译这段英文", False, NonDataDisposition.DELEGATE_MAIN),
        ("查询一下平台的模型怎么配置", False, NonDataDisposition.DELEGATE_MAIN),
        ("联网查一下今天的行业新闻", False, NonDataDisposition.DELEGATE_WEB),
        ("查一下有孚网络公司信息", False, NonDataDisposition.DELEGATE_WEB),
    ],
)
def test_resolve_non_data_disposition(query, has_result, expected):
    result = resolve_non_data_disposition(query, has_last_data_result=has_result)
    assert result.disposition == expected


def test_result_action_requires_reusable_result():
    result = resolve_non_data_disposition(
        "把刚才的结果导出成 Excel",
        has_last_data_result=False,
    )
    assert result.disposition == NonDataDisposition.LOCAL_HELP
    assert result.requires_result is True


@pytest.mark.asyncio
async def test_non_data_local_help_stays_in_chatbi(monkeypatch):
    from app.services.ai.data_query_turn_classifier import DataQueryTurnType
    from app.services.ai.runners.chatbi.turn_handlers import dispatch_early_turn

    async def fake_local_help(*args, **kwargs):
        yield {"content": "同比是本期与上年同期的比较。"}

    async def forbidden_handoff(*args, **kwargs):
        raise AssertionError("local help must not hand off")
        yield

    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.clarification.yield_local_help",
        fake_local_help,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.handoff.stream_to_routed_assistant",
        forbidden_handoff,
    )
    events = [
        event
        async for event in dispatch_early_turn(
            SimpleNamespace(),
            turn_cls=SimpleNamespace(turn_type=DataQueryTurnType.NON_DATA_REQUEST),
            history=[],
            runtime_messages=[],
            user_question="同比是什么意思",
            last_data_result_for_turn=None,
        )
    ]
    assert events == [{"content": "同比是本期与上年同期的比较。"}]


@pytest.mark.asyncio
async def test_non_data_web_request_is_seamlessly_handed_off(monkeypatch):
    from app.services.ai.data_query_turn_classifier import DataQueryTurnType
    from app.services.ai.runners.chatbi.turn_handlers import dispatch_early_turn

    async def fake_handoff(*args, **kwargs):
        yield {"content": "已由联网助手处理"}

    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.handoff.stream_to_routed_assistant",
        fake_handoff,
    )
    events = [
        event
        async for event in dispatch_early_turn(
            SimpleNamespace(),
            turn_cls=SimpleNamespace(turn_type=DataQueryTurnType.NON_DATA_REQUEST),
            history=[],
            runtime_messages=[],
            user_question="联网查一下今天行业新闻",
            last_data_result_for_turn=None,
        )
    ]
    assert events == [{"content": "已由联网助手处理"}]
