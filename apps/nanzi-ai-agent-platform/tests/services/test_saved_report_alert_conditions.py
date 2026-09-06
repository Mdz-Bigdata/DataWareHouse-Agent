import pytest

from app.services.saved_report_subscription_service import evaluate_alert_condition


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    ("condition", "rows", "hit"),
    [
        ({"version": 1, "type": "threshold", "field": "amount", "operator": ">", "value": 100}, [{"amount": 120}], True),
        ({"version": 1, "type": "threshold", "field": "amount", "operator": "<=", "value": 100}, [{"amount": 120}], False),
        ({"version": 1, "type": "no_data"}, [], True),
        (None, [{"amount": 1}], True),
    ],
)
def test_alert_condition_basics(condition, rows, hit):
    result = evaluate_alert_condition(condition, rows, {})
    assert result.hit is hit
    assert result.evidence["condition_type"] == (condition or {}).get("type", "always")


def test_rate_of_change_uses_persisted_baseline():
    condition = {"version": 1, "type": "rate_of_change", "field": "amount", "operator": ">=", "value": 20}
    result = evaluate_alert_condition(condition, [{"amount": 130}], {"last_value": 100})
    assert result.hit is True
    assert result.evidence["change_percent"] == 30.0


def test_consecutive_hits_require_configured_count():
    condition = {"version": 1, "type": "threshold", "field": "amount", "operator": ">", "value": 100, "consecutive_hits": 2}
    first = evaluate_alert_condition(condition, [{"amount": 120}], {})
    second = evaluate_alert_condition(condition, [{"amount": 130}], first.next_state)
    assert first.hit is False
    assert second.hit is True
    assert second.next_state["consecutive_hits"] == 2
