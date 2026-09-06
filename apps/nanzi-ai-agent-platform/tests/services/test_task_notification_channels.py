import pytest

from app.services.task_notification_channels import (
    build_notification_delivery_supplement,
    channels_from_task_config,
    merge_notification_channels_into_config,
    normalize_notification_channels,
)


pytestmark = pytest.mark.no_infrastructure


def test_normalize_notification_channels_dedupes_and_filters():
    assert normalize_notification_channels(["portal", "PORTAL", "fax", "dingtalk"]) == [
        "portal",
        "dingtalk",
    ]
    assert normalize_notification_channels(None) == []
    assert normalize_notification_channels("portal") == []


def test_channels_from_task_config_and_merge_preserve_metrics():
    assert channels_from_task_config({"notification_channels": ["email", "portal"]}) == [
        "email",
        "portal",
    ]
    merged = merge_notification_channels_into_config(
        {"task_metrics": {"trigger_count": 2}},
        ["portal", "wechat_work"],
    )
    assert merged["task_metrics"]["trigger_count"] == 2
    assert merged["notification_channels"] == ["portal", "wechat_work"]

    cleared = merge_notification_channels_into_config(merged, [])
    assert "notification_channels" not in cleared
    assert cleared["task_metrics"]["trigger_count"] == 2


def test_build_notification_delivery_supplement_lists_tools_and_dedupe():
    text = build_notification_delivery_supplement(["portal", "dingtalk"])
    assert "【结果通知要求】" in text
    assert "send_portal_notification" in text
    assert "send_dingtalk_message" in text
    assert "每个渠道只发送一次" in text
    assert build_notification_delivery_supplement([]) == ""
