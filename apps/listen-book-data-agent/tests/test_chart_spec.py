from datetime import date

import pytest

from app.services.chart_spec_service import (
    ChartSpecV1,
    ChartSpecValidationError,
    build_chart_spec,
    validate_chart_spec,
)


def test_builds_kpi_line_bar_and_table_specs_deterministically():
    kpi = build_chart_spec(["播放量"], [{"播放量": "12.5"}])
    line = build_chart_spec(
        ["日期", "播放量"],
        [
            {"日期": date(2026, 7, 1), "播放量": 10},
            {"日期": date(2026, 7, 2), "播放量": 12},
        ],
    )
    bar = build_chart_spec(
        ["渠道", "播放量"],
        [{"渠道": "自然", "播放量": 10}, {"渠道": "广告", "播放量": 8}],
    )
    table = build_chart_spec(["专辑"], [{"专辑": "A"}, {"专辑": "B"}])

    assert kpi.type == "kpi"
    assert line.type == "line" and line.dimension == "日期"
    assert bar.type == "bar" and bar.metrics == ["播放量"]
    assert table.type == "table"
    assert {item.schema_version for item in (kpi, line, bar, table)} == {
        "chart-spec/v1"
    }


def test_rejects_unknown_or_type_incompatible_suggestion_fields():
    rows = [{"渠道": "自然", "播放量": 10}, {"渠道": "广告", "播放量": 8}]
    with pytest.raises(ChartSpecValidationError, match="不存在"):
        validate_chart_spec(
            ChartSpecV1(
                type="bar",
                title="恶意字段",
                dimension="渠道",
                metrics=["用户手机号"],
            ),
            ["渠道", "播放量"],
            rows,
        )
    with pytest.raises(ChartSpecValidationError, match="数值列"):
        validate_chart_spec(
            ChartSpecV1(
                type="bar",
                title="类型错误",
                dimension="播放量",
                metrics=["渠道"],
            ),
            ["渠道", "播放量"],
            rows,
        )


def test_invalid_llm_suggestion_falls_back_and_valid_one_is_marked():
    columns = ["渠道", "播放量"]
    rows = [{"渠道": "自然", "播放量": 10}, {"渠道": "广告", "播放量": 8}]
    invalid = build_chart_spec(
        columns,
        rows,
        {"type": "pie", "title": "bad", "dimension": "渠道", "metrics": ["密钥"]},
    )
    valid = build_chart_spec(
        columns,
        rows,
        {
            "type": "pie",
            "title": "渠道占比",
            "dimension": "渠道",
            "metrics": ["播放量"],
        },
    )

    assert invalid.type == "bar" and invalid.source == "deterministic"
    assert valid.type == "pie" and valid.source == "llm_validated"
