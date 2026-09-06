import pytest
from pydantic import ValidationError

from app.agent.dsl import (
    DSLCompilationError,
    DSLCompiler,
    DSLValidationError,
    QueryDSL,
    validate_query_dsl,
)
from app.agent.dsl.normalizer import (
    build_catalog_metric_dsl,
    build_status_compare_dsl,
    normalize_query_dsl,
)

TABLE_INFOS = [
    {
        "id": "play_session",
        "name": "play_session",
        "columns": [
            {"id": "play_session.id", "name": "id"},
            {"id": "play_session.album_id", "name": "album_id"},
            {"id": "play_session.play_status", "name": "play_status"},
            {"id": "play_session.played_seconds", "name": "played_seconds"},
            {"id": "play_session.play_start_at", "name": "play_start_at"},
        ],
    },
    {
        "id": "audio_album",
        "name": "audio_album",
        "columns": [
            {"id": "audio_album.id", "name": "id"},
            {"id": "audio_album.album_name", "name": "album_name"},
        ],
    },
]

METRICS = [
    {
        "id": "play_count",
        "name": "play_count",
        "alias": ["播放次数"],
        "formula": "COUNT(play_session.id)",
        "filters": [],
        "time_column": "play_session.play_start_at",
        "dimensions": ["play_session.album_id"],
    },
    {
        "id": "average_played_seconds",
        "name": "average_played_seconds",
        "alias": ["平均播放时长"],
        "formula": "AVG(play_session.played_seconds)",
        "filters": [],
        "time_column": "play_session.play_start_at",
        "dimensions": ["play_session.album_id"],
    },
]

RELATIONSHIPS = [
    {
        "id": "play_session.album",
        "source_table": "play_session",
        "source_column": "album_id",
        "target_table": "audio_album",
        "target_column": "id",
        "physical": True,
    }
]


def compile_dsl(payload: dict) -> str:
    dsl = QueryDSL.model_validate(payload)
    validate_query_dsl(dsl, METRICS, TABLE_INFOS, {}, 500)
    return DSLCompiler(500).compile(dsl, METRICS, RELATIONSHIPS, TABLE_INFOS, dialect="mysql")


def test_compiler_builds_aggregate_with_metric_filter_and_time_range():
    sql = compile_dsl(
        {
            "intent": "aggregate",
            "measures": [{"metric": "play_count", "alias": "播放次数"}],
            "time_range": {"start": "2026-07-01", "end": "2026-07-31"},
            "limit": 1,
        }
    )

    assert "COUNT(play_session.id) AS `播放次数`" in sql
    assert "play_session.play_start_at >= '2026-07-01'" in sql
    assert "LIMIT 1" in sql


def test_compiler_builds_trend_with_time_bucket():
    sql = compile_dsl(
        {
            "intent": "trend",
            "measures": [{"metric": "play_count", "alias": "播放次数"}],
            "time_range": {"start": "2026-07-01", "end": "2026-07-31"},
            "time_grain": "day",
            "limit": 500,
        }
    )

    assert "DATE(play_session.play_start_at) AS `时间`" in sql
    assert "GROUP BY DATE(play_session.play_start_at)" in sql
    assert "ORDER BY `时间` ASC" in sql


def test_compiler_deduplicates_trend_time_dimension_and_forces_chronological_order():
    sql = compile_dsl(
        {
            "intent": "trend",
            "measures": [{"metric": "play_count", "alias": "播放次数"}],
            "dimensions": [{"column": "play_session.play_start_at", "alias": "日期"}],
            "time_column": "play_session.play_start_at",
            "time_grain": "day",
            "order_by": [{"target": "播放次数", "direction": "desc"}],
        }
    )

    assert sql.count("play_session.play_start_at") == 2
    assert "ORDER BY `时间` ASC" in sql


def test_compiler_builds_ranking_with_authorized_join():
    sql = compile_dsl(
        {
            "intent": "ranking",
            "measures": [{"metric": "play_count", "alias": "播放次数"}],
            "dimensions": [{"column": "audio_album.album_name", "alias": "专辑"}],
            "order_by": [{"target": "播放次数", "direction": "desc"}],
            "limit": 10,
        }
    )

    assert "JOIN audio_album ON play_session.album_id = audio_album.id" in sql
    assert "GROUP BY audio_album.id, audio_album.album_name" in sql
    assert "ORDER BY `播放次数` DESC" in sql
    assert "`专辑` ASC" in sql


