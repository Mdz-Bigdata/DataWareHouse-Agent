"""可重复的问数准确率基准定义。

每条用例都包含面向用户的自然语言问题及其独立的标准 SQL。评测时由标准 SQL
计算真值，再与 /api/query/sync 的返回结果作数值比较；不能仅以“接口成功返回”
作为准确率依据。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.metric_baseline_service import (
    build_metric_reference_sql,
    load_audio_metrics,
    metric_question,
)


@dataclass(frozen=True)
class QueryAccuracyCase:
    """单指标、单行结果的端到端准确率用例。"""

    case_id: str
    question: str
    reference_sql: str
    category: str
    metric_name: str | None = None
    intent: str = "aggregate"


CORE_ACCURACY_CASES: tuple[QueryAccuracyCase, ...] = (
    QueryAccuracyCase(
        "album_count",
        "平台一共有多少个有声专辑",
        "SELECT COUNT(DISTINCT id) AS value FROM audio_album",
        "内容",
    ),
    QueryAccuracyCase(
        "published_album_count",
        "平台当前有多少个已发布的有声专辑",
        "SELECT COUNT(DISTINCT id) AS value FROM audio_album WHERE album_status = 'published'",
        "内容",
    ),
    QueryAccuracyCase(
        "track_count",
        "平台一共有多少个音频章节",
        "SELECT COUNT(DISTINCT id) AS value FROM audio_track",
        "内容",
    ),
    QueryAccuracyCase(
        "published_track_count",
        "平台当前有多少个已发布章节",
        "SELECT COUNT(DISTINCT id) AS value FROM audio_track WHERE track_status = 'published'",
        "内容",
    ),
    QueryAccuracyCase(
        "author_count",
        "平台当前有多少位作者",
        "SELECT COUNT(DISTINCT id) AS value FROM content_author WHERE yn = 1",
        "内容",
    ),
    QueryAccuracyCase(
        "narrator_count",
        "平台当前有多少位启用的主播",
        "SELECT COUNT(DISTINCT id) AS value FROM content_narrator WHERE yn = 1",
        "内容",
    ),
    QueryAccuracyCase(
        "play_count",
        "平台总播放次数是多少",
        "SELECT COUNT(id) AS value FROM play_session",
        "播放",
    ),
    QueryAccuracyCase(
        "unique_listener_count",
        "平台总收听人数是多少",
        "SELECT COUNT(DISTINCT user_id) AS value FROM play_session",
        "播放",
    ),
    QueryAccuracyCase(
        "favorite_count",
        "平台当前收藏数是多少",
        "SELECT COUNT(id) AS value FROM user_bookshelf WHERE shelf_status = 'active'",
        "互动",
    ),
    QueryAccuracyCase(
        "approved_comment_count",
        "平台当前有效评论数是多少",
        "SELECT COUNT(id) AS value FROM content_comment WHERE audit_status = 'approved'",
        "互动",
    ),
    QueryAccuracyCase(
        "active_member_count",
        "平台当前有效会员用户数是多少",
        "SELECT COUNT(DISTINCT user_id) AS value FROM member_account "
        "WHERE member_status = 'active' AND valid_from <= CURRENT_TIMESTAMP "
        "AND valid_to >= CURRENT_TIMESTAMP",
        "会员",
    ),
    QueryAccuracyCase(
        "paid_content_order_count",
        "平台成交订单数是多少",
        "SELECT COUNT(id) AS value FROM content_order WHERE order_status = 'paid'",
        "交易",
    ),
    QueryAccuracyCase(
        "successful_payment_count",
        "平台成功支付笔数是多少",
        "SELECT COUNT(id) AS value FROM payment_record WHERE payment_status = 'success'",
        "交易",
    ),
    QueryAccuracyCase(
        "successful_refund_count",
        "平台成功退款笔数是多少",
        "SELECT COUNT(id) AS value FROM refund_record WHERE refund_status = 'success'",
        "交易",
    ),
    QueryAccuracyCase(
        "published_topic_count",
        "平台当前已发布专题数是多少",
        "SELECT COUNT(id) AS value FROM content_topic WHERE topic_status = 'published'",
        "运营",
    ),
    QueryAccuracyCase(
        "search_count",
        "平台总搜索次数是多少",
        "SELECT COUNT(id) AS value FROM search_query_log",
        "搜索",
    ),
)


# These cases deliberately exercise shapes the canonical single-metric catalog cannot
# measure: joins, grouped time series, rankings, conditional comparisons and details.
# Their reference SQL is independent from the DSL compiler and is executed by the evaluator.
DSL_COMPARISON_CASES: tuple[QueryAccuracyCase, ...] = (
    QueryAccuracyCase(
        "aggregate_completed_play",
        "平台累计完播次数是多少",
        "SELECT COUNT(id) AS value FROM play_session WHERE play_status = 'completed'",
        "DSL 对照",
        intent="aggregate",
    ),
    QueryAccuracyCase(
        "aggregate_played_seconds",
        "平台累计播放时长是多少",
        "SELECT COALESCE(SUM(played_seconds), 0) AS value FROM play_session",
        "DSL 对照",
        intent="aggregate",
    ),
    QueryAccuracyCase(
        "aggregate_published_tracks",
        "平台已发布章节数是多少",
        "SELECT COUNT(id) AS value FROM audio_track WHERE track_status = 'published'",
        "DSL 对照",
        intent="aggregate",
    ),
    QueryAccuracyCase(
        "aggregate_album_duration",
        "平台专辑总时长是多少",
        "SELECT COALESCE(SUM(total_duration_seconds), 0) AS value FROM audio_album",
        "DSL 对照",
        intent="aggregate",
    ),
    QueryAccuracyCase(
        "aggregate_search_clicks",
        "平台累计搜索点击次数是多少",
        "SELECT COUNT(id) AS value FROM search_query_log WHERE clicked_flag = 1",
        "DSL 对照",
        intent="aggregate",
    ),
    QueryAccuracyCase(
        "trend_daily_plays",
        "按天看播放次数趋势",
        "SELECT DATE(play_start_at) AS bucket, COUNT(id) AS value FROM play_session GROUP BY DATE(play_start_at) ORDER BY bucket LIMIT 500",
        "DSL 对照",
        intent="trend",
    ),
    QueryAccuracyCase(
        "trend_daily_completed",
        "按天看完播次数趋势",
        "SELECT DATE(play_start_at) AS bucket, COUNT(id) AS value FROM play_session WHERE play_status = 'completed' GROUP BY DATE(play_start_at) ORDER BY bucket LIMIT 500",
        "DSL 对照",
        intent="trend",
    ),
    QueryAccuracyCase(
        "trend_monthly_plays",
        "按月看播放次数趋势",
        "SELECT DATE_FORMAT(play_start_at, '%Y-%m') AS bucket, COUNT(id) AS value FROM play_session GROUP BY DATE_FORMAT(play_start_at, '%Y-%m') ORDER BY bucket LIMIT 500",
        "DSL 对照",
        intent="trend",
    ),
    QueryAccuracyCase(
        "trend_daily_new_albums",
        "按天看新增专辑趋势",
        "SELECT DATE(created_at) AS bucket, COUNT(id) AS value FROM audio_album GROUP BY DATE(created_at) ORDER BY bucket LIMIT 500",
        "DSL 对照",
        intent="trend",
    ),
    QueryAccuracyCase(
        "trend_daily_searches",
        "按天看搜索次数趋势",
        "SELECT DATE(created_at) AS bucket, COUNT(id) AS value FROM search_query_log GROUP BY DATE(created_at) ORDER BY bucket LIMIT 500",
        "DSL 对照",
        intent="trend",
    ),
    QueryAccuracyCase(
        "ranking_album_plays",
        "播放次数排名前5的专辑",
        "SELECT aa.album_title AS dimension, COUNT(ps.id) AS value FROM play_session ps JOIN audio_album aa ON ps.album_id = aa.id GROUP BY aa.id, aa.album_title ORDER BY value DESC, dimension ASC LIMIT 5",
        "DSL 对照",
        intent="ranking",
    ),
    QueryAccuracyCase(
        "ranking_album_tracks",
        "章节数排名前5的专辑",
        "SELECT aa.album_title AS dimension, COUNT(at.id) AS value FROM audio_album aa JOIN audio_track at ON at.album_id = aa.id GROUP BY aa.id, aa.album_title ORDER BY value DESC, dimension ASC LIMIT 5",
        "DSL 对照",
        intent="ranking",
    ),
    QueryAccuracyCase(
        "ranking_category_albums",
        "专辑数排名前5的分类",
        "SELECT dac.category_name AS dimension, COUNT(aa.id) AS value FROM audio_album aa JOIN dim_audio_category dac ON aa.category_id = dac.id GROUP BY dac.id, dac.category_name ORDER BY value DESC, dimension ASC LIMIT 5",
        "DSL 对照",
        intent="ranking",
    ),
    QueryAccuracyCase(
        "ranking_search_keyword",
        "搜索次数排名前5的关键词",
        "SELECT keyword AS dimension, COUNT(id) AS value FROM search_query_log GROUP BY keyword ORDER BY value DESC, dimension ASC LIMIT 5",
        "DSL 对照",
        intent="ranking",
    ),
    QueryAccuracyCase(
        "ranking_channel_plays",
        "播放次数排名前5的渠道",
        "SELECT dc.channel_name AS dimension, COUNT(ps.id) AS value "
        "FROM play_session ps JOIN dim_channel dc ON ps.channel_id = dc.id "
        "GROUP BY dc.id, dc.channel_name ORDER BY value DESC, dimension ASC LIMIT 5",
        "DSL 对照",
        intent="ranking",
    ),
    QueryAccuracyCase(
        "compare_completed_vs_interrupted",
        "完播和中断播放次数相差多少",
        "SELECT COUNT(CASE WHEN play_status = 'completed' THEN id END) - COUNT(CASE WHEN play_status = 'interrupted' THEN id END) AS value FROM play_session",
        "DSL 对照",
        intent="compare",
    ),
    QueryAccuracyCase(
        "compare_completed_vs_failed",
        "完播和失败播放次数相差多少",
        "SELECT COUNT(CASE WHEN play_status = 'completed' THEN id END) - COUNT(CASE WHEN play_status = 'failed' THEN id END) AS value FROM play_session",
        "DSL 对照",
        intent="compare",
    ),
    QueryAccuracyCase(
        "compare_play_duration",
        "完播和中断的平均播放时长差多少",
        "SELECT AVG(CASE WHEN play_status = 'completed' THEN played_seconds END) - AVG(CASE WHEN play_status = 'interrupted' THEN played_seconds END) AS value FROM play_session",
        "DSL 对照",
        intent="compare",
    ),
    QueryAccuracyCase(
        "compare_published_draft_albums",
        "已发布和草稿专辑数量相差多少",
        "SELECT COUNT(CASE WHEN album_status = 'published' THEN id END) - COUNT(CASE WHEN album_status = 'draft' THEN id END) AS value FROM audio_album",
        "DSL 对照",
        intent="compare",
    ),
    QueryAccuracyCase(
        "compare_clicked_not_clicked",
        "已点击和未点击搜索次数相差多少",
        "SELECT COUNT(CASE WHEN clicked_flag = 1 THEN id END) - COUNT(CASE WHEN clicked_flag = 0 THEN id END) AS value FROM search_query_log",
        "DSL 对照",
        intent="compare",
    ),
    QueryAccuracyCase(
        "detail_recent_albums",
        "查看最近创建的5个专辑明细",
        "SELECT id AS id, album_title AS title, created_at AS created_at FROM audio_album ORDER BY created_at DESC, id DESC LIMIT 5",
        "DSL 对照",
        intent="detail",
    ),
    QueryAccuracyCase(
        "detail_recent_tracks",
        "查看最近创建的5个章节明细",
        "SELECT id AS id, track_title AS title, created_at AS created_at FROM audio_track ORDER BY created_at DESC, id DESC LIMIT 5",
        "DSL 对照",
        intent="detail",
    ),
    QueryAccuracyCase(
        "detail_completed_plays",
        "查看最近5条完播记录明细",
        "SELECT id AS id, play_start_at AS play_start_at, play_status AS play_status "
        "FROM play_session WHERE play_status = 'completed' "
        "ORDER BY play_start_at DESC, id DESC LIMIT 5",
        "DSL 对照",
        intent="detail",
    ),
    QueryAccuracyCase(
        "detail_recent_searches",
        "查看最近5条搜索记录明细",
        "SELECT id AS id, keyword AS keyword, created_at AS created_at FROM search_query_log ORDER BY created_at DESC, id DESC LIMIT 5",
        "DSL 对照",
        intent="detail",
    ),
    QueryAccuracyCase(
        "detail_published_albums",
        "查看最近发布的5个专辑明细",
        "SELECT id AS id, album_title AS title, published_at AS published_at FROM audio_album WHERE album_status = 'published' ORDER BY published_at DESC, id DESC LIMIT 5",
        "DSL 对照",
        intent="detail",
    ),
)


def build_all_metric_accuracy_cases() -> tuple[QueryAccuracyCase, ...]:
    """Return one executable end-to-end case for every semantic metric."""

    return tuple(
        QueryAccuracyCase(
            case_id=f"metric_{metric['name']}",
            question=metric_question(metric),
            reference_sql=build_metric_reference_sql(metric),
            category="全量指标",
            metric_name=str(metric["name"]),
        )
        for metric in load_audio_metrics()
    )
