"""ADS 应用数据层：11 张开箱即用的智驾数据产品表 + 六项闭环业务服务。

来源
----
· [S1-05]《11 张 ADS 数据闭环表开箱即用：智驾数据产品矩阵全览》
  （公众号「小周谈智驾数据闭环」2026-08-28）——本子系统的主线设计；
· [S1-全景]《智驾数据闭环的湖仓架构全景：8 环节闭环 × 11 数据域 × 79+ 张表 × 6 大场景》
  （同公众号 2026-09-08）第七章双路查询、第九章服务层四层结构。
两篇的完整链接见 :mod:`~adas_lakehouse.ads.constants` 的模块 docstring。

一句话定位
----------
**ODS/DWD 存事实，DWS 存聚合，ADS 存答案**（[S1-05] 第一章原话）。
每张表只回答一个业务问题，口径在加工时固化、答案在查询时直取——
业务平台 SELECT 即渲染，零 JOIN、不感知湖仓分层。

模块地图
--------
==============  ================================================================
模块             职责
==============  ================================================================
``constants``    原文每个数字的唯一出处（99.2% / +30% / 0 起 / 87.5% / 45%…），
                 业务代码里不写裸数字
``products``     11 张表的产品矩阵：主题 × 服务对象 × 计算维度 × 上游链路
``schema``       从 catalog 的 TableSpec 派生 StarRocks 内表模型与 DDL（不另抄字段）
``routing``      双路查询选路：内表物化 / Paimon 外部表即席查 / 向量索引（委派）
``query``        业务平台唯一取数入口：表白名单 + 字段白名单 + 参数化
``geo``          地理网格与热力等级：表 9 的口径唯一出处，服务层与 Flink 同源
``services``     六项闭环业务服务 + OTA 灰度三条件放行门 + 难例采纳率
``gateway``      统一 API 网关：认证 / 限流 / 审计，刻意做成**传输无关**
``materialize``  两段式 T+1 物化：DWS/DWD → Paimon ADS → StarRocks 内表
``demo``         原文案例数字灌成的内存行源，不连 StarRocks 也能端到端跑通
``errors``       三类异常：调用方错误 / 依赖不可用 / 网关拒绝
==============  ================================================================

三十秒上手
----------
    from adas_lakehouse.ads import (
        AdsQueryService, ClosedLoopServiceSuite, DemoRowSource,
    )

    suite = ClosedLoopServiceSuite(AdsQueryService(DemoRowSource()))

    # OTA 灰度三条件放行（[S1-05] 表 8）：99.2% / +30% / 0 起，三条全中才放行
    gate = suite.model.ota_release_gate(
        ota_task_id="OTA-v3.3-GREY", safety_issue_count=0, check_regression=False)
    gate.decision          # 'full_rollout'

    # 地理网格热力图（表 9）与难例采纳率（表 5）
    suite.trigger.top_hotspots(top_n=3)[0].heat_level
    suite.trigger.hard_case_adoption_summary().adoption_rate   # 2800/3200 = 0.875

出口自检（11 张表是否都有服务化出口、原文代表 API 是否都落了地）::

    from adas_lakehouse.ads import verify_service_exits, verify_against_catalog
    verify_service_exits()      # {} 即全绿
    verify_against_catalog()    # {} 即产品矩阵与 catalog 注册表一致

外部依赖（pymysql / mysql-connector-python）一律延迟 import：没装驱动也能
import 本包、渲染全部 SQL、跑通六项服务的全部业务逻辑（用 :class:`DemoRowSource`）。
"""

from __future__ import annotations

from .constants import (
    ADS_TABLE_COUNT,
    ADS_THEME_COUNT,
    CLOSED_LOOP_SERVICE_COUNT,
    GEO_GRID_PRECISION_DEGREES,
    OTA_GATE_CONDITION_COUNT,
    OTA_GATE_DECISION_HOLD,
    OTA_GATE_DECISION_PASS,
    OTA_GATE_MAX_SAFETY_ISSUE_COUNT,
    OTA_GATE_MIN_SUCCESS_RATE,
    OTA_GATE_MIN_TRIGGER_GROWTH_RATE,
    OTA_POST_RELEASE_OBSERVE_DAYS,
    TRIGGER_HEAT_LEVEL_MAX,
    TRIGGER_HEAT_LEVEL_MIN,
    TRIGGER_HEAT_LEVEL_THRESHOLDS,
    TRIGGER_WOW_ANOMALY_THRESHOLD,
    TRIGGER_WOW_WINDOW_DAYS,
)
from .demo import DEMO_OTA_TASK_ID, DemoRowSource, demo_rows
from .errors import (
    AdsError,
    AdsQueryError,
    AuthenticationError,
    AuthorizationError,
    BackendUnavailableError,
    InvalidFilterError,
    RateLimitExceededError,
    RouteNotFoundError,
    ServiceUnavailableError,
    StarRocksUnavailableError,
    UnknownColumnError,
    UnknownTableError,
)
from .gateway import (
    ApiGateway,
    ApiRoute,
    AuditLog,
    AuditRecord,
    Principal,
    TokenAuthenticator,
    TokenBucketRateLimiter,
    error_status,
    is_gateway_error,
)
from .geo import (
    GRID_SIZE_DEGREES,
    GeoGridCell,
    grid_center,
    grid_id,
    grid_id_sql,
    heat_level,
    heat_level_sql,
    parse_grid_id,
)
from .materialize import (
    PLANS,
    AdsMaterializer,
    MaterializePlan,
    ScriptExportSubmitter,
    SqlSubmitter,
    render_all_flink_sql,
    render_flink_sql,
    write_sql_files,
)
from .products import (
    ADS_TABLE_NAMES,
    PRODUCTS,
    AdsProduct,
    AdsTheme,
    BusinessPlatform,
    ClosedLoopService,
    get_product,
    matrix_rows,
    products_by_platform,
    products_by_service,
    products_by_theme,
    render_matrix,
)
from .query import (
    SUPPORTED_OPERATORS,
    AdsQuery,
    AdsQueryService,
    Filter,
    QueryResult,
    Row,
    RowSource,
    StarRocksRowSource,
    StaticRowSource,
    ensure_columns,
)
from .routing import ROUTING_TABLE, QueryRoute, RouteDecision, WorkloadKind, qualify, route_for
from .schema import (
    StarRocksColumn,
    StarRocksTable,
    column_names,
    load_table_spec,
    render_all_starrocks_ddl,
    require_column,
    starrocks_table,
    verify_against_catalog,
)
from .services import (
    API_BINDINGS,
    CATALOG_GAPS,
    DELEGATED_APIS,
    ApiBinding,
    ClosedLoopServiceSuite,
    DatasetVersionDeliveryService,
    GateCondition,
    GridHotspot,
    HardCaseAdoption,
    HardCaseAdoptionSummary,
    HeatCell,
    LineagePort,
    LineageTraceService,
    ModelIterationEvaluationService,
    OtaGateDecision,
    ProductionTrackingService,
    RegressionCheck,
    SafetyIssuePort,
    SceneSearchCurationService,
    SemanticSearchPort,
    TriggerGrowth,
    TriggerMiningClosedLoopService,
    WowAnomaly,
    adoption_rate,
    evaluate_ota_release_gate,
    growth_rate,
    service_exit_matrix,
    verify_service_exits,
)

