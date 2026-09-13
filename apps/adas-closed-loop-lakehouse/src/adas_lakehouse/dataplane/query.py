"""双路查询出口：检索走 Paimon External Catalog，看板走 StarRocks 内表。

原文第二章对齐约定四逐字：

  「双路查询出口：检索明细与向量走 External Catalog 查 Paimon；
    看板经 ADS 物化至 StarRocks 内表毫秒级直查」

原文第五章向量检索的取舍：

  「向量检索选 StarRocks 外部表 HNSW——业界主流是 Milvus 等独立向量库，我们放弃独立
    向量库换取湖仓单一事实源与免数据冗余，属差异化权衡，并保留**内表降级路径**」

所以出口其实有三条形态：外表检索（默认）、内表看板（毫秒级）、内表向量降级（兜底）。
本模块负责**选路**并渲染 SQL，不负责执行——执行要连 StarRocks，客户端延迟 import。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..config import settings
from ..controlplane import constants as K

__all__ = [
    "QueryRoute",
    "QueryIntent",
    "QueryPlan",
    "route_for",
    "plan_query",
    "StarRocksClient",
    "VECTOR_INDEX_TYPE",
]

#: 向量索引类型（原文第五章逐字：StarRocks 外部表 HNSW）
VECTOR_INDEX_TYPE: str = "HNSW"


class QueryRoute(str, Enum):
    """三条出口。"""

    #: 检索明细与向量：External Catalog 直查 Paimon（不搬数据，湖仓单一事实源）
    EXTERNAL_CATALOG = "external_catalog"
    #: 看板：ADS 物化至 StarRocks 内表，毫秒级直查
    INTERNAL_TABLE = "internal_table"
    #: 向量检索的内表降级路径（原文第五章「保留内表降级路径」）
    INTERNAL_VECTOR_FALLBACK = "internal_vector_fallback"


class QueryIntent(str, Enum):
    """查询意图。决定走哪条出口。"""

    SEMANTIC_SEARCH = "semantic_search"  # 文搜图 / 图搜图 / 混合检索
    DETAIL_LOOKUP = "detail_lookup"  # 检索明细
    DASHBOARD = "dashboard"  # 看板 / 大屏
    TAG_COVERAGE = "tag_coverage"  # 标签覆盖率（看板类聚合）


def route_for(intent: QueryIntent, *, vector_fallback: bool = False) -> QueryRoute:
    """按意图选路。

    :param intent: 查询意图
    :param vector_fallback: 外表 HNSW 不可用时降级到内表（原文保留的降级路径）
    """
    if intent in (QueryIntent.SEMANTIC_SEARCH, QueryIntent.DETAIL_LOOKUP):
        if intent is QueryIntent.SEMANTIC_SEARCH and vector_fallback:
            return QueryRoute.INTERNAL_VECTOR_FALLBACK
        return QueryRoute.EXTERNAL_CATALOG
    return QueryRoute.INTERNAL_TABLE


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """一条查询计划：走哪条出口、打到哪个 catalog/库、SQL 长什么样。"""

    intent: QueryIntent
    route: QueryRoute
    catalog: str
    database: str
    sql: str
    top_k: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "route": self.route.value,
            "catalog": self.catalog,
            "database": self.database,
            "sql": self.sql,
            "top_k": self.top_k,
            "note": self.note,
        }


def _quote(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _where(filters: Mapping[str, Any]) -> str:
    """把标量过滤条件渲染成 WHERE 子句。只支持等值与 IN——复杂谓词请走规则引擎。"""
    if not filters:
        return ""
    parts: list[str] = []
    for key, value in filters.items():
        if not str(key).replace("_", "").isalnum():
            raise ValueError(f"非法过滤字段名: {key!r}")
        if isinstance(value, (list, tuple, set)):
            joined = ", ".join(_quote(v) for v in value)
            parts.append(f"`{key}` IN ({joined})")
        else:
            parts.append(f"`{key}` = {_quote(value)}")
    return " WHERE " + " AND ".join(parts)


def plan_query(
    intent: QueryIntent,
    *,
    filters: Mapping[str, Any] | None = None,
    top_k: int = 50,
    query_vector_column: str = "image_vector",
    vector_fallback: bool = False,
) -> QueryPlan:
    """渲染一条查询计划。

    语义检索用 StarRocks 的向量近邻函数按 ``approx_l2_distance`` 排序；
    ⚠️ 原文未明确，本项目设计：原文只说「StarRocks 外部表 HNSW」，没给函数名与距离度量，
    这里取 StarRocks 向量索引的常用写法，实际以集群版本为准。

    :raises ValueError: top_k 超过 :data:`~adas_lakehouse.controlplane.constants.SEARCH_MAX_TOP_K`
    """
    if top_k > K.SEARCH_MAX_TOP_K:
        raise ValueError(f"top_k {top_k} 超过上限 {K.SEARCH_MAX_TOP_K}")
    cfg = settings()
    route = route_for(intent, vector_fallback=vector_fallback)
    where = _where(filters or {})

    if route is QueryRoute.EXTERNAL_CATALOG:
        catalog, database = cfg.starrocks.external_catalog, cfg.paimon.database
        if intent is QueryIntent.SEMANTIC_SEARCH:
            table = K.TABLE_IMAGE_VECTOR_DETAIL
            sql = (
                f"SELECT `image_id`, `data_id`, `artifact_id`, "
                f"approx_l2_distance(`{query_vector_column}`, ?) AS distance\n"
                f"FROM `{catalog}`.`{database}`.`{table}`{where}\n"
                f"ORDER BY distance ASC\nLIMIT {top_k}"
            )
            note = (
                f"向量检索走 StarRocks 外部表 {VECTOR_INDEX_TYPE} 索引直查 Paimon："
                "放弃独立向量库换取湖仓单一事实源与免数据冗余"
            )
        else:
            table = K.TABLE_IMAGE_FRAME_DETAIL
            sql = f"SELECT *\nFROM `{catalog}`.`{database}`.`{table}`{where}\nLIMIT {top_k}"
            note = "检索明细走 External Catalog 直查 Paimon，不落副本"
    elif route is QueryRoute.INTERNAL_VECTOR_FALLBACK:
        catalog, database = "default_catalog", cfg.starrocks.internal_database
        table = K.TABLE_IMAGE_VECTOR_DETAIL
        sql = (
            f"SELECT `image_id`, `data_id`, `artifact_id`, "
            f"approx_l2_distance(`{query_vector_column}`, ?) AS distance\n"
            f"FROM `{database}`.`{table}`{where}\n"
            f"ORDER BY distance ASC\nLIMIT {top_k}"
        )
        note = "内表降级路径：外表 HNSW 不可用时临时物化到内表，恢复后回切外表"
    else:
        catalog, database = "default_catalog", cfg.starrocks.internal_database
        table = K.TABLE_MINING_DASHBOARD
        sql = f"SELECT *\nFROM `{database}`.`{table}`{where}\nLIMIT {top_k}"
        note = "看板经 ADS 物化至 StarRocks 内表，毫秒级直查"

    return QueryPlan(
        intent=intent,
        route=route,
        catalog=catalog,
        database=database,
        sql=sql,
        top_k=top_k,
        note=note,
    )


class StarRocksClient:
    """StarRocks 查询客户端。MySQL 协议，``pymysql`` 延迟 import。

    只做「执行一条已经规划好的 SQL」这一件事，不拼 SQL——拼 SQL 是 :func:`plan_query` 的活。
    """

    def __init__(self, *, connection_factory: Any = None) -> None:
        self._factory = connection_factory
        self._conn: Any = None

    def _connect(self) -> Any:
        if self._conn is not None:
            return self._conn
        if self._factory is not None:
            self._conn = self._factory()
            return self._conn
        try:
            import pymysql  # 延迟 import
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "StarRocksClient 需要 pymysql（StarRocks 走 MySQL 协议）；请 `pip install pymysql`"
            ) from exc
        cfg = settings().starrocks
        self._conn = pymysql.connect(
            host=cfg.fe_host,
            port=cfg.query_port,
            user=cfg.user,
            password=cfg.password,
            charset="utf8mb4",
            autocommit=True,
        )
        return self._conn

    def execute(self, plan: QueryPlan, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """执行查询计划，返回行。

        :param plan: :func:`plan_query` 的产物
        :param params: SQL 里 ``?`` 占位符的参数（如查询向量）
        """
        sql = plan.sql.replace("?", "%s")
        conn = self._connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
