"""向量检索子系统的全部常量：SLA、流水线节拍、HNSW 索引参数、POC 验收清单。

来源（两篇，都是原文）：

  [a7] 系列二 · 湖仓实战 第 7 篇《HNSW 向量索引落地 StarRocks：从 Paimon 外部表到语义
       检索》（小周，2026-09-06）——本模块下文不加篇号的「原文第 N 章」一律指本篇；
  [a5] 全景综述特辑《智驾数据闭环的湖仓架构全景》（小周，2026-09-08）第七章——
       索引落地参数「cosine 距离、M=16、efConstruction=200」只在本篇正文出现，
       [a7] 对应位置是一张未公开文字版的图片，正文只说「POC 阶段压测定参」。
       引用这组数字时必须标 [a5]，标成 [a7] 会被原文打脸。

本模块是整个 vector 包的「数字唯一出处」：凡原文给出的具体数字一律逐字落成常量，并在
注释里标注篇号与章节出处；凡原文没给、由本项目补的取值，一律用「⚠️ 原文未明确，本项目设计：」
显式标注，绝不冒充原文方案——反过来也一样，**原文给了的数字不许标成本项目设计**。

原文的核心主张（三句话）：
  1. 向量长在哪，索引就建在哪——向量落湖于 Paimon，HNSW 索引建在 StarRocks 外部表上，
     不新增一套向量数据库、不产生第二份副本；
  2. 分区设计撑起增量刷新——按 dt 分区让每日新数据只刷新当日分区索引，
     embedding_version 入主键让模型换代可灰度可回滚；
  3. 降级路径提前设计——外部表优先、内表兜底，检索 API 对上层零感知，
     P95 ≤ 2s 是唯一验收线。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "SEARCH_P95_SLA_SECONDS",
    "SEARCH_SLA_SCALE_CN",
    "VECTOR_TABLE_SCALE_CN",
    "EXAMPLE_SCALAR_FILTER_RECENT_DAYS",
    "PIPELINE_DEADLINE_HOUR",
    "PIPELINE_STEPS",
    "PIPELINE_STEP_COUNT",
    "PIPELINE_RUNTIME_CN",
    "PipelineStep",
    "RETRIEVAL_CAPABILITIES",
    "RetrievalCapability",
    "RetrievalMode",
    "POC_CHECKLIST",
    "POC_CHECK_COUNT",
    "PocCheckItem",
    "VectorBackend",
    "VECTOR_STORE_OPTIONS",
    "VectorStoreOption",
    "HnswIndexParams",
    "DEFAULT_HNSW_PARAMS",
    "HNSW_POC_SWEEP_GRID",
    "DEFAULT_VECTOR_DIM",
    "DEFAULT_METRIC_TYPE",
    "SIMILARITY_METRIC_NAME",
    "DEFAULT_INDEX_REFRESH_STATUS",
    "INDEX_REFRESH_PENDING",
    "INDEX_REFRESH_REFRESHING",
    "INDEX_REFRESH_DONE",
    "INDEX_REFRESH_STATUSES",
    "DEFAULT_TOP_K",
    "MAX_TOP_K",
    "DEFAULT_IMAGE_WEIGHT",
    "DEFAULT_TEXT_WEIGHT",
    "DEFAULT_NORMAL_SAMPLE_RATIO",
    "DEFAULT_GPU_WINDOW_START_HOUR",
    "DEFAULT_EMBEDDING_BATCH_SIZE",
    "DEFAULT_INDEX_BUILD_TIMEOUT_SEC",
]


# --------------------------------------------------------------------------- SLA

#: 原文第六章：「性能目标只有一条：千万级数据量单次向量检索 P95 ≤ 2 秒」。
#: 同一数字在第四章第 5 条前置验证项里再次出现：「千万级检索 P95 ≤ 2 秒 -- 这是整条链路的验收线」。
#: 它同时是外部表 → 内表降级的唯一触发条件（见 backend.BackendSelector）。
SEARCH_P95_SLA_SECONDS: float = 2.0

#: 原文口径：SLA 对应的数据规模是「千万级」，不是全湖亿级。
SEARCH_SLA_SCALE_CN: str = "千万级"

#: 原文第二章：dwd_mining_image_vector_detail 是「全湖体量最大的明细表（千万~亿级行 × 高维向量）」。
VECTOR_TABLE_SCALE_CN: str = "千万~亿级行"

#: 原文第一章标量融合示例：「最近 30 天 + 某城市 + 雨天」——标量预过滤的典型时间窗。
EXAMPLE_SCALAR_FILTER_RECENT_DAYS: int = 30


# ------------------------------------------------------------------ Embedding 流水线


@dataclass(frozen=True, slots=True)
class PipelineStep:
    """Embedding 五步流水线中的一步（原文第三章表格，逐字誊录）。"""

    ordinal: int
    name_cn: str
    mechanism_cn: str


#: 原文第三章：「T+1 向量化流水线每日凌晨 6 点前完成增量处理」。
#: 这是调度的硬截止时间：当日新增数据必须在 06:00 前完成向量化 + 索引刷新，
#: 才能兑现「新数据当日可检索」。
PIPELINE_DEADLINE_HOUR: int = 6

#: 原文第三章五步流水线，「关键机制」列逐字誊录。
PIPELINE_STEPS: tuple[PipelineStep, ...] = (
    PipelineStep(1, "增量识别", "按 create_time / update_time 水位，仅处理新增与标签变更图片"),
    PipelineStep(
        2,
        "成本分级",
        "高价值数据全量处理，普通数据按比例抽样，GPU 空闲时段分批——GPU 是稀缺资源，成本必须分级",
    ),
    PipelineStep(3, "双路编码", "图片与 caption 经同一 CLIP 模型双塔编码，保证向量同空间"),
    PipelineStep(
        4, "幂等写回", "按 (image_id, embedding_version) Upsert，重跑无副作用；标签变图片不变不重算"
    ),
    PipelineStep(5, "索引刷新", "写入完成后通知 StarRocks 增量刷新当日分区索引，新数据当日可检索"),
)

#: 原文第三章：「共五步」。
PIPELINE_STEP_COUNT: int = 5

#: 原文第三章实现说明：「实现上采用 Spark / Ray + GPU 算子，批次失败可断点续跑」。
PIPELINE_RUNTIME_CN: str = "Spark / Ray + GPU 算子，批次失败可断点续跑"


# ------------------------------------------------------------------------ 检索能力


class RetrievalMode(str, Enum):
    """原文第五章「四类检索能力」。枚举值同时用作检索 API 的入参。"""

    #: 文搜图：「雨天夜间高速行人横穿」找同类场景
    TEXT_TO_IMAGE = "text_to_image"
    #: 图搜图：Badcase 找相似样本
    IMAGE_TO_IMAGE = "image_to_image"
    #: 标签 + 向量：限定城市 / 时段内的语义检索
    TAG_PLUS_VECTOR = "tag_plus_vector"
    #: 混合检索：图文双向量加权融合 + 重排
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class RetrievalCapability:
    """一类检索能力（原文第五章表格，逐字誊录）。"""

    mode: RetrievalMode
    name_cn: str
    index_path_cn: str
    scenario_cn: str


#: 原文第五章：「四类检索能力覆盖挖掘平台的日常场景」。
RETRIEVAL_CAPABILITIES: tuple[RetrievalCapability, ...] = (
    RetrievalCapability(
        RetrievalMode.TEXT_TO_IMAGE,
        "文搜图",
        "text query 向量 → 双索引",
        "「雨天夜间高速行人横穿」找同类场景",
    ),
    RetrievalCapability(
        RetrievalMode.IMAGE_TO_IMAGE, "图搜图", "image query 向量 → 图片索引", "Badcase 找相似样本"
    ),
    RetrievalCapability(
        RetrievalMode.TAG_PLUS_VECTOR,
        "标签 + 向量",
        "标量预过滤 + ANN",
        "限定城市 / 时段内的语义检索",
    ),
    RetrievalCapability(
        RetrievalMode.HYBRID, "混合检索", "双向量加权融合 + 重排", "图文互补提升召回精度"
    ),
)


# ---------------------------------------------------------------------- 向量库选型


class VectorBackend(str, Enum):
    """向量检索的两档形态（原文第六章降级路径）。

    两种形态共用同一条检索 API，切换对上层应用零感知；Paimon 始终是单一事实源与对账基准。
    """

    #: 第一档（优先）：StarRocks External Catalog 直查 Paimon，HNSW 索引建在外部表上，免冗余
    EXTERNAL_PAIMON = "external_paimon"
    #: 第二档（降级）：向量数据冗余写入 StarRocks 内表，定时同步 + 主键对账
    INTERNAL_STARROCKS = "internal_starrocks"


@dataclass(frozen=True, slots=True)
class VectorStoreOption:
    """向量库三方案对比中的一项（原文第一章表格，逐字誊录）。"""

    name_cn: str
    advantage_cn: str
    cost_cn: str
    chosen: bool


#: 原文第一章：「业界主流三类方案，各有取舍」，本项目选第三类。
VECTOR_STORE_OPTIONS: tuple[VectorStoreOption, ...] = (
    VectorStoreOption(
        "专用向量库（Milvus 类）",
        "十亿级规模、存算分离",
        "新增独立系统，运维成本高，数据双副本",
        False,
    ),
    VectorStoreOption(
        "搜索引擎（Elasticsearch 类）", "生态成熟、全文检索强", "大规模向量受限，同样双副本", False
    ),
    VectorStoreOption(
        "StarRocks 向量索引",
        "湖仓一体、外部表免冗余、标量过滤原生融合",
        "超大向量规模能力需 POC 验证",
        True,
    ),
)


# ------------------------------------------------------------------- HNSW 索引参数


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw else default


#: 原文第四章：StarRocks「在外部 Paimon 表上构建两个 HNSW 索引（图文各一个），采用余弦相似度」；
#: [a5] 第七章同样写「cosine 距离」。
#: 这个字面量是**索引 PROPERTIES 的取值**（StarRocks 语法）；落到表里那一列的取值见
#: :data:`SIMILARITY_METRIC_NAME`——两者指的是同一件事，只是一个是 StarRocks 的键值、
#: 一个是 registry 里 ``similarity_metric`` 列注释写死的「cosine」。
DEFAULT_METRIC_TYPE: str = "cosine_similarity"

#: 写进 dwd_mining_image_vector_detail.similarity_metric 列的取值。
#: registry 该列注释：「相似度度量：cosine（HNSW 图文双索引均采用余弦相似度）」；
#: 与 ``ads.constants.HNSW_METRIC`` 同为 "cosine"，三处必须一致（[a5] 七「cosine 距离」）。
SIMILARITY_METRIC_NAME: str = "cosine"

#: index_refresh_status 列的三个取值，逐字取自 registry 该列注释：
#: 「当日分区索引刷新状态：pending/refreshing/done」。
INDEX_REFRESH_PENDING: str = "pending"
INDEX_REFRESH_REFRESHING: str = "refreshing"
INDEX_REFRESH_DONE: str = "done"
INDEX_REFRESH_STATUSES: tuple[str, ...] = (
    INDEX_REFRESH_PENDING,
    INDEX_REFRESH_REFRESHING,
    INDEX_REFRESH_DONE,
)

#: 向量行落库那一刻索引还没刷，所以初始值是 pending，由流水线第 ⑤ 步刷完当日分区后
#: 改写成 done（见 embedding.render_mark_refreshed_sql）。
DEFAULT_INDEX_REFRESH_STATUS: str = INDEX_REFRESH_PENDING

#: ⚠️ 原文未明确，本项目设计：原文只说图文双向量来自「同一个 CLIP 模型的双塔编码」，
#: 未给出向量维度。这里取 CLIP ViT-B/32 的 512 维作为默认值，可用环境变量
#: VECTOR_EMBEDDING_DIM 覆盖；换模型时必须同步改 embedding_version 与索引 dim 属性。
DEFAULT_VECTOR_DIM: int = _env_int("VECTOR_EMBEDDING_DIM", 512)


@dataclass(frozen=True, slots=True)
class HnswIndexParams:
    """HNSW 索引参数。

    **两套原文口径，都要说清**：

    * [a7] 第六章只写了「索引参数调优：M / efConstruction 在 POC 阶段按数据规模压测定参」，
      本篇正文没有落地数值（第四章那张索引 DDL 在原文里是图片，文字版未公开）；
    * [a5]（全景综述）第七章把落地值写进了正文：「向量落湖于 Paimon，HNSW 索引建在
      StarRocks 外部表上（**cosine 距离、M=16、efConstruction=200**），按分区增量刷新」。

    因此 ``m=16`` / ``ef_construction=200`` **是原文数字（[a5] 第七章逐字照抄）**，
    不是本项目拍的；``metric_type`` 同样来自原文（[a7] 四「余弦相似度」/ [a5] 七「cosine 距离」）。
    同一组数字在 ``ads.constants.HNSW_M`` / ``HNSW_EF_CONSTRUCTION`` 也有登记，两处必须一致。

    ⚠️ 原文未明确，本项目设计：``ef_search``（检索期候选队列长度）原文两篇都没提，
    默认 128 是本项目按 HNSW 通用经验给的起跑值。

    [a7] 的「POC 压测定参」与 [a5] 的落地值并不矛盾——前者说的是方法，后者是压测后的结论。
    换数据规模时仍应拿 ``HNSW_POC_SWEEP_GRID`` 重新压测，用 ``SEARCH_P95_SLA_SECONDS``
    （原文唯一验收线，2 秒）卡出实际取值；扫参网格默认已把 (16, 200) 包含在内。

    :param m: HNSW 每层最大出边数（原文 [a5] 七：M=16）
    :param ef_construction: 建索引时的候选队列长度（原文 [a5] 七：efConstruction=200）
    :param ef_search: 检索时的候选队列长度，可按查询下推，直接换取召回率 / 延迟
        （⚠️ 原文未给，本项目默认 128）
    :param metric_type: 度量方式，原文指定余弦相似度
    :param dim: 向量维度，必须与 embedding_version 绑定的模型输出维度一致
    :param is_vector_normed: 向量是否已归一化（归一化后余弦相似度可退化为点积，检索更快）
    """

    m: int = _env_int("VECTOR_HNSW_M", 16)
    ef_construction: int = _env_int("VECTOR_HNSW_EF_CONSTRUCTION", 200)
    ef_search: int = _env_int("VECTOR_HNSW_EF_SEARCH", 128)
    metric_type: str = DEFAULT_METRIC_TYPE
    dim: int = DEFAULT_VECTOR_DIM
    is_vector_normed: bool = True

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """参数自检。非法参数在渲染 DDL 之前就要炸，别等 StarRocks 报错。"""
        if self.m <= 0:
            raise ValueError(f"HNSW M 必须为正整数，收到 {self.m}")
        if self.ef_construction < self.m:
            raise ValueError(
                f"efConstruction({self.ef_construction}) 不应小于 M({self.m})，否则建索引质量无保障"
            )
        if self.ef_search <= 0:
            raise ValueError(f"efSearch 必须为正整数，收到 {self.ef_search}")
        if self.dim <= 0:
            raise ValueError(f"向量维度必须为正整数，收到 {self.dim}")
        if self.metric_type not in ("cosine_similarity", "l2_distance"):
            raise ValueError(
                f"metric_type 只支持 cosine_similarity / l2_distance，收到 {self.metric_type!r}；"
                "原文第四章指定余弦相似度"
            )

    def index_properties(self) -> dict[str, str]:
        """渲染成 StarRocks 向量索引的 PROPERTIES 字典。"""
        return {
            "index_type": "hnsw",
            "dim": str(self.dim),
            "metric_type": self.metric_type,
            "is_vector_normed": "true" if self.is_vector_normed else "false",
            "M": str(self.m),
            "efconstruction": str(self.ef_construction),
        }

    def ann_params(self) -> str:
        """渲染成 StarRocks 会话级 ANN 参数（检索时下推 efSearch）。"""
        return f'{{"efsearch":"{self.ef_search:d}"}}'

    def with_(self, **changes: object) -> HnswIndexParams:
        """派生一个改了若干字段的新参数对象（POC 压测扫参用）。"""
        base = {
            "m": self.m,
            "ef_construction": self.ef_construction,
            "ef_search": self.ef_search,
            "metric_type": self.metric_type,
            "dim": self.dim,
            "is_vector_normed": self.is_vector_normed,
        }
        base.update(changes)  # type: ignore[arg-type]
        return HnswIndexParams(**base)  # type: ignore[arg-type]


#: 默认索引参数：M / efConstruction / metric 来自原文（[a5] 七），
#: efSearch 为本项目默认值，见 HnswIndexParams 的说明。
DEFAULT_HNSW_PARAMS: HnswIndexParams = HnswIndexParams()

#: ⚠️ 原文未明确，本项目设计：[a7] 六只说「M / efConstruction 在 POC 阶段按数据规模压测定参」，
#: 没给扫参范围（[a5] 七给的是压测后的落地值 16 / 200，不是扫参区间）。
#: 这里给出一张压测网格，供 index.run_poc_sweep() 逐组压测；原文落地值必须落在网格内。
HNSW_POC_SWEEP_GRID: dict[str, tuple[int, ...]] = {
    "m": (8, 16, 32, 64),
    "ef_construction": (100, 200, 400),
    "ef_search": (64, 128, 256, 512),
}

# 防漂移断言：原文（[a5] 七）的落地值必须同时是默认值、且在扫参网格内。
assert DEFAULT_HNSW_PARAMS.m in HNSW_POC_SWEEP_GRID["m"]
assert DEFAULT_HNSW_PARAMS.ef_construction in HNSW_POC_SWEEP_GRID["ef_construction"]
# 余弦相似度是原文两篇都写死的（[a7] 四「余弦相似度」/ [a5] 七「cosine 距离」），
# 不在压测范围内——扫参只调 M / efConstruction / efSearch，不许把度量方式一起扫掉。
assert DEFAULT_HNSW_PARAMS.metric_type == DEFAULT_METRIC_TYPE == "cosine_similarity"
assert SIMILARITY_METRIC_NAME in DEFAULT_METRIC_TYPE
assert "metric_type" not in HNSW_POC_SWEEP_GRID


# ------------------------------------------------------------------- POC 前置验证


@dataclass(frozen=True, slots=True)
class PocCheckItem:
    """一条 POC 前置验证项（原文第四章清单，逐字誊录）。"""

    ordinal: int
    name_cn: str
    question_cn: str
    #: 是否为「硬验收线」——不达标直接触发降级
    blocking: bool


#: 原文第四章：「外部表向量索引是相对新的能力，全量上线前必须先过 POC 验证。五个前置验证项」。
POC_CHECKLIST: tuple[PocCheckItem, ...] = (
    PocCheckItem(1, "多向量列同表索引支持度", "两个 ARRAY<FLOAT> 列能否各建一个", True),
    PocCheckItem(2, "分区级索引支持度", "刷新能否精确到单个分区", True),
    PocCheckItem(3, "索引构建耗时", "千万级向量的建索引时间", False),
    PocCheckItem(4, "增量刷新延迟", "当日分区刷完的时间窗", False),
    PocCheckItem(5, "千万级检索 P95", "P95 ≤ 2 秒 —— 这是整条链路的验收线", True),
)

#: 原文第四章：「五个前置验证项」。
POC_CHECK_COUNT: int = 5


# ------------------------------------------------------------- 本项目补充的运行参数

#: ⚠️ 原文未明确，本项目设计：原文第六章只说「TopK 合理取值：不为用不到的长尾结果付出
#: 检索成本」，未给数值。默认 50 条满足挖掘平台一屏浏览，上限 1000 防止长尾拖垮 P95。
DEFAULT_TOP_K: int = _env_int("VECTOR_DEFAULT_TOP_K", 50)
MAX_TOP_K: int = _env_int("VECTOR_MAX_TOP_K", 1000)

#: ⚠️ 原文未明确，本项目设计：原文第五章只写了融合公式「image×w1 + text×w2」，
#: 没给 w1 / w2 取值。默认图文等权，权重和必须为 1.0（见 search.FusionWeights）。
DEFAULT_IMAGE_WEIGHT: float = _env_float("VECTOR_FUSION_IMAGE_WEIGHT", 0.5)
DEFAULT_TEXT_WEIGHT: float = _env_float("VECTOR_FUSION_TEXT_WEIGHT", 0.5)

#: ⚠️ 原文未明确，本项目设计：原文第三章成本分级只说「普通数据按比例抽样」，未给比例。
#: 默认 10% 抽样，可用环境变量 VECTOR_NORMAL_SAMPLE_RATIO 覆盖。
DEFAULT_NORMAL_SAMPLE_RATIO: float = _env_float("VECTOR_NORMAL_SAMPLE_RATIO", 0.1)

#: ⚠️ 原文未明确，本项目设计：原文只说「GPU 空闲时段分批」，未给时段。默认取 00:00 起跑，
#: 与原文的 06:00 硬截止（PIPELINE_DEADLINE_HOUR）组成 [00:00, 06:00) 的 GPU 窗口。
DEFAULT_GPU_WINDOW_START_HOUR: int = _env_int("VECTOR_GPU_WINDOW_START_HOUR", 0)

#: ⚠️ 原文未明确，本项目设计：GPU 批大小，原文只提「分批」「批次失败可断点续跑」。
DEFAULT_EMBEDDING_BATCH_SIZE: int = _env_int("VECTOR_EMBEDDING_BATCH_SIZE", 256)

#: ⚠️ 原文未明确，本项目设计：索引构建 / 刷新的客户端超时（秒）。原文把「索引构建耗时」
#: 列为 POC 验证项但未给时间窗，这里给 2 小时兜底，确保仍落在 06:00 截止之内。
DEFAULT_INDEX_BUILD_TIMEOUT_SEC: int = _env_int("VECTOR_INDEX_BUILD_TIMEOUT_SEC", 7200)
