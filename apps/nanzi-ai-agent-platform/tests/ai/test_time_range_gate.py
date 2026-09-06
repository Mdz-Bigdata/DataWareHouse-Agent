from datetime import datetime

import pytest
import pytz

from app.services.ai.time_anchor import (
    detect_time_range_mismatch,
    extract_sql_date_literals,
    has_explicit_absolute_time,
    resolve_relative_time_expectation,
)

pytestmark = pytest.mark.no_infrastructure

TZ = pytz.timezone("Asia/Shanghai")
TZ_NAME = "Asia/Shanghai"
NOW = TZ.localize(datetime(2026, 6, 21, 10, 0, 0))


def test_resolve_last_month_expectation():
    exp = resolve_relative_time_expectation("帮我查上个月拜访记录", timezone=TZ_NAME, now=NOW)
    assert exp is not None
    assert exp.label == "上月"
    assert exp.start.isoformat() == "2026-05-01"
    assert exp.end.isoformat() == "2026-05-31"


def test_detect_mismatch_last_month_wrong_year():
    sql = (
        "SELECT * FROM t WHERE visit_date >= TO_DATE('2025-05-01', 'YYYY-MM-DD') "
        "AND visit_date < TO_DATE('2025-06-01', 'YYYY-MM-DD')"
    )
    reason = detect_time_range_mismatch("查询上个月拜访记录", sql, timezone=TZ_NAME, now=NOW)
    assert reason
    assert "上月" in reason
    assert "2026-05-01" in reason
    assert "2025" in reason


def test_detect_pass_correct_last_month():
    sql = (
        "SELECT * FROM t WHERE visit_date >= DATE '2026-05-01' "
        "AND visit_date < DATE '2026-06-01'"
    )
    assert detect_time_range_mismatch("查询上个月拜访记录", sql, timezone=TZ_NAME, now=NOW) == ""


def test_skip_when_user_gives_explicit_absolute_time():
    sql = (
        "SELECT * FROM t WHERE visit_date >= TO_DATE('2025-05-01', 'YYYY-MM-DD') "
        "AND visit_date < TO_DATE('2025-06-01', 'YYYY-MM-DD')"
    )
    assert detect_time_range_mismatch("查询2025年5月拜访记录", sql, timezone=TZ_NAME, now=NOW) == ""


def test_last_year_allows_previous_calendar_year():
    sql = (
        "SELECT * FROM t WHERE dt >= '2025-01-01' AND dt <= '2025-12-31'"
    )
    assert detect_time_range_mismatch("统计去年销售额", sql, timezone=TZ_NAME, now=NOW) == ""


def test_extract_sql_date_literals_multiple_formats():
    sql = (
        "WHERE a >= TO_DATE('2026-05-01', 'YYYY-MM-DD') "
        "AND b < DATE '2026-06-01' AND c = '2026/05/15'"
    )
    dates = extract_sql_date_literals(sql)
    date_set = {d.isoformat() for d in dates}
    assert "2026-05-01" in date_set
    assert "2026-06-01" in date_set
    assert "2026-05-15" in date_set


def test_has_explicit_absolute_time():
    assert has_explicit_absolute_time("查2025年5月数据")
    assert not has_explicit_absolute_time("查上个月数据")


def test_build_time_range_gate_message():
    from app.services.ai.time_anchor import TIME_RANGE_GATE_PREFIX, build_time_range_gate_message

    msg = build_time_range_gate_message("上月应为 2026-05-01 至 2026-05-31")
    assert msg.startswith(TIME_RANGE_GATE_PREFIX)
    assert "2026-05-01" in msg
