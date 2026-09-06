from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_chart_card_supports_table_view():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert "buildChartTableRows" in source
    assert "localChartTypes[idx] = 'table'" in source
    assert "title=\"切换为表格视图\"" in source
    assert "表格" in source
    assert "v-if=\"localChartTypes[idx] === 'table'\"" in source


def test_message_renderer_supports_clarification_card():
    source = _source("frontend/src/components/MessageRenderer.vue")

    assert ":::clarification" in source
    assert "'clarification'" in source
    assert "clarification-card" in source
    assert "需要你确认" in source
    assert "clarification-card__icon" in source
