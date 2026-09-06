from app.agent.analysis_plan import build_analysis_plan
from app.services.deterministic_sql_service import build_catalog_metric_sql, find_catalog_metric

METRICS = [
    {
        "id": "paid_album_count",
        "alias": ["付费专辑数"],
        "formula": "COUNT(DISTINCT album_price_rule.album_id)",
        "filters": [
            "album_price_rule.yn = 1",
            "album_price_rule.price_type <> 'free'",
        ],
        "currency_column": None,
        "relevant_columns": ["album_price_rule.album_id"],
    },
    {
        "id": "ranking_score",
        "alias": ["榜单分数"],
        "formula": "AVG(ranking_item.score_value)",
        "filters": [],
        "currency_column": None,
        "relevant_columns": ["ranking_item.score_value"],
    },
    {
        "id": "ranking_play_count",
        "alias": ["榜单播放量"],
        "formula": "COALESCE(SUM(ranking_item.play_count), 0)",
        "filters": [],
        "currency_column": None,
        "relevant_columns": ["ranking_item.play_count"],
    },
]


def test_catalog_metric_sql_preserves_formula_and_fixed_filters():
    sql = build_catalog_metric_sql("平台当前付费专辑数是多少", METRICS)

    assert sql is not None
    assert "COUNT(DISTINCT album_price_rule.album_id)" in sql
    assert "album_price_rule.yn = 1" in sql
    assert "album_price_rule.price_type <> 'free'" in sql


def test_catalog_metric_sql_preserves_average_not_sum():
    sql = build_catalog_metric_sql("平台累计榜单分数是多少", METRICS)

    assert sql is not None
    assert "AVG(ranking_item.score_value)" in sql
    assert "SUM(ranking_item.score_value)" not in sql


def test_catalog_metric_sql_does_not_absorb_ranking_question():
    assert build_catalog_metric_sql("榜单分数排名前10的专辑", METRICS) is None


def test_catalog_metric_can_be_found_without_retrieval_candidates():
    metric = find_catalog_metric("平台累计榜单播放量是多少", [])

    assert metric is not None
    assert metric["id"] == "ranking_play_count"


def test_catalog_lookup_normalizes_natural_count_questions():
    questions = {
        "平台一共有多少个有声专辑": "album_count",
        "平台当前有多少个已发布的有声专辑": "published_album_count",
        "平台一共有多少个音频章节": "track_count",
        "平台当前有多少位作者": "author_count",
        "平台当前有多少位启用的主播": "narrator_count",
        "平台当前有效会员用户数是多少": "active_member_count",
    }

    assert {
        question: find_catalog_metric(question, [])["id"] for question in questions
    } == questions


def test_ranking_play_count_does_not_inherit_play_session_count_requirement():
    plan = build_analysis_plan("平台累计榜单播放量是多少").to_state()

    assert plan["metric_requirements"] == []