def test_compiler_builds_compare_with_conditional_aggregates():
    sql = compile_dsl(
        {
            "intent": "compare",
            "measures": [
                {
                    "metric": "average_played_seconds",
                    "alias": "完播平均时长",
                    "filters": [
                        {
                            "logic": "and",
                            "clauses": [
                                {
                                    "column": "play_session.play_status",
                                    "operator": "eq",
                                    "value": "completed",
                                }
                            ],
                        }
                    ],
                },
                {
                    "metric": "average_played_seconds",
                    "alias": "未完播平均时长",
                    "filters": [
                        {
                            "logic": "and",
                            "clauses": [
                                {
                                    "column": "play_session.play_status",
                                    "operator": "eq",
                                    "value": "playing",
                                }
                            ],
                        }
                    ],
                },
            ],
            "comparison": {
                "operation": "difference",
                "left_measure": "完播平均时长",
                "right_measure": "未完播平均时长",
                "alias": "平均时长差",
            },
            "limit": 1,
        }
    )

    assert "AVG(CASE WHEN" in sql
    assert "AS `平均时长差`" in sql


def test_compiler_builds_detail_without_aggregate():
    sql = compile_dsl(
        {
            "intent": "detail",
            "dimensions": [
                {"column": "play_session.id", "alias": "会话ID"},
                {"column": "play_session.play_status", "alias": "状态"},
            ],
            "filters": [
                {
                    "logic": "and",
                    "clauses": [
                        {
                            "column": "play_session.play_status",
                            "operator": "eq",
                            "value": "completed",
                        }
                    ],
                }
            ],
            "order_by": [{"target": "play_session.id", "direction": "desc"}],
            "limit": 20,
        }
    )

    assert "play_session.id AS `会话ID`" in sql
    assert "play_session.play_status = 'completed'" in sql
    assert "ORDER BY `会话ID` DESC" in sql
    assert "GROUP BY" not in sql


def test_compiler_adds_stable_primary_key_order_to_detail():
    sql = compile_dsl(
        {
            "intent": "detail",
            "dimensions": [
                {"column": "play_session.id", "alias": "ID"},
                {"column": "play_session.play_start_at", "alias": "时间"},
            ],
            "order_by": [{"target": "play_session.play_start_at", "direction": "desc"}],
            "limit": 5,
        }
    )

    assert "ORDER BY `时间` DESC, `ID` DESC" in sql


def test_catalog_metric_dsl_fast_path_uses_no_model_judgment():
    dsl = build_catalog_metric_dsl(
        "平台累计播放次数是多少",
        METRICS,
        max_result_rows=500,
    )

    assert dsl is not None
    assert dsl.intent == "aggregate"
    assert dsl.measures[0].metric == "play_count"
    assert dsl.dimensions == []


@pytest.mark.parametrize(
    ("question", "metric_id", "expected_values"),
    [
        ("完播和中断播放次数相差多少", "play_count", ["completed", "interrupted"]),
        (
            "完播和中断的平均播放时长差多少",
            "average_played_seconds",
            ["completed", "interrupted"],
        ),
    ],
)
def test_status_compare_dsl_uses_explicit_statuses(
    question: str,
    metric_id: str,
    expected_values: list[str],
):
    dsl = build_status_compare_dsl(
        question,
        METRICS,
        TABLE_INFOS,
        max_result_rows=500,
    )

    assert dsl is not None
    assert [measure.metric for measure in dsl.measures] == [metric_id, metric_id]
    assert [measure.filters[0].clauses[0].value for measure in dsl.measures] == expected_values
    assert dsl.comparison is not None
    assert dsl.comparison.operation == "difference"


def test_status_compare_dsl_requires_recalled_status_column():
    assert (
        build_status_compare_dsl(
            "完播和失败播放次数相差多少",
            METRICS,
            [],
            max_result_rows=500,
        )
        is None
    )


def test_normalizer_prefers_named_ranking_dimension_and_adds_detail_key():
    ranking = normalize_query_dsl(
        "播放次数排名前5的专辑",
        QueryDSL.model_validate(
            {
                "intent": "ranking",
                "measures": [{"metric": "play_count", "alias": "播放次数"}],
                "dimensions": [{"column": "audio_album.id", "alias": "专辑"}],
                "order_by": [{"target": "播放次数", "direction": "desc"}],
                "limit": 5,
            }
        ),
        METRICS,
        TABLE_INFOS,
    )
    detail = normalize_query_dsl(
        "查看最近5条播放明细",
        QueryDSL.model_validate(
            {
                "intent": "detail",
                "dimensions": [{"column": "play_session.play_status", "alias": "状态"}],
                "limit": 5,
            }
        ),
        METRICS,
        TABLE_INFOS,
    )

    assert ranking.dimensions[0].column == "audio_album.album_name"
    assert detail.dimensions[0].column == "play_session.id"


