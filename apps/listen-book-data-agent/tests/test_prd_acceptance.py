"""PRD acceptance test suite: real LLM + real data closed-loop verification.

Requires the application to be running (default http://127.0.0.1:8000) and
full knowledge base built against `audio_full`.  Gated by the same flag as the
audio data acceptance tests so it does not run in pure CI.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import requests

BASE_URL = os.getenv("LISTENBOOK_API_URL", "http://127.0.0.1:8000")
SYNC_ENDPOINT = f"{BASE_URL}/api/query/sync"
QUERY_TIMEOUT_SECONDS = int(os.getenv("LISTENBOOK_ACCEPTANCE_TIMEOUT", "600"))


ACCEPTANCE_CASES = [
    pytest.param("最近30天播放量最高的前5个专辑", 1, id="album_chapter"),
    pytest.param("最近7天专辑的平均播放完成率", 1, id="play_completion_rate"),
    pytest.param("评分最高的前10个专辑", 1, id="comment_rating"),
    pytest.param("最近30天被收藏次数最多的前5个专辑", 1, id="favorite"),
    pytest.param("当前各类会员用户数", 1, id="membership"),
    pytest.param("最近30天有多少退款订单", 1, id="order_refund"),
    pytest.param("最近7天搜索次数最多的关键词", 1, id="search_ctr"),
    pytest.param("最近7天每天播放量趋势", 1, id="trend"),
    pytest.param("最近14天每天播放量", 1, id="compare"),
    pytest.param("本月评论数排名前10的专辑", 1, id="top_ranking"),
]


@pytest.mark.integration
@pytest.mark.parametrize("question,min_rows", ACCEPTANCE_CASES)
def test_prd_acceptance_query(question: str, min_rows: int):
    if os.getenv("RUN_AUDIO_DATA_ACCEPTANCE") != "1":
        pytest.skip("set RUN_AUDIO_DATA_ACCEPTANCE=1 to run local MySQL acceptance")

    result = _query_sync(question)

    assert result.get("status") == "completed", (
        f"query failed: {result.get('error')}"
    )

    sql = result.get("sql") or ""
    assert sql.strip().upper().startswith("SELECT"), (
        f"generated SQL is not a SELECT statement: {sql}"
    )

    row_count = result.get("row_count", 0)
    assert row_count >= min_rows, (
        f"expected at least {min_rows} rows, got {row_count}"
    )


def _query_sync(question: str) -> dict[str, Any]:
    response = requests.post(
        SYNC_ENDPOINT,
        json={"query": question},
        timeout=QUERY_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json"},
    )
    response.raise_for_status()
    return response.json()
