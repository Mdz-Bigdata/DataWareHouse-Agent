import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.runners.chatbi.drilldown_context import build_inherited_analysis_context


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


def test_build_inherited_analysis_context_preserves_conditions_without_reusing_sql_verbatim():
    result = {
        "result_id": "r2",
        "question": "查询本月华东区域销售额",
        "dataset_name": "销售数据集",
        "sql": "SELECT region, SUM(amount) FROM sales WHERE month='2026-07' GROUP BY region",
        "analysis_context": {
            "metrics": ["销售额"],
            "dimensions": ["区域"],
            "filters": {"区域": "华东"},
            "time_range": "本月",
            "time_grain": "月",
            "analysis_method": "汇总",
        },
    }

    prompt = build_inherited_analysis_context(result, "再按门店拆一下")

    assert "销售额" in prompt
    assert "华东" in prompt
    assert "本月" in prompt
    assert "门店" in prompt
    assert "重新基于当前 Schema 生成 SQL" in prompt
    assert "SELECT region" not in prompt


def test_build_inherited_analysis_context_handles_legacy_result():
    prompt = build_inherited_analysis_context(
        {"question": "查询最近30天订单趋势", "dataset_name": "订单"},
        "改成按周看",
    )
    assert "最近30天订单趋势" in prompt
    assert "按周" in prompt


def test_build_inherited_analysis_context_accepts_structured_filter_list():
    prompt = build_inherited_analysis_context(
        {
            "analysis_context": {
                "filters": [
                    {"field": "region", "operator": "=", "value": "华东"},
                    {"field": "status", "operator": "in", "value": ["有效"]},
                ]
            }
        },
        "再按门店拆一下",
    )
    assert '"field": "region"' in prompt
    assert '"value": "华东"' in prompt
