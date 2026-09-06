import pytest

from app.api.portal.endpoints.chatbi_monitors import (
    build_chatbi_monitor_report_id,
    validate_alert_condition_fields,
)


pytestmark = pytest.mark.no_infrastructure


def test_monitor_id_is_stable_for_repeated_result_action():
    first = build_chatbi_monitor_report_id("7", "conversation-a", "result-1")
    second = build_chatbi_monitor_report_id("7", "conversation-a", "result-1")
    assert first == second
    assert first != build_chatbi_monitor_report_id("7", "conversation-a", "result-2")


def test_alert_field_must_exist_in_bound_result():
    validate_alert_condition_fields(
        {"type": "threshold", "field": "sales", "operator": ">", "value": 10},
        [{"region": "华东", "sales": 20}],
    )
    with pytest.raises(ValueError, match="告警字段无效"):
        validate_alert_condition_fields(
            {"type": "threshold", "field": "unknown", "operator": ">", "value": 10},
            [{"region": "华东", "sales": 20}],
        )
