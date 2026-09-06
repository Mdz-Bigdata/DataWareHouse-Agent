import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.schema_retry import clean_schema_retry_phrase
from app.services.ai.runners.data_agent_runner import DataAgentRunner

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def data_config():
    return ChatConfig(
        agent_id="data-agent-id",
        agent_name="DataAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a data agent.",
        tools=["update_dashboard_context"],
    )


def test_clean_schema_retry_phrase_filters_emoji():
    # 测试 Emoji 清理
    raw_text = "机房 🙋"
    cleaned = clean_schema_retry_phrase(raw_text)
    assert cleaned == "机房"


def test_clean_schema_retry_phrase_filters_ui_stopwords():
    # 测试 UI 停用词清理
    raw_text = "为您找到以下数据 机房的 信息 详细"
    cleaned = clean_schema_retry_phrase(raw_text)
    # "为您", "以下", "数据", "信息", "详细" 都会被过滤掉，只剩下 "机房 的" ("的"因为不在 stopword，但因为只是一个单字，后置过滤)
    assert "为您" not in cleaned
    assert "数据" not in cleaned
    assert "信息" not in cleaned
    assert "机房" in cleaned


def test_prepare_controlled_schema_retry_keywords_prioritizes_extracted(data_config):
    runner = DataAgentRunner(config=data_config, trace_id="trace-retry", trace_buffer=[])
    runner._schema_search_keywords = "机房 列表"
    runner._standalone_query = "为您 到以下数据 机房的 信息 🙋"

    state = DataRunState()
    state.last_schema_keywords = "机房"

    user_question = "为您 到以下数据 机房的 信息 🙋 机房详情"
    runner._prepare_controlled_schema_retry_keywords(state, user_question)

    keywords = state.controlled_schema_retry_keywords
    assert "机房" in keywords
    assert "为您" not in keywords
    assert "🙋" not in keywords
    assert "数据" not in keywords
    assert "信息" not in keywords


def test_prepare_controlled_schema_retry_keywords_fallback_with_cleanup(data_config):
    runner = DataAgentRunner(config=data_config, trace_id="trace-retry-fallback", trace_buffer=[])
    runner._schema_search_keywords = ""
    runner._standalone_query = ""

    state = DataRunState()
    state.last_schema_keywords = ""

    user_question = "为您 到以下数据 机房的 信息 🙋"
    runner._prepare_controlled_schema_retry_keywords(state, user_question)

    keywords = state.controlled_schema_retry_keywords
    assert "机房" in keywords
    assert "为您" not in keywords
    assert "数据" not in keywords
    assert "🙋" not in keywords
