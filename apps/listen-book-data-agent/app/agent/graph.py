import asyncio

from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.compile_dsl import compile_dsl
from app.agent.nodes.correct_dsl import correct_dsl
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.decompose_query import decompose_query
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.fallback_to_legacy import fallback_to_legacy
from app.agent.nodes.generate_answer import generate_answer
from app.agent.nodes.generate_chart_spec import generate_chart_spec
from app.agent.nodes.generate_dsl import generate_dsl
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.load_business_rules import load_business_rules
from app.agent.nodes.match_verified_query import match_verified_query
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.plan_analysis import plan_analysis
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.refine_query_plan import refine_query_plan
from app.agent.nodes.report_sql_error import report_sql_error
from app.agent.nodes.select_semantics import select_semantics
from app.agent.nodes.validate_query_plan import validate_query_plan
from app.agent.nodes.validate_sql import validate_sql
from app.agent.state import DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import dw_mysql_client_manager, meta_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.conf.app_config import app_config
from app.repositories.es.value_es_repository import ValueInfoRepository
from app.repositories.mysql.dw_mysql_repository import DWMySQlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

# 1.基于StateGraph创建graph构建器
graph_builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

# 2.添加节点
graph_builder.add_node("plan_analysis", plan_analysis)
graph_builder.add_node("extract_keywords", extract_keywords)
graph_builder.add_node("recall_column", recall_column)
graph_builder.add_node("recall_metric", recall_metric)
graph_builder.add_node("recall_value", recall_value)
graph_builder.add_node("merge_retrieved_info", merge_retrieved_info)
graph_builder.add_node("select_semantics", select_semantics)
graph_builder.add_node("decompose_query", decompose_query)
graph_builder.add_node("refine_query_plan", refine_query_plan)
graph_builder.add_node("add_extra_context", add_extra_context)
graph_builder.add_node("validate_query_plan", validate_query_plan)
graph_builder.add_node("match_verified_query", match_verified_query)
graph_builder.add_node("load_business_rules", load_business_rules)
graph_builder.add_node("generate_dsl", generate_dsl)
graph_builder.add_node("correct_dsl", correct_dsl)
graph_builder.add_node("compile_dsl", compile_dsl)
graph_builder.add_node("fallback_to_legacy", fallback_to_legacy)
graph_builder.add_node("generate_sql", generate_sql)
graph_builder.add_node("validate_sql", validate_sql)
graph_builder.add_node("correct_sql", correct_sql)
graph_builder.add_node("report_sql_error", report_sql_error)
graph_builder.add_node("execute_sql", execute_sql)
graph_builder.add_node("generate_answer", generate_answer)
graph_builder.add_node("generate_chart_spec", generate_chart_spec)

# 3.添加边
graph_builder.add_edge(START, "plan_analysis")
graph_builder.add_edge("plan_analysis", "extract_keywords")
graph_builder.add_edge("extract_keywords", "recall_column")
graph_builder.add_edge("extract_keywords", "recall_metric")
graph_builder.add_edge("extract_keywords", "recall_value")
graph_builder.add_edge("recall_column", "merge_retrieved_info")
graph_builder.add_edge("recall_metric", "merge_retrieved_info")
graph_builder.add_edge("recall_value", "merge_retrieved_info")


def route_after_semantic_merge(state: DataAgentState) -> str:
    complexity = state.get("query_plan", {}).get("complexity", "EASY")
    return "add_extra_context" if complexity == "EASY" else "select_semantics"


def route_after_selector(state: DataAgentState) -> str:
    return (
        "decompose_query"
        if state.get("query_plan", {}).get("complexity") == "NESTED"
        else "refine_query_plan"
    )


graph_builder.add_conditional_edges(
    "merge_retrieved_info",
    route_after_semantic_merge,
    {
        "add_extra_context": "add_extra_context",
        "select_semantics": "select_semantics",
    },
)
graph_builder.add_conditional_edges(
    "select_semantics",
    route_after_selector,
    {
        "decompose_query": "decompose_query",
        "refine_query_plan": "refine_query_plan",
    },
)
graph_builder.add_edge("decompose_query", "refine_query_plan")
graph_builder.add_edge("refine_query_plan", "add_extra_context")
graph_builder.add_edge("add_extra_context", "validate_query_plan")
graph_builder.add_edge("validate_query_plan", "match_verified_query")


def route_after_verified_query(state: DataAgentState) -> str:
    """Exact governed SQL still enters Guard; approximate matches enter generation."""

    if state.get("generation_source") == "verified_exact" and state.get("sql"):
        return "validate_sql"
    return "load_business_rules"


def route_after_business_rules(_: DataAgentState) -> str:
    return "generate_dsl" if app_config.query.generation_mode.lower() == "dsl" else "generate_sql"


def route_after_dsl_attempt(state: DataAgentState) -> str:
    if state.get("dsl_error") is None and state.get("query_dsl") is not None:
        return "compile_dsl"
    if state.get("dsl_attempts", 0) < 2:
        return "correct_dsl"
    return "fallback_to_legacy"


