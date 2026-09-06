from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.services.access_policy import AccessPolicyContextV1
from app.services.recommendation_service import RecommendationService


def _policy(*tables: str) -> AccessPolicyContextV1:
    return AccessPolicyContextV1(
        user_id="user-1",
        role="user",
        domain="audio",
        datasource="audio_dw",
        table_acl=dict.fromkeys(tables, ("id",)),
        function_whitelist=("COUNT",),
        policy_version="policy-v1",
        policy_hash="a" * 64,
    )


def test_manual_recommendations_have_priority_and_avoid_llm_when_full():
    async def fail_if_called():
        raise AssertionError("manual recommendations should avoid the LLM")

    service = RecommendationService(
        manual_recommendations=[
            {
                "question": "把时间范围改为上个月",
                "intents": ["trend"],
                "priority": 30,
            },
            {
                "question": "与上一周期对比同一指标",
                "intents": ["trend"],
                "priority": 20,
            },
            {
                "question": "按周查看同一指标趋势",
                "intents": ["trend"],
                "priority": 10,
            },
        ],
        llm_factory=fail_if_called,
    )

    result = asyncio.run(
        service.recommend(
            question="最近7天播放趋势",
            query_plan={"intent": "trend"},
            answer_summary="整体上升。",
            current_tables=["play_session"],
            access_policy=_policy("play_session"),
        )
    )

    assert result.questions == (
        "把时间范围改为上个月",
        "与上一周期对比同一指标",
        "按周查看同一指标趋势",
    )
    assert result.source == "manual"
    assert result.llm_calls == 0


def test_ai_fill_is_deduplicated_and_filtered_by_permissions_and_sensitive_terms():
    class FakeLLM:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                content=json.dumps(
                    [
                        {
                            "question": "按天查看播放次数趋势",
                            "required_tables": ["play_session"],
                        },
                        {
                            "question": "把时间范围改为上个月",
                            "required_tables": [],
                        },
                        {
                            "question": "查看订单金额",
                            "required_tables": ["content_order"],
                        },
                        {
                            "question": "查看会员手机号明细",
                            "required_tables": ["play_session"],
                        },
                    ],
                    ensure_ascii=False,
                )
            )

    service = RecommendationService(
        manual_recommendations=[
            {
                "question": "把时间范围改为上个月",
                "intents": ["trend"],
                "priority": 10,
            }
        ],
        llm_factory=lambda: FakeLLM(),
    )
    result = asyncio.run(
        service.recommend(
            question="最近7天播放趋势",
            query_plan={"intent": "trend"},
            answer_summary="整体上升。",
            current_tables=["play_session"],
            access_policy=_policy("play_session"),
        )
    )

    assert result.questions[0] == "把时间范围改为上个月"
    assert "按天查看播放次数趋势" in result.questions
    assert all("订单" not in question for question in result.questions)
    assert all("手机号" not in question for question in result.questions)
    assert len(result.questions) == 3
    assert result.source == "hybrid"
    assert result.llm_calls == 1