def test_normalizer_injects_single_value_required_filter():
    dsl = normalize_query_dsl(
        "查看最近5条完播记录明细",
        QueryDSL.model_validate(
            {
                "intent": "detail",
                "dimensions": [
                    {"column": "play_session.id", "alias": "ID"},
                    {"column": "play_session.play_start_at", "alias": "时间"},
                ],
                "limit": 5,
            }
        ),
        METRICS,
        TABLE_INFOS,
        {
            "filter_requirements": [
                {
                    "columns": ["play_session.play_status"],
                    "values": ["completed"],
                    "value_match": "exact",
                    "location": "where",
                }
            ]
        },
    )

    assert dsl.filters[0].clauses[0].column == "play_session.play_status"
    assert dsl.filters[0].clauses[0].value == "completed"


def test_normalizer_orders_detail_time_before_status():
    dsl = normalize_query_dsl(
        "查看最近5条完播记录明细",
        QueryDSL.model_validate(
            {
                "intent": "detail",
                "dimensions": [
                    {"column": "play_session.play_status", "alias": "播放状态"},
                    {"column": "play_session.play_start_at", "alias": "播放开始时间"},
                ],
                "limit": 5,
            }
        ),
        METRICS,
        TABLE_INFOS,
    )

    assert [item.column for item in dsl.dimensions] == [
        "play_session.id",
        "play_session.play_start_at",
        "play_session.play_status",
    ]


def test_normalizer_aligns_same_name_ranking_dimension_to_metric_table():
    metrics = [
        {
            "id": "search_count",
            "name": "search_count",
            "alias": ["搜索次数"],
            "formula": "COUNT(search_query_log.id)",
            "filters": [],
            "dimensions": ["search_query_log.keyword"],
        }
    ]
    dsl = normalize_query_dsl(
        "搜索次数排名前5的关键词",
        QueryDSL.model_validate(
            {
                "intent": "ranking",
                "measures": [{"metric": "search_count", "alias": "搜索次数"}],
                "dimensions": [{"column": "search_keyword_stat.keyword", "alias": "关键词"}],
                "order_by": [{"target": "搜索次数", "direction": "desc"}],
                "limit": 5,
            }
        ),
        metrics,
        [
            {
                "id": "search_keyword_stat",
                "name": "search_keyword_stat",
                "columns": [
                    {
                        "id": "search_keyword_stat.keyword",
                        "name": "keyword",
                    }
                ],
            }
        ],
    )

    assert dsl.dimensions[0].column == "search_query_log.keyword"


def test_semantic_validation_rejects_unrecalled_columns_and_raw_sql_fields():
    dsl = QueryDSL.model_validate(
        {
            "intent": "aggregate",
            "measures": [{"metric": "play_count", "alias": "播放次数"}],
            "filters": [
                {"clauses": [{"column": "user_account.phone", "operator": "eq", "value": "1"}]}
            ],
        }
    )
    with pytest.raises(DSLValidationError, match="字段未在本次语义上下文中召回"):
        validate_query_dsl(dsl, METRICS, TABLE_INFOS, {}, 500)

    with pytest.raises(ValidationError):
        QueryDSL.model_validate(
            {
                "intent": "aggregate",
                "measures": [{"metric": "play_count", "alias": "播放次数"}],
                "sql": "SELECT * FROM users",
            }
        )


def test_compare_raises_for_metric_formula_that_cannot_be_conditionally_scoped():
    unsupported_metrics = [
        {
            "id": "completion_rate",
            "formula": "COALESCE(SUM(play_session.played_seconds), 0)",
            "filters": [],
            "time_column": "play_session.play_start_at",
        }
    ]
    dsl = QueryDSL.model_validate(
        {
            "intent": "compare",
            "measures": [
                {
                    "metric": "completion_rate",
                    "alias": "A",
                    "filters": [
                        {
                            "clauses": [
                                {
                                    "column": "play_session.play_status",
                                    "operator": "eq",
                                    "value": "a",
                                }
                            ]
                        }
                    ],
                },
                {
                    "metric": "completion_rate",
                    "alias": "B",
                    "filters": [
                        {
                            "clauses": [
                                {
                                    "column": "play_session.play_status",
                                    "operator": "eq",
                                    "value": "b",
                                }
                            ]
                        }
                    ],
                },
            ],
            "comparison": {
                "operation": "difference",
                "left_measure": "A",
                "right_measure": "B",
                "alias": "差值",
            },
        }
    )
    with pytest.raises(DSLCompilationError, match="不支持分组对比"):
        DSLCompiler(500).compile(
            dsl,
            unsupported_metrics,
            RELATIONSHIPS,
            TABLE_INFOS,
            dialect="mysql",
        )