def route_after_dsl_compile(state: DataAgentState) -> str:
    if state.get("dsl_error") is None and state.get("sql"):
        return "validate_sql"
    if state.get("dsl_attempts", 0) < 2:
        return "correct_dsl"
    return "fallback_to_legacy"


graph_builder.add_conditional_edges(
    "match_verified_query",
    route_after_verified_query,
    {
        "validate_sql": "validate_sql",
        "load_business_rules": "load_business_rules",
    },
)
graph_builder.add_conditional_edges(
    "load_business_rules",
    route_after_business_rules,
    {"generate_dsl": "generate_dsl", "generate_sql": "generate_sql"},
)
graph_builder.add_conditional_edges(
    "generate_dsl",
    route_after_dsl_attempt,
    {
        "compile_dsl": "compile_dsl",
        "correct_dsl": "correct_dsl",
        "fallback_to_legacy": "fallback_to_legacy",
    },
)
graph_builder.add_conditional_edges(
    "correct_dsl",
    route_after_dsl_attempt,
    {
        "compile_dsl": "compile_dsl",
        "correct_dsl": "correct_dsl",
        "fallback_to_legacy": "fallback_to_legacy",
    },
)
graph_builder.add_conditional_edges(
    "compile_dsl",
    route_after_dsl_compile,
    {
        "validate_sql": "validate_sql",
        "correct_dsl": "correct_dsl",
        "fallback_to_legacy": "fallback_to_legacy",
    },
)
graph_builder.add_edge("fallback_to_legacy", "generate_sql")
graph_builder.add_edge("generate_sql", "validate_sql")


# 校验失败时至多修复指定次数；修复后的 SQL 必须再次通过同一套校验。
def route_after_sql_validation(state: DataAgentState) -> str:
    if state.get("error") is None:
        return "execute_sql"
    if state.get("correction_attempts", 0) < _sql_refinement_limit():
        return "correct_sql"
    return "report_sql_error"


def route_after_sql_execution(state: DataAgentState) -> str:
    """Send execution-time SQL semantics to the same bounded Refiner loop."""

    if state.get("error") is None:
        return "generate_chart_spec"
    if state.get("correction_attempts", 0) < _sql_refinement_limit():
        return "correct_sql"
    return "report_sql_error"


def _sql_refinement_limit() -> int:
    return min(2, max(0, app_config.query.correction_attempts))


graph_builder.add_conditional_edges(
    "validate_sql",
    route_after_sql_validation,
    {
        "correct_sql": "correct_sql",
        "correct_dsl": "correct_dsl",
        "fallback_to_legacy": "fallback_to_legacy",
        "execute_sql": "execute_sql",
        "report_sql_error": "report_sql_error",
    },
)
graph_builder.add_edge("correct_sql", "validate_sql")
graph_builder.add_edge("report_sql_error", END)
graph_builder.add_conditional_edges(
    "execute_sql",
    route_after_sql_execution,
    {
        "correct_sql": "correct_sql",
        "generate_chart_spec": "generate_chart_spec",
        "report_sql_error": "report_sql_error",
    },
)
graph_builder.add_edge("generate_chart_spec", "generate_answer")
graph_builder.add_edge("generate_answer", END)

# 4.编译得到graph对象
graph = graph_builder.compile()

# 5.测试
if __name__ == "__main__":
    # print(graph.get_graph().draw_mermaid())

    # 1. 执行各个客户端初始化方法
    dw_mysql_client_manager.init_client()
    meta_mysql_client_manager.init_client()
    embedding_client_manager.init_client()
    qdrant_client_manager.init_client()
    es_client_manager.init()

    async def test_graph():
        # 2. 获取操作不同库session对象
        async with (
            dw_mysql_client_manager.session_factory() as dw_session,
            meta_mysql_client_manager.session_factory() as meta_session,
        ):
            # 3.创建Context上下文对象
            context = DataAgentContext(
                dw_mysql_repository=DWMySQlRepository(dw_session),
                meta_mysql_repository=MetaMySQlRepository(meta_session),
                meta_repository_lock=asyncio.Lock(),
                column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
                metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
                value_es_repository=ValueInfoRepository(es_client_manager.client),
                embedding_client=embedding_client_manager.client,
            )
            # ainvoke默认执行结果是被更新的 state
            # result = await graph.ainvoke(input=DataAgentState(query="统计下广东地区的销售总额"), context=context)
            # print(result)
            # 使用异步流式调用 模式：updates 输出更更新state ; values:输出完整state
            async for chunk in graph.astream(
                input=DataAgentState(query="黄金会员购买苹果品牌产品的总数量"),
                context=context,
                stream_mode="custom",
            ):
                print(chunk)
        # 4.关闭客户端
        await dw_mysql_client_manager.close()
        await meta_mysql_client_manager.close()
        await es_client_manager.close()
        await qdrant_client_manager.close()

    asyncio.run(test_graph())
