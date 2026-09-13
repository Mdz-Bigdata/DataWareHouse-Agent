"""统一检索 API：五步链路 + 四类检索能力 + 向量检索 SQL。

来源：原文第五章《检索实践：统一 API 五步 + 四类检索能力》。上层应用不直接写 SQL，
统一走检索 API，内部是固定的五步链路：

  ① 查询向量化      把用户的文本 / 图片用同一个 CLIP 模型编码成 query 向量
  ② 标量过滤编译    把时间 / GPS / 摄像头 / 标签条件编译成 SQL 预过滤——先用标量条件
                    缩小范围，再进 ANN，检索快且准
  ③ ANN 检索        落在外部表的 HNSW 索引上，取 TopK
  ④ 混合融合与重排  图文双向量按权重加权融合（image×w1 + text×w2），检索服务层完成重排
  ⑤ 回补元数据      命中后回 Paimon 补齐标签 / caption / 地理 / 时间，统一返回完整结果

文搜图的 SQL 要在一条语句里同时完成三件事（原文强调）：分区裁剪、版本过滤与标量预过滤。

性能目标只有一条：千万级数据量单次向量检索 P95 ≤ 2 秒（params.SEARCH_P95_SLA_SECONDS）。
两种后端形态（外部表 / 内表）共用这一条 API，切换对上层应用零感知。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from .client import QueryResult, SqlExecutor, get_executor
from .index import IMAGE_INDEX_NAME, TEXT_INDEX_NAME
from .params import (
    DEFAULT_HNSW_PARAMS,
    DEFAULT_IMAGE_WEIGHT,
    DEFAULT_TEXT_WEIGHT,
    DEFAULT_TOP_K,
    EXAMPLE_SCALAR_FILTER_RECENT_DAYS,
    MAX_TOP_K,
    RETRIEVAL_CAPABILITIES,
    SEARCH_P95_SLA_SECONDS,
    HnswIndexParams,
    RetrievalMode,
    VectorBackend,
)
from .schema import (
    SCALAR_FILTER_COLUMNS,
    external_table_ref,
    internal_table_ref,
    vector_columns_for_projection,
)
from .versioning import render_active_filter

__all__ = [
    "ScalarFilters",
    "FusionWeights",
    "SearchRequest",
    "SearchHit",
    "SearchResponse",
    "QueryEncoder",
    "ClipQueryEncoder",
    "as_query_encoder",
    "VectorSearchService",
    "compile_scalar_filters",
    "render_search_sql",
    "render_enrich_sql",
    "recent_days_window",
    "index_path_for",
    "prefilter_warning",
    "IMAGE_SIM_ALIAS",
    "TEXT_SIM_ALIAS",
    "FUSED_SCORE_ALIAS",
]

_log = logging.getLogger(__name__)

#: 相似度列名（SQL 里的别名）。
IMAGE_SIM_ALIAS = "image_sim"
TEXT_SIM_ALIAS = "text_sim"
FUSED_SCORE_ALIAS = "fused_score"


# ------------------------------------------------------------------------ 入参


@dataclass(frozen=True, slots=True)
class FusionWeights:
    """图文融合权重（原文第五章第 ④ 步：image×w1 + text×w2）。

    ⚠️ 原文未明确，本项目设计：原文只给公式没给取值，默认图文等权 0.5 / 0.5，
    并强制两权重之和为 1.0——否则融合分数不可跨查询比较，重排会失真。
    """

    image: float = DEFAULT_IMAGE_WEIGHT
    text: float = DEFAULT_TEXT_WEIGHT

    def __post_init__(self) -> None:
        if self.image < 0 or self.text < 0:
            raise ValueError(f"融合权重不能为负: image={self.image}, text={self.text}")
        total = self.image + self.text
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"融合权重之和必须为 1.0，收到 {total}（image={self.image}, text={self.text}）"
            )


@dataclass(frozen=True, slots=True)
class ScalarFilters:
    """标量预过滤条件（原文第五章第 ② 步：时间 / GPS / 摄像头 / 标签）。

    :param dt_from: 分区裁剪起始日（含），对应 dt >= ?
    :param dt_to: 分区裁剪结束日（含）
    :param time_from: 精确到秒的采集时间下界
    :param time_to: 采集时间上界
    :param equals: 等值条件 {列名: 值 或 值列表}，列名必须落在 SCALAR_FILTER_COLUMNS 白名单
    :param gps_bbox: (min_lat, min_lon, max_lat, max_lon) 经纬度包围盒
    """

    dt_from: date | None = None
    dt_to: date | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    equals: dict[str, Any] = field(default_factory=dict)
    gps_bbox: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        illegal = set(self.equals) - SCALAR_FILTER_COLUMNS
        if illegal:
            raise ValueError(
                f"标量过滤列不在白名单内: {sorted(illegal)}；"
                f"允许的列: {sorted(SCALAR_FILTER_COLUMNS)}（白名单同时是 SQL 注入的第一道闸）"
            )
        if self.dt_from and self.dt_to and self.dt_from > self.dt_to:
            raise ValueError(f"分区区间非法: {self.dt_from} > {self.dt_to}")


def recent_days_window(
    days: int = EXAMPLE_SCALAR_FILTER_RECENT_DAYS, *, today: date | None = None
) -> ScalarFilters:
    """构造「最近 N 天」的分区裁剪条件。

    默认 30 天直接取自原文第一章的检索示例「最近 30 天 + 某城市 + 雨天」。
    """
    end = today or date.today()
    return ScalarFilters(dt_from=end - timedelta(days=days), dt_to=end)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """一次检索请求。

    :param mode: 四类检索能力之一
    :param text: 文本查询（文搜图 / 混合检索）
    :param image_uri: 图片查询（图搜图 / 混合检索）
    :param query_image_vector: 已编码好的图片查询向量，给了就跳过第 ① 步
    :param query_text_vector: 已编码好的文本查询向量
    :param filters: 标量预过滤
    :param top_k: 取多少条（原文第六章：TopK 合理取值，不为用不到的长尾结果付出检索成本）
    :param weights: 混合检索的融合权重
    :param embedding_version: 指定版本（灰度对比用）；不指定则只按 vector_status='active' 过滤
    """

    mode: RetrievalMode
    text: str = ""
    image_uri: str = ""
    query_image_vector: Sequence[float] | None = None
    query_text_vector: Sequence[float] | None = None
    filters: ScalarFilters = field(default_factory=ScalarFilters)
    top_k: int = DEFAULT_TOP_K
    weights: FusionWeights = field(default_factory=FusionWeights)
    embedding_version: str | None = None

    def __post_init__(self) -> None:
        if self.top_k <= 0 or self.top_k > MAX_TOP_K:
            raise ValueError(f"top_k 必须落在 [1, {MAX_TOP_K}]，收到 {self.top_k}")
        if self.mode in (RetrievalMode.TEXT_TO_IMAGE, RetrievalMode.TAG_PLUS_VECTOR) and (
            not self.text and self.query_text_vector is None
        ):
            raise ValueError(f"{self.mode.value} 需要 text 或 query_text_vector")
        if self.mode is RetrievalMode.IMAGE_TO_IMAGE and (
            not self.image_uri and self.query_image_vector is None
        ):
            raise ValueError("image_to_image 需要 image_uri 或 query_image_vector")
        if self.mode is RetrievalMode.HYBRID:
            has_text = bool(self.text) or self.query_text_vector is not None
            has_image = bool(self.image_uri) or self.query_image_vector is not None
            if not (has_text and has_image):
                raise ValueError("hybrid 需要同时给出文本与图片（图文互补才谈得上融合重排）")


# ------------------------------------------------------------------------ 出参


@dataclass(frozen=True, slots=True)
class SearchHit:
    """一条命中结果。"""

    image_id: str
    score: float
    image_sim: float | None = None
    text_sim: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """检索返回。

    :param hits: 命中列表，已按融合分数降序
    :param elapsed_sec: 端到端耗时，用于对照 P95 ≤ 2 秒
    :param sql: 实际下发的 SQL，排障用
    :param backend: 本次走的档位（外部表 / 内表），对上层只是信息，不改变返回结构
    :param within_sla: 是否达标
    """

    hits: tuple[SearchHit, ...]
    elapsed_sec: float
    sql: str
    backend: VectorBackend
    within_sla: bool


class QueryEncoder(Protocol):
    """查询向量化协议（原文第五章第 ① 步）——**单条**编码。

    必须与入湖时的 embedding 用**同一个 CLIP 模型**，否则 query 向量和库内向量不在同一空间，
    检索结果毫无意义。

    注意：入湖侧的 :class:`adas_lakehouse.vector.embedding.ClipEncoder` 是**批量**接口
    （``encode_images`` / ``encode_texts``，复数），签名和这里对不上，不能直接当
    QueryEncoder 用——请用 :class:`ClipQueryEncoder` 包一层（``VectorSearchService``
    也会在检测到批量接口时自动包）。
    """

    embedding_version: str
    dim: int

    def encode_text(self, text: str) -> Sequence[float]:  # pragma: no cover - 协议
        ...

    def encode_image(self, uri: str) -> Sequence[float]:  # pragma: no cover - 协议
        ...


@dataclass(slots=True)
class ClipQueryEncoder:
    """把入湖侧的批量 CLIP 编码器（``ClipEncoder``）适配成检索侧的单条 ``QueryEncoder``。

    这一层不是形式主义：原文第五章第 ① 步要求「用**同一个** CLIP 模型编码 query 向量」，
    唯一能保证「同一个」的做法就是直接复用入湖用的那个编码器对象。两边接口一个批量一个
    单条，中间必须有个适配器，否则「以图搜图」在运行时会撞 AttributeError——
    这正是本次审计发现的断点。

    :param clip: 入湖侧的 ClipEncoder（``encode_images`` / ``encode_texts``）
    """

    clip: Any

    @property
    def embedding_version(self) -> str:
        return str(self.clip.embedding_version)

    @property
    def dim(self) -> int:
        return int(self.clip.dim)

    def encode_text(self, text: str) -> Sequence[float]:
        """单条文本编码。空文本直接拒绝——编出来的向量没有语义，检索必然是噪声。"""
        if not text:
            raise ValueError("文本 query 为空，无法编码")
        out = self.clip.encode_texts([text])
        return _single(out, "encode_texts")

    def encode_image(self, uri: str) -> Sequence[float]:
        """单条图片编码（以图搜图：拿一张 Badcase 图片找相似样本）。"""
        if not uri:
            raise ValueError("图片 query 为空，无法编码")
        out = self.clip.encode_images([uri])
        return _single(out, "encode_images")


def _single(out: Sequence[Sequence[float]], fn: str) -> Sequence[float]:
    """从批量编码结果里取唯一一条，条数不对就炸——错位的 query 向量比报错危险得多。"""
    items = list(out)
    if len(items) != 1:
        raise RuntimeError(f"{fn} 传入 1 条却返回 {len(items)} 条，query 向量已错位")
    return items[0]


def as_query_encoder(encoder: Any) -> Any:
    """把用户注入的编码器规整成 QueryEncoder。

    已经是单条接口的原样返回；只有批量接口（入湖那一个）的自动用
    :class:`ClipQueryEncoder` 包一层。两个都没有的直接报错，不等到检索时才炸。
    """
    if encoder is None:
        return None
    if hasattr(encoder, "encode_text") and hasattr(encoder, "encode_image"):
        return encoder
    if hasattr(encoder, "encode_texts") and hasattr(encoder, "encode_images"):
        return ClipQueryEncoder(encoder)
    raise TypeError(
        "注入的编码器既没有 encode_text/encode_image（QueryEncoder），"
        "也没有 encode_texts/encode_images（ClipEncoder），无法用于查询向量化"
    )


# ------------------------------------------------------------------ 第 ② 步：编译


def compile_scalar_filters(filters: ScalarFilters) -> tuple[list[str], list[Any]]:
    """把标量条件编译成参数化的 WHERE 片段。

    所有取值都走占位符 ``%s``，列名走白名单——用户输入永远不进 SQL 文本。
    先用标量条件缩小范围、再进 ANN 是原文明确的顺序（「检索快且准」）。

    :return: (条件片段列表, 参数列表)
    """
    clauses: list[str] = []
    params: list[Any] = []
    # 分区裁剪放最前：让 StarRocks 先剪掉不相关分区，再谈索引
    if filters.dt_from:
        clauses.append("`dt` >= %s")
        params.append(filters.dt_from.isoformat())
    if filters.dt_to:
        clauses.append("`dt` <= %s")
        params.append(filters.dt_to.isoformat())
    if filters.time_from:
        clauses.append("`capture_time` >= %s")
        params.append(filters.time_from)
    if filters.time_to:
        clauses.append("`capture_time` <= %s")
        params.append(filters.time_to)
    if filters.gps_bbox:
        min_lat, min_lon, max_lat, max_lon = filters.gps_bbox
        clauses.append("`gps_lat` BETWEEN %s AND %s")
        params.extend([min_lat, max_lat])
        clauses.append("`gps_lon` BETWEEN %s AND %s")
        params.extend([min_lon, max_lon])
    for column, value in filters.equals.items():
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            if not values:
                continue
            placeholders = ", ".join(["%s"] * len(values))
            clauses.append(f"`{column}` IN ({placeholders})")
            params.extend(values)
        else:
            clauses.append(f"`{column}` = %s")
            params.append(value)
    return clauses, params


# ------------------------------------------------------------------ 第 ③ 步：SQL


def _vector_literal(vec: Sequence[float]) -> str:
    """把 query 向量渲染成 SQL 数组字面量。

    向量值是服务端自己算出来的浮点数、不是用户文本，且必须内联（ANN 函数不接受
    参数化的数组），这里显式做一次 float() 转换，杜绝任何非数值混入。

    NaN / inf 也要拦：float("nan") 能过 float() 这一关，渲染出来是裸的 ``nan``，
    在 StarRocks 里既不是合法字面量也不会被当成语法错误报出来——检索会静默返回
    一堆 NULL 相似度，比直接报错难查得多。

    :raises ValueError: 向量为空，或含 NaN / inf
    """
    if not len(vec):
        raise ValueError("query 向量为空——检索不可能有意义")
    parts: list[str] = []
    for i, x in enumerate(vec):
        f = float(x)
        if f != f or f in (float("inf"), float("-inf")):  # NaN / ±inf
            raise ValueError(f"query 向量第 {i} 维不是有限浮点数: {x!r}")
        parts.append(f"{f:.6f}")
    return "[" + ", ".join(parts) + "]"


#: 每种检索模式实际命中的索引（原文第五章「四类检索能力」表的「索引路径」列，逐字）。
#: 注意文搜图走的是**双索引**：同一条 text query 向量既比图片向量（CLIP 双塔同空间，
#: 这才是「文搜图」三个字的本义），也比 caption 的文本向量；图搜图只走图片索引。
INDEX_PATH: dict[RetrievalMode, tuple[str, ...]] = {
    RetrievalMode.TEXT_TO_IMAGE: (IMAGE_INDEX_NAME, TEXT_INDEX_NAME),
    RetrievalMode.TAG_PLUS_VECTOR: (IMAGE_INDEX_NAME, TEXT_INDEX_NAME),
    RetrievalMode.IMAGE_TO_IMAGE: (IMAGE_INDEX_NAME,),
    RetrievalMode.HYBRID: (IMAGE_INDEX_NAME, TEXT_INDEX_NAME),
}


def index_path_for(mode: RetrievalMode) -> tuple[str, ...]:
    """某个检索模式命中哪几个 HNSW 索引（原文第五章表的「索引路径」列）。"""
    return INDEX_PATH[mode]


def prefilter_warning(filters: ScalarFilters) -> str | None:
    """没有任何预过滤条件时给出警告文案；有则返回 None。

    原文第六章把优化手段排在第一位的就是「dt 分区裁剪 + 标量预过滤：先把检索范围从全量
    缩到『某时段 × 某区域』，再进 ANN」。一条不带任何标量条件的检索会让 ANN 在千万~亿级
    全表上跑——P95 ≤ 2 秒这条唯一验收线基本就没了。

    这里**只警告不拦截**：全量语义检索偶尔是合理的（比如离线圈选），硬拦会误伤；
    但它必须在日志里留痕，否则「P95 为什么忽然崩了」永远查不到根因。
    """
    if filters.dt_from is not None or filters.dt_to is not None:
        return None
    if filters.time_from is not None or filters.time_to is not None:
        return None
    if filters.equals or filters.gps_bbox is not None:
        return None
    return (
        "本次向量检索没有任何标量预过滤条件（既无 dt 分区裁剪也无标量等值/范围条件），"
        f"ANN 将在全量数据上跑——原文唯一验收线是千万级 P95 ≤ {SEARCH_P95_SLA_SECONDS} 秒，"
        "请先用「某时段 × 某区域」把范围缩小再检索"
    )


def render_search_sql(
    request: SearchRequest,
    *,
    backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON,
    image_vector: Sequence[float] | None = None,
    text_vector: Sequence[float] | None = None,
) -> tuple[str, list[Any]]:
    """渲染向量检索 SQL：分区裁剪 + 版本过滤 + 标量预过滤三件事在一条语句里完成。

    索引路径严格按原文第五章「四类检索能力」表：

      · 文搜图 / 标签+向量：``text query 向量 → 双索引``——**同一条 text query 向量**
        分别去比 ``image_embedding`` 与 ``text_embedding``。前者是跨模态比对（CLIP 双塔
        把图文编到同一空间，这正是「文搜图」成立的前提），后者是和 caption 的同模态比对；
        两路按 image×w1 + text×w2 融合后排序。只比 text_embedding 的话，检索出来的是
        「caption 像的图」而不是「画面像的图」，双塔就白编了。
      · 图搜图：``image query 向量 → 图片索引``——只走 image_embedding，不碰文本索引。
      · 混合检索：``双向量加权融合 + 重排``——图 query 比图片向量、文 query 比文本向量。

    ⚠️ 函数名说明：``approx_cosine_similarity`` 是 StarRocks 的近似向量检索函数，
    命中 HNSW 索引；原文只给了「HNSW + 余弦相似度 + ORDER BY 取 TopK」的示意图，
    具体函数名属本项目按 StarRocks 语法补齐。

    :return: (SQL, 参数列表)
    """
    ref = external_table_ref() if backend is VectorBackend.EXTERNAL_PAIMON else internal_table_ref()
    warning = prefilter_warning(request.filters)
    if warning:
        _log.warning("%s（mode=%s）", warning, request.mode.value)
    clauses, params = compile_scalar_filters(request.filters)
    clauses.insert(0, render_active_filter(request.embedding_version))  # 版本过滤

    select_parts: list[str] = ["`image_id`"]
    order_expr: str
    if request.mode is RetrievalMode.IMAGE_TO_IMAGE:
        # 原文：image query 向量 → 图片索引。只有这一类检索是单索引。
        if image_vector is None:
            raise ValueError("image_to_image 需要图片 query 向量")
        select_parts.append(
            f"approx_cosine_similarity(`image_embedding`, {_vector_literal(image_vector)}) AS {IMAGE_SIM_ALIAS}"
        )
        order_expr = IMAGE_SIM_ALIAS
    else:
        if request.mode is RetrievalMode.HYBRID:
            # 原文：双向量加权融合 + 重排——两条 query 向量各打各的索引
            if image_vector is None or text_vector is None:
                raise ValueError("hybrid 需要图文两个 query 向量")
            image_probe, text_probe = image_vector, text_vector
        else:
            # 文搜图 / 标签+向量：原文「text query 向量 → 双索引」——
            # 一条 text query 向量同时打图片索引与文本索引
            if text_vector is None:
                raise ValueError(f"{request.mode.value} 需要文本 query 向量")
            image_probe, text_probe = text_vector, text_vector
        select_parts.append(
            f"approx_cosine_similarity(`image_embedding`, {_vector_literal(image_probe)}) AS {IMAGE_SIM_ALIAS}"
        )
        select_parts.append(
            f"approx_cosine_similarity(`text_embedding`, {_vector_literal(text_probe)}) AS {TEXT_SIM_ALIAS}"
        )
        # 第 ④ 步的融合公式：image×w1 + text×w2，下推到 SQL 里排序，避免把大结果集拉回服务层
        select_parts.append(
            f"({request.weights.image} * {IMAGE_SIM_ALIAS} + {request.weights.text} * {TEXT_SIM_ALIAS})"
            f" AS {FUSED_SCORE_ALIAS}"
        )
        order_expr = FUSED_SCORE_ALIAS

    index_hint = " + ".join(index_path_for(request.mode))
    where_sql = "\n  AND ".join(clauses)
    sql = (
        f"-- 检索模式: {request.mode.value} | 索引路径: {index_hint} | 档位: {backend.value}\n"
        f"-- 一条语句同时完成：分区裁剪、版本过滤（只查 active）、标量预过滤\n"
        f"SELECT {', '.join(select_parts)}\n"
        f"FROM {ref}\n"
        f"WHERE {where_sql}\n"
        f"ORDER BY {order_expr} DESC\n"
        f"LIMIT {request.top_k};"
    )
    return sql, params


def render_enrich_sql(
    image_ids: Sequence[str], *, backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON
) -> tuple[str, list[Any]]:
    """渲染第 ⑤ 步回补元数据的 SQL：命中后回 Paimon 补齐标签 / caption / 地理 / 时间。

    刻意不投影向量列——把千万级向量本体拉回服务层是打穿 P95 的最快方式。
    """
    if not image_ids:
        raise ValueError("回补元数据需要至少一个 image_id")
    ref = external_table_ref() if backend is VectorBackend.EXTERNAL_PAIMON else internal_table_ref()
    cols = ", ".join(f"`{c}`" for c in vector_columns_for_projection())
    placeholders = ", ".join(["%s"] * len(image_ids))
    sql = (
        f"-- 第 ⑤ 步：回补元数据（不投影向量列）\n"
        f"SELECT {cols}\nFROM {ref}\n"
        f"WHERE `image_id` IN ({placeholders})\n"
        f"  AND {render_active_filter()};"
    )
    return sql, list(image_ids)


# --------------------------------------------------------------------- 检索服务


@dataclass(slots=True)
class VectorSearchService:
    """统一检索 API。五步链路的唯一入口，上层应用不写 SQL。

    :param encoder: 查询向量化器（与入湖同一个 CLIP 模型）
    :param executor: SQL 执行器
    :param backend: 当前档位。外部表不达标时由 backend.BackendSelector 切成内表，
                    **切换对上层零感知**——请求与返回结构完全不变
    :param hnsw: HNSW 参数，检索时把 efSearch 以会话变量下推
    """

    encoder: Any = None
    executor: SqlExecutor = field(default_factory=lambda: get_executor())
    backend: VectorBackend = VectorBackend.EXTERNAL_PAIMON
    hnsw: HnswIndexParams = DEFAULT_HNSW_PARAMS

    def __post_init__(self) -> None:
        """把注入的编码器规整成单条接口，并当场对一次维度。

        维度不一致的编码器会让每一次检索都返回「相似度全是垃圾」的结果而不报错，
        所以宁可在构造服务的时候就炸。
        """
        self.encoder = as_query_encoder(self.encoder)
        if self.encoder is not None:
            dim = getattr(self.encoder, "dim", None)
            if dim is not None and int(dim) != self.hnsw.dim:
                raise ValueError(
                    f"编码器维度 {dim} 与 HNSW 索引 dim {self.hnsw.dim} 不一致——"
                    "query 向量和库内向量不在同一空间，检索结果不可信"
                )

    # ---- ① 查询向量化 ----

    def vectorize(self, request: SearchRequest) -> tuple[list[float] | None, list[float] | None]:
        """把用户的文本 / 图片用同一个 CLIP 模型编码成 query 向量。

        已经带向量的请求直接复用（挖掘平台的「以图搜图」常常拿库内向量发起二次检索）。
        """
        image_vec = (
            list(request.query_image_vector) if request.query_image_vector is not None else None
        )
        text_vec = (
            list(request.query_text_vector) if request.query_text_vector is not None else None
        )
        need_text = request.mode in (
            RetrievalMode.TEXT_TO_IMAGE,
            RetrievalMode.TAG_PLUS_VECTOR,
            RetrievalMode.HYBRID,
        )
        need_image = request.mode in (RetrievalMode.IMAGE_TO_IMAGE, RetrievalMode.HYBRID)
        if (need_text and text_vec is None) or (need_image and image_vec is None):
            if self.encoder is None:
                raise RuntimeError(
                    "请求未携带 query 向量，且未注入 QueryEncoder——"
                    "查询向量化必须用与入湖相同的 CLIP 模型，不能省略"
                )
            if need_text and text_vec is None:
                text_vec = list(self.encoder.encode_text(request.text))
            if need_image and image_vec is None:
                image_vec = list(self.encoder.encode_image(request.image_uri))
        for name, vec in (("image", image_vec), ("text", text_vec)):
            if vec is not None and len(vec) != self.hnsw.dim:
                raise ValueError(
                    f"{name} query 向量维度 {len(vec)} 与索引 dim {self.hnsw.dim} 不一致，检索结果不可信"
                )
        return image_vec, text_vec

    # ---- ④ 融合与重排 ----

    def fuse_and_rerank(
        self, rows: Sequence[dict[str, Any]], weights: FusionWeights, top_k: int
    ) -> list[SearchHit]:
        """图文双向量按权重加权融合（image×w1 + text×w2），在检索服务层完成重排。

        SQL 里已按融合分数排过一次；这里重算一遍是为了：
          · 混合检索时两路 TopK 合并后需要统一口径；
          · 服务层可以在不改 SQL 的前提下调权重做 A/B。
        """
        hits: list[SearchHit] = []
        for row in rows:
            image_sim = row.get(IMAGE_SIM_ALIAS)
            text_sim = row.get(TEXT_SIM_ALIAS)
            if image_sim is not None and text_sim is not None:
                score = weights.image * float(image_sim) + weights.text * float(text_sim)
            elif image_sim is not None:
                score = float(image_sim)
            elif text_sim is not None:
                score = float(text_sim)
            else:
                score = float(row.get(FUSED_SCORE_ALIAS) or 0.0)
            hits.append(
                SearchHit(
                    image_id=str(row["image_id"]),
                    score=score,
                    image_sim=float(image_sim) if image_sim is not None else None,
                    text_sim=float(text_sim) if text_sim is not None else None,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    # ---- ⑤ 回补元数据 ----

    def enrich(self, hits: Sequence[SearchHit]) -> list[SearchHit]:
        """命中后回 Paimon 补齐标签 / caption / 地理 / 时间，统一返回完整结果。"""
        if not hits:
            return []
        sql, params = render_enrich_sql([h.image_id for h in hits], backend=self.backend)
        result = self.executor.query(sql, params)
        by_id = {str(row["image_id"]): row for row in result.dicts()}
        return [
            SearchHit(h.image_id, h.score, h.image_sim, h.text_sim, by_id.get(h.image_id, {}))
            for h in hits
        ]

    # ---- 编排 ----

    def search(self, request: SearchRequest, *, enrich: bool = True) -> SearchResponse:
        """跑完五步链路。

        :param enrich: 是否执行第 ⑤ 步回补元数据（只要 image_id 的调用方可以关掉省一跳）
        :raises RuntimeError: SQL 执行失败
        """
        started = time.perf_counter()
        image_vec, text_vec = self.vectorize(request)
        sql, params = render_search_sql(
            request, backend=self.backend, image_vector=image_vec, text_vector=text_vec
        )
        self._apply_ann_params()
        result: QueryResult = self.executor.query(sql, params)
        hits = self.fuse_and_rerank(result.dicts(), request.weights, request.top_k)
        if enrich:
            hits = self.enrich(hits)
        elapsed = time.perf_counter() - started
        within = elapsed <= SEARCH_P95_SLA_SECONDS
        if not within:
            _log.warning(
                "单次向量检索耗时 %.3fs，超过验收线 %.1fs（mode=%s, backend=%s, top_k=%d）——"
                "先查分区裁剪与标量预过滤是否生效，再考虑降级第二档",
                elapsed,
                SEARCH_P95_SLA_SECONDS,
                request.mode.value,
                self.backend.value,
                request.top_k,
            )
        return SearchResponse(tuple(hits), elapsed, sql, self.backend, within)

    def _apply_ann_params(self) -> None:
        """把 efSearch 以会话变量下推。失败不致命——索引仍可用默认值检索。"""
        try:
            self.executor.execute("SET ann_params = %s", [self.hnsw.ann_params()])
        except Exception as exc:  # noqa: BLE001 - 会话变量不支持时不应中断检索
            _log.debug("下推 ann_params 失败（不影响检索，走索引默认值）: %s", exc)

    # ---- 便捷入口：四类检索能力 ----

    def text_to_image(self, text: str, **kwargs: Any) -> SearchResponse:
        """文搜图：「雨天夜间高速行人横穿」找同类场景。"""
        return self.search(SearchRequest(RetrievalMode.TEXT_TO_IMAGE, text=text, **kwargs))

    def image_to_image(self, image_uri: str, **kwargs: Any) -> SearchResponse:
        """图搜图：Badcase 找相似样本。"""
        return self.search(
            SearchRequest(RetrievalMode.IMAGE_TO_IMAGE, image_uri=image_uri, **kwargs)
        )

    def tag_plus_vector(self, text: str, filters: ScalarFilters, **kwargs: Any) -> SearchResponse:
        """标签 + 向量：限定城市 / 时段内的语义检索。"""
        return self.search(
            SearchRequest(RetrievalMode.TAG_PLUS_VECTOR, text=text, filters=filters, **kwargs)
        )

    def hybrid(self, text: str, image_uri: str, **kwargs: Any) -> SearchResponse:
        """混合检索：图文互补提升召回精度。"""
        return self.search(
            SearchRequest(RetrievalMode.HYBRID, text=text, image_uri=image_uri, **kwargs)
        )

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        """四类检索能力的中文说明，便于前端直接渲染能力清单。"""
        return tuple(
            f"{c.name_cn}（{c.index_path_cn}）：{c.scenario_cn}" for c in RETRIEVAL_CAPABILITIES
        )
