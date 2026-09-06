import pytest

pytestmark = pytest.mark.no_infrastructure


def test_parse_semantic_intent_payload_keeps_filter_roles():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    payload = """
    {"keywords":"机房 剩余机柜数 上海 区域",
     "goal":"查询上海区域所有机房的剩余机柜数",
     "metrics":["剩余机柜数"],
     "dimensions":["机房"],
     "filters":[
       {"phrase":"上海区域","semantic_type":"geographic_region",
        "expected_column_types":["区域","地域","gxqy","region","area"],
        "avoid_column_types":["机房名称","shipName"],
        "relation":"parent_region_or_scope"}
     ],
     "time_range":"无",
     "grain":"机房"}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="查询上海区域所有机房的剩余机柜数")

    assert intent.keywords == "机房 剩余机柜数 上海 区域"
    assert intent.metrics == ["剩余机柜数"]
    assert intent.dimensions == ["机房"]
    assert intent.filters[0].phrase == "上海区域"
    assert intent.filters[0].semantic_type == "geographic_region"
    assert "gxqy" in intent.filters[0].expected_column_types
    assert "shipName" in intent.filters[0].avoid_column_types
    assert intent.filters[0].relation == "parent_region_or_scope"


def test_format_semantic_intent_context_guides_field_binding():
    from app.services.ai.data_query_semantic_intent import (
        DataQuerySemanticIntent,
        SemanticIntentFilter,
        format_semantic_intent_context,
    )

    intent = DataQuerySemanticIntent(
        goal="查询上海区域所有机房的剩余机柜数",
        keywords="机房 剩余机柜数 上海 区域",
        metrics=["剩余机柜数"],
        dimensions=["机房"],
        filters=[
            SemanticIntentFilter(
                phrase="上海区域",
                semantic_type="geographic_region",
                expected_column_types=["区域", "gxqy", "region", "area"],
                avoid_column_types=["shipName"],
                relation="parent_region_or_scope",
            )
        ],
        grain="机房",
    )

    block = format_semantic_intent_context(intent)

    assert "结构化业务意图" in block
    assert "上海区域" in block
    assert "geographic_region" in block
    assert "优先绑定字段语义" in block
    assert "gxqy" in block
    assert "避免误绑" in block
    assert "shipName" in block
    assert "不是已确认物理表名或字段名" in block
    assert "必须以 get_dataset_schema 返回为准" in block


def test_build_semantic_intent_prompt_warns_intent_frame_is_not_schema():
    from app.services.ai.data_query_semantic_intent import build_semantic_intent_prompt

    prompt = build_semantic_intent_prompt(
        "查询上海区域所有机房的剩余机柜数",
        "查询上海区域所有机房的剩余机柜数",
        "",
    )

    assert "DataQueryIntentFrame 不是数据库 Schema" in prompt
    assert "不得编造物理表名" in prompt
    assert "SQL 的 FROM/JOIN/字段必须以 get_dataset_schema 返回为准" in prompt


def test_parse_semantic_intent_payload_preserves_complex_filters():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    # 用户问：所有北京在用的机房列表
    # 期望：“北京”和“在用”作为合法筛选不被误杀，但“所有”等纯范围词要过滤
    payload = """
    {"keywords":"北京 在用 虚拟机 列表",
     "goal":"查一下所有北京在用状态的虚拟机",
     "metrics":[],
     "dimensions":["虚拟机", "配置"],
     "filters":[
       {"phrase":"北京","semantic_type":"geographic_region","relation":"exact_value"},
       {"phrase":"在用","semantic_type":"status","relation":"exact_value"},
       {"phrase":"所有","relation":"parent_region_or_scope"}
     ],
     "time_range":"无",
     "grain":"明细粒度"}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="列出所有北京在用状态的虚拟机")

    # keywords 保留了合理的同义词和主语
    assert "虚拟机" in intent.keywords
    assert "北京" in intent.keywords
    
    # filters 移除了纯噪声 filter("所有")，但保留了包含具体过滤逻辑的 "北京" 和 "在用"
    phrases = [f.phrase for f in intent.filters]
    assert "北京" in phrases
    assert "在用" in phrases
    assert "所有" not in phrases

    # dimensions 保留了推导出的 "配置"
    assert "虚拟机" in intent.dimensions
    assert "配置" in intent.dimensions


def test_parse_semantic_intent_payload_cleans_only_pure_scope_filters():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_payload

    # 全量查询：所有机房列表
    payload = """
    {"keywords":"机房 列表",
     "goal":"获取所有机房的基础信息列表",
     "metrics":[],
     "dimensions":["机房"],
     "filters":[
       {"phrase":"所有","relation":"parent_region_or_scope"},
       {"phrase":"全部的","relation":"parent_region_or_scope"}
     ],
     "time_range":"无",
     "grain":"明细粒度"}
    """

    intent = parse_semantic_intent_payload(payload, fallback_question="查一下所有机房的列表")

    assert intent.keywords == "机房 列表"
    # filters 中的纯噪声范围词全部被过滤
    assert intent.filters == []
    assert intent.dimensions == ["机房"]


def test_build_semantic_intent_prompt_has_core_constraints_and_few_shot():
    from app.services.ai.data_query_semantic_intent import build_semantic_intent_prompt

    prompt = build_semantic_intent_prompt(
        "查一下所有机房的列表",
        "查一下所有机房的列表",
        "",
    )

    assert "【核心约束原则】" in prompt
    assert "严禁脑补与需求扩大化" in prompt
    assert "【示例对比学习】" in prompt
    assert "示例一：无额外筛选条件的全量明细列表查询" in prompt
    assert "示例二：带具体筛选条件的列表查询" in prompt


def test_format_empty_result_semantic_repair_context_mentions_parent_child_relationship():
    from app.services.ai.data_query_semantic_intent import (
        DataQuerySemanticIntent,
        SemanticIntentFilter,
        format_empty_result_semantic_repair_context,
    )

    intent = DataQuerySemanticIntent(
        goal="查询上海区域所有机房的剩余机柜数",
        keywords="机房 剩余机柜数 上海 区域",
        metrics=["剩余机柜数"],
        dimensions=["机房"],
        filters=[
            SemanticIntentFilter(
                phrase="上海区域",
                semantic_type="geographic_region",
                expected_column_types=["区域", "gxqy", "region", "area"],
                avoid_column_types=["shipName"],
                relation="parent_region_or_scope",
            )
        ],
    )

    block = format_empty_result_semantic_repair_context(
        intent,
        diagnostics=[
            {
                "column": "shipName",
                "used_values": ["上海"],
                "candidates": ["外高桥", "金桥B8", "临港123期", "唐镇"],
                "alternative_columns": ["cc_username", "ccname", "gxqy"],
            }
        ],
    )

    assert "空结果语义复核" in block
    assert "父级/范围条件" in block
    assert "上海区域" in block
    assert "shipName" in block
    assert "gxqy" in block
    assert "不能仅因候选值不包含原词就判定无数据" in block
