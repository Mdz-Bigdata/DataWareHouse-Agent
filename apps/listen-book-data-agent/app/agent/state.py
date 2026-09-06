import operator
from typing import Annotated, TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.value_info import ValueInfo


# 列信息封装实体
class ColumnInfoState(TypedDict):
    id: str
    name: str
    type: str
    role: str
    examples: list
    description: str
    alias: list[str]
    table_id: str
    sensitive: bool  # Phase 1.3：敏感标记随列下沉到 guard，执行前阻断
    filter_only: bool  # 敏感列仅可用于聚合查询 WHERE 过滤，不可 SELECT/GROUP/ORDER


# 表信息封装实体
class TableInfoState(TypedDict):
    id: str
    name: str
    role: str
    description: str
    alias: list[str]
    columns: list[ColumnInfoState]


# 指标信息封装实体
class MetricInfoState(TypedDict):
    id: str
    name: str
    description: str
    relevant_columns: list[str]
    alias: list[str]
    formula: str
    filters: list[str]
    time_column: str | None
    unit: str
    currency_column: str | None
    dimensions: list[str]
    snapshot: bool


class RelationshipState(TypedDict):
    id: str
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str
    condition: str | None
    physical: bool


class AnalysisPlanState(TypedDict):
    intent: str
    metric_hints: list[str]
    dimensions: list[str]
    filters: list[str]
    time_range: dict[str, str | None]
    time_grain: str | None
    top_n: int | None
    sort_direction: str | None
    comparison: str | None
    filter_requirements: list[dict]
    metric_requirements: list[dict]


class QueryPlanState(TypedDict):
    schema_version: str
    intent: str
    complexity: str
    metrics: list[dict]
    dimensions: list[dict]
    filters: list[dict]
    time: dict
    sort: list[dict]
    join_path: list[str]
    subplans: list[dict]
    limit: int | None
    comparison: str | None
    source_hints: dict


class AnswerState(TypedDict):
    summary: str
    row_count: int
    columns: list[str]
    metrics: list[str]
    time_range: str
    sql: str


class ChartSpecState(TypedDict):
    schema_version: str
    type: str
    title: str
    dimension: str | None
    metrics: list[str]
    series: str | None
    source: str


class DateInfoState(TypedDict):
    date: str
    weekday: str
    quarter: str


class DBInfoState(TypedDict):
    version: str
    dialect: str


class AccessPolicyState(TypedDict):
    schema_version: str
    user_id: str
    role: str
    domain: str
    datasource: str
    table_acl: dict[str, list[str]]
    row_predicates: list[dict]
    function_whitelist: list[str]
    policy_version: str
    policy_hash: str
    admin_bypass: bool


class DataAgentState(TypedDict):
    """所有节点共享数据 节点对state中数据进行读写"""

    query: str  # 用户提出问题
    query_parameters: dict[str, object]  # 可信案例命名参数，仅由 API 请求提供
    keywords: list[str]  # 抽取关键词结果
    retrieved_columns: list[ColumnInfo]  # 召回字段结果
    retrieved_metrics: list[MetricInfo]  # 召回指标结果
    error: str | None  # 校验SQL产生错误信息
    error_kind: str | None  # sql_semantic / infrastructure
    error_stage: str | None  # sql_validation / execution
    correction_attempts: int  # 已执行的 SQL 修复次数
    retrieved_values: list[ValueInfo]  # 召回字段取值结果
    analysis_plan: AnalysisPlanState  # 规则化意图、时间、排序计划
    query_plan: QueryPlanState  # 绑定当前语义构建稳定 ID 的 QueryPlanV1
    planning_roles: list[str]  # 复杂查询按需执行的 Selector/Decomposer/Refiner
    selected_semantics: dict[str, list[str]]
    decomposed_query: list[dict]
    query_plan_refined: bool
    dry_plan_status: str
    dry_plan_checks: list[str]
    explain_estimate: dict
    sql_validation_stages: list[str]
    execution_mode: str
    retrieval_warnings: Annotated[list[str], operator.add]  # 外部召回降级提示
    build_id: str  # 当前元数据知识库构建版本
    table_infos: list[TableInfoState]  # 合并节点封装表信息列表
    metric_infos: list[MetricInfoState]  # 合并节点封装指标信息列表
    relationships: list[RelationshipState]  # SQL 生成可用的连接路径
    result_rows: list[dict]  # 已执行 SQL 的临时结果，仅用于本次回答生成
    answer: AnswerState  # 严格基于结果的解释
    chart_spec: ChartSpecState  # 仅引用真实结果列的确定性 ChartSpecV1
    date_info: DateInfoState  # 新增额外上下文日期信息
    db_info: DBInfoState  # 新增额外上下文数仓信息
    sql: str  # 生成SQL产生SQL语句
    pre_rls_sql: str  # Guard 注入 RLS 前的 SQL，仅用于本次请求和模板化反馈
    # DSL 实验链路：结构化计划仅在本次请求中流转，不持久化到查询追踪表。
    query_dsl: dict | None
    dsl_raw: str | None
    dsl_error: str | None
    dsl_attempts: int
    dsl_fallback: bool
    dsl_fallback_reason: str | None
    generation_mode: str
    generation_source: str
    llm_calls: int
    semantic_terms: list[dict]  # 当前作用域发布术语；只用于召回扩展，不进入 Prompt 原文
    semantic_term_matches: list[dict]  # 经当前召回语义和 ACL 过滤后的公开命中
    verified_query_examples: list[dict]  # 近似案例只用于生成 few-shot，不可直接执行
    verified_query_match: dict | None
    verified_exact_error: str | None
    query_set_id: str | None
    query_set_version: int | None
    query_set_hash: str | None
    semantic_release_id: str | None
    semantic_release_version: int | None
    business_rule_set_id: str | None
    business_rule_set_version: int | None
    business_rules: list[dict]  # 仅包含已审核发布且通过注入检测的类型化规则片段
    business_rule_matches: list[dict]
    # 每次请求在 API 边界解析出的不可变权限快照；不得由 LLM 修改。
    access_policy: AccessPolicyState
    # Phase 1.2：行级数据权限约束（admin 传空列表）。
    # 结构：[{"column": "region", "value": "华东"}]，由 router 从 user.data_scope 解析。
    row_level_scope: list[dict]
    # Phase 2.1：最近一次修复前的失败 SQL 与报错（仅经历过 correct_sql 时存在）。
    # 供 execute_sql 成功后回写 Few-shot 经验对使用。
    previous_error_sql: str | None
    previous_error_message: str | None
