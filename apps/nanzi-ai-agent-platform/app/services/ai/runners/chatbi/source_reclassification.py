"""Reclassify source after ChatBI exhausts relevant Schema lookup."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.services.ai.request_decision import RequestSource, resolve_request_decision


class SchemaMissDisposition(str, Enum):
    KEEP_DATA_FAILURE = "keep_data_failure"
    DELEGATE_MAIN = "delegate_main"
    DELEGATE_WEB = "delegate_web"


@dataclass(frozen=True)
class SchemaMissSourceDecision:
    disposition: SchemaMissDisposition
    source: RequestSource
    confidence: float
    reason: str


def reclassify_schema_miss_source(query: str) -> SchemaMissSourceDecision:
    decision = resolve_request_decision(query)
    if decision.source == RequestSource.PUBLIC_WEB:
        disposition = SchemaMissDisposition.DELEGATE_WEB
    elif decision.source in {
        RequestSource.INTERNAL_DOCS,
        RequestSource.PLATFORM_SELF_HELP,
        RequestSource.RUNTIME_DIAGNOSTIC,
        RequestSource.GENERAL,
    }:
        disposition = SchemaMissDisposition.DELEGATE_MAIN
    else:
        disposition = SchemaMissDisposition.KEEP_DATA_FAILURE
    return SchemaMissSourceDecision(
        disposition=disposition,
        source=decision.source,
        confidence=decision.confidence,
        reason=decision.reasoning,
    )