__all__ = [
    # constants（原文数字，其余见 ads.constants）
    "ADS_TABLE_COUNT",
    "ADS_THEME_COUNT",
    "CLOSED_LOOP_SERVICE_COUNT",
    "OTA_GATE_MIN_SUCCESS_RATE",
    "OTA_GATE_MIN_TRIGGER_GROWTH_RATE",
    "OTA_GATE_MAX_SAFETY_ISSUE_COUNT",
    "OTA_GATE_CONDITION_COUNT",
    "OTA_GATE_DECISION_PASS",
    "OTA_GATE_DECISION_HOLD",
    "OTA_POST_RELEASE_OBSERVE_DAYS",
    "GEO_GRID_PRECISION_DEGREES",
    "TRIGGER_HEAT_LEVEL_MIN",
    "TRIGGER_HEAT_LEVEL_MAX",
    "TRIGGER_HEAT_LEVEL_THRESHOLDS",
    "TRIGGER_WOW_ANOMALY_THRESHOLD",
    "TRIGGER_WOW_WINDOW_DAYS",
    # products
    "PRODUCTS",
    "ADS_TABLE_NAMES",
    "AdsProduct",
    "AdsTheme",
    "BusinessPlatform",
    "ClosedLoopService",
    "get_product",
    "products_by_theme",
    "products_by_platform",
    "products_by_service",
    "matrix_rows",
    "render_matrix",
    # schema
    "StarRocksColumn",
    "StarRocksTable",
    "load_table_spec",
    "starrocks_table",
    "column_names",
    "require_column",
    "verify_against_catalog",
    "render_all_starrocks_ddl",
    # routing
    "WorkloadKind",
    "QueryRoute",
    "RouteDecision",
    "ROUTING_TABLE",
    "route_for",
    "qualify",
    # query
    "Row",
    "Filter",
    "AdsQuery",
    "QueryResult",
    "RowSource",
    "StarRocksRowSource",
    "StaticRowSource",
    "AdsQueryService",
    "ensure_columns",
    "SUPPORTED_OPERATORS",
    # geo
    "GRID_SIZE_DEGREES",
    "GeoGridCell",
    "grid_id",
    "parse_grid_id",
    "grid_center",
    "grid_id_sql",
    "heat_level",
    "heat_level_sql",
    # services
    "SemanticSearchPort",
    "LineagePort",
    "SafetyIssuePort",
    "GateCondition",
    "OtaGateDecision",
    "RegressionCheck",
    "TriggerGrowth",
    "HeatCell",
    "GridHotspot",
    "WowAnomaly",
    "HardCaseAdoption",
    "HardCaseAdoptionSummary",
    "adoption_rate",
    "growth_rate",
    "evaluate_ota_release_gate",
    "ProductionTrackingService",
    "SceneSearchCurationService",
    "DatasetVersionDeliveryService",
    "ModelIterationEvaluationService",
    "TriggerMiningClosedLoopService",
    "LineageTraceService",
    "ClosedLoopServiceSuite",
    "ApiBinding",
    "API_BINDINGS",
    "DELEGATED_APIS",
    "CATALOG_GAPS",
    "verify_service_exits",
    "service_exit_matrix",
    # gateway
    "Principal",
    "TokenAuthenticator",
    "TokenBucketRateLimiter",
    "AuditRecord",
    "AuditLog",
    "ApiRoute",
    "ApiGateway",
    "is_gateway_error",
    "error_status",
    # materialize
    "MaterializePlan",
    "PLANS",
    "render_flink_sql",
    "render_all_flink_sql",
    "write_sql_files",
    "SqlSubmitter",
    "ScriptExportSubmitter",
    "AdsMaterializer",
    # demo
    "DemoRowSource",
    "demo_rows",
    "DEMO_OTA_TASK_ID",
    # errors
    "AdsError",
    "AdsQueryError",
    "UnknownTableError",
    "UnknownColumnError",
    "InvalidFilterError",
    "BackendUnavailableError",
    "StarRocksUnavailableError",
    "ServiceUnavailableError",
    "AuthenticationError",
    "AuthorizationError",
    "RateLimitExceededError",
    "RouteNotFoundError",
]
