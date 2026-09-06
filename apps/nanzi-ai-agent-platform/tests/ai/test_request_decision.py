import pytest

from app.services.ai.intent_service import IntentType
from app.services.ai.request_decision import (
    RequestCapability,
    RequestSource,
    resolve_request_decision,
)


pytestmark = pytest.mark.no_infrastructure


def test_platform_self_help_overrides_knowledge_binding_and_semantic_knowledge():
    decision = resolve_request_decision(
        "那如何安装 skills 技能呢",
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.88,
        has_knowledge_binding=True,
    )

    assert decision.source == RequestSource.PLATFORM_SELF_HELP
    assert decision.capability == RequestCapability.ANSWER
    assert decision.should_delegate is False
    assert decision.requires_knowledge_search is False
    assert decision.allows_data_route is False


def test_public_web_request_overrides_knowledge_semantics_without_internal_search():
    decision = resolve_request_decision(
        "搜索一下有孚网络的最新信息",
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.8,
        has_knowledge_binding=True,
    )

    assert decision.source == RequestSource.PUBLIC_WEB
    assert decision.capability == RequestCapability.WEB_SEARCH
    assert decision.should_delegate is False
    assert decision.requires_knowledge_search is False


def test_internal_docs_requires_knowledge_search_and_can_delegate():
    decision = resolve_request_decision(
        "查一下设备运维规范和操作指引",
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.92,
    )

    assert decision.source == RequestSource.INTERNAL_DOCS
    assert decision.capability == RequestCapability.KNOWLEDGE_SEARCH
    assert decision.should_delegate is True
    assert decision.delegate_capability == "knowledge_base"
    assert decision.requires_knowledge_search is True


def test_internal_structured_data_requires_data_query_and_allows_data_route():
    decision = resolve_request_decision(
        "查一下客户订单列表",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.91,
    )

    assert decision.source == RequestSource.INTERNAL_STRUCTURED_DATA
    assert decision.capability == RequestCapability.DATA_QUERY
    assert decision.should_delegate is True
    assert decision.delegate_capability == "data_query"
    assert decision.allows_data_route is True


def test_data_query_intent_delegates_without_strong_keyword_signal():
    """意图已是 DATA_QUERY 时，即使未命中强业务关键词也必须委派/可路由。"""
    decision = resolve_request_decision(
        "查询合同编号 YVPR-FZN-202211-068 下的所有资产信息",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.9,
        turn_intent=IntentType.DATA_QUERY,
    )

    assert decision.source == RequestSource.INTERNAL_STRUCTURED_DATA
    assert decision.capability == RequestCapability.DATA_QUERY
    assert decision.should_delegate is True
    assert decision.delegate_capability == "data_query"
    assert decision.allows_data_route is True


def test_runtime_diagnostic_is_tool_capability_not_data_route():
    decision = resolve_request_decision(
        "查看当前系统的CPU和内存使用情况",
        turn_intent=IntentType.DATA_QUERY,
    )

    assert decision.source == RequestSource.RUNTIME_DIAGNOSTIC
    assert decision.capability == RequestCapability.RUNTIME_TOOL
    assert decision.should_delegate is False
    assert decision.allows_data_route is False


def test_my_server_status_query_is_runtime_diagnostic():
    decision = resolve_request_decision("看看我的服务器状态")

    assert decision.source == RequestSource.RUNTIME_DIAGNOSTIC
    assert decision.capability == RequestCapability.RUNTIME_TOOL


def test_server_status_concept_explanation_is_not_runtime_diagnostic():
    decision = resolve_request_decision("服务器状态是什么意思")

    assert decision.source != RequestSource.RUNTIME_DIAGNOSTIC


def test_dynamic_public_fact_requires_web_evidence():
    decision = resolve_request_decision("上海现在天气怎么样")

    assert decision.source == RequestSource.PUBLIC_WEB
    assert decision.capability == RequestCapability.WEB_SEARCH


def test_general_previous_web_visualization_is_context_transform_without_delegate():
    decision = resolve_request_decision(
        "能不能把刚刚的信息可视化一下呢",
        semantic_intent=IntentType.GENERAL,
        semantic_confidence=0.95,
    )

    assert decision.source == RequestSource.CONVERSATION_CONTEXT
    assert decision.capability == RequestCapability.CONTEXT_TRANSFORM
    assert decision.should_delegate is False
    assert decision.delegate_capability is None
    assert decision.allows_data_route is False


def test_data_previous_result_visualization_can_delegate_to_data_query():
    decision = resolve_request_decision(
        "把刚才的结果画成柱状图",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.93,
    )

    assert decision.source == RequestSource.CONVERSATION_CONTEXT
    assert decision.capability == RequestCapability.CONTEXT_TRANSFORM
    assert decision.should_delegate is True
    assert decision.delegate_capability == "data_query"
    assert decision.allows_data_route is True
