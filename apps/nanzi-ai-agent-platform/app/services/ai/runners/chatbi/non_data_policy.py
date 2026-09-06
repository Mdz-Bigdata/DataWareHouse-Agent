"""Disposition policy for requests that do not require a fresh SQL query."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.services.ai.intent_service import looks_like_context_action, looks_like_greeting
from app.services.ai.request_decision import RequestSource, resolve_request_decision


class NonDataDisposition(str, Enum):
    LOCAL_HELP = "local_help"
    RESULT_ACTION = "result_action"
    DELEGATE_MAIN = "delegate_main"
    DELEGATE_WEB = "delegate_web"


@dataclass(frozen=True)
class NonDataDecision:
    disposition: NonDataDisposition
    reason: str
    requires_result: bool = False


_BI_HELP_SIGNALS = (
    "同比", "环比", "占比", "贡献度", "指标口径", "统计口径",
    "维度", "下钻", "数据来源", "权限过滤", "怎么提问", "怎么查",
    "你能做什么", "你是谁", "能查什么", "可以查什么",
)


def resolve_non_data_disposition(
    query: str,
    *,
    has_last_data_result: bool,
) -> NonDataDecision:
    """Choose a completing action instead of returning a generic switch hint."""
    q = str(query or "").strip()
    is_result_action = looks_like_context_action(q)
    if is_result_action:
        if has_last_data_result:
            return NonDataDecision(
                NonDataDisposition.RESULT_ACTION,
                "request acts on the current structured result",
                requires_result=True,
            )
        return NonDataDecision(
            NonDataDisposition.LOCAL_HELP,
            "result action requested without a reusable structured result",
            requires_result=True,
        )

    if looks_like_greeting(q) or any(signal in q.lower() for signal in _BI_HELP_SIGNALS):
        return NonDataDecision(
            NonDataDisposition.LOCAL_HELP,
            "ChatBI can answer capability or BI-method help locally",
        )

    source = resolve_request_decision(q).source
    if source == RequestSource.PUBLIC_WEB:
        return NonDataDecision(
            NonDataDisposition.DELEGATE_WEB,
            "request requires public web evidence",
        )
    if source in {
        RequestSource.PLATFORM_SELF_HELP,
        RequestSource.RUNTIME_DIAGNOSTIC,
        RequestSource.INTERNAL_DOCS,
        RequestSource.GENERAL,
    }:
        return NonDataDecision(
            NonDataDisposition.DELEGATE_MAIN,
            f"request source {source.value} belongs to Main",
        )
    return NonDataDecision(
        NonDataDisposition.DELEGATE_MAIN,
        "unknown non-data request is safest in Main",
    )

