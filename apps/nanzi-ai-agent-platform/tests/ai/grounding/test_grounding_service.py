from unittest.mock import patch

import pytest

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.policy import (
    FactRequirement,
    GroundingAction,
    GroundingRiskLevel,
)
from app.services.ai.grounding.service import GroundingService


pytestmark = pytest.mark.no_infrastructure


def test_audit_returns_pass_without_warning_for_matching_evidence():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-1",
        producer="execute_sql_query",
        evidence_types={EvidenceType.INTERNAL_DATA},
        result={"rows": [{"name": "王强", "amount": 100}]},
    )

    result = GroundingService.audit(
        candidate_text="王强本月金额为 100 万元。",
        requirement=FactRequirement(
            required=True,
            accepted_types=frozenset({EvidenceType.INTERNAL_DATA}),
        ),
        ledger=ledger,
    )

    assert result.decision.action == GroundingAction.PASS
    assert result.warning_chunk is None
    assert result.should_warn is False


def test_audit_returns_standard_warning_payload_for_missing_evidence():
    result = GroundingService.audit(
        candidate_text="当前销售额排名第一的是王强，金额为 663.98 万元。",
        requirement=FactRequirement(
            required=True,
            accepted_types=frozenset({EvidenceType.INTERNAL_DATA}),
        ),
        ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
    )

    assert result.decision.action == GroundingAction.PASS_WITH_WARNING
    assert result.should_warn is True
    assert result.warning_chunk is not None
    assert "风险提示" in result.warning_chunk["content"]
    assert result.warning_chunk["grounding_risk"] == {
        "level": "high",
        "reason": "required evidence receipt is missing",
        "required_evidence_types": ["internal_data"],
        "available_evidence_types": [],
    }


def test_audit_returns_pass_when_explicitly_disabled():
    result = GroundingService.audit(
        candidate_text="当前销售额排名第一的是王强，金额为 663.98 万元。",
        requirement=FactRequirement(
            required=True,
            accepted_types=frozenset({EvidenceType.INTERNAL_DATA}),
        ),
        ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
        enabled=False,
    )

    assert result.decision.action == GroundingAction.PASS
    assert result.should_warn is False


def test_warning_chunk_can_reuse_an_external_guard_reason():
    chunk = GroundingService.warning_chunk(
        risk_level=GroundingRiskLevel.HIGH,
        reason="知识库反思仍未通过",
        required_types=frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
    )

    assert "风险提示" in chunk["content"]
    assert chunk["grounding_risk"]["reason"] == "知识库反思仍未通过"
    assert chunk["grounding_risk"]["required_evidence_types"] == [
        "internal_knowledge"
    ]


def test_audit_converts_legacy_block_decision_to_soft_warning():
    from app.services.ai.grounding.policy import GroundingDecision

    legacy_decision = GroundingDecision(
        action=GroundingAction.BLOCK_UNGROUNDED_FACTS,
        reason="legacy block decision",
        required_evidence_types=frozenset({EvidenceType.INTERNAL_DATA}),
    )
    with patch(
        "app.services.ai.grounding.service.evaluate_grounding",
        return_value=legacy_decision,
    ):
        result = GroundingService.audit(
            candidate_text="当前销售额为 100 万元。",
            requirement=FactRequirement(
                required=True,
                accepted_types=frozenset({EvidenceType.INTERNAL_DATA}),
            ),
            ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
        )

    assert result.should_warn is True
    assert result.warning_chunk["grounding_risk"]["level"] == "high"
