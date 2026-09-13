"""Paimon Variant：向量元数据的半结构化落地（vector_meta 列）。

来源：《Paimon 2.0 系列：从 JSON 到 Variant，电商与智驾的半结构化实践》
（李劲松，2026-08-17，以下简称「a9 原文」）。

为什么向量表需要 Variant：向量行上挂的元数据（场景标签全集、感知事件摘要、模型调试
属性）字段随车型、传感器和模型版本变化，但检索时只反复读取其中少数几条路径——
a9 原文给汽车智驾场景的物理边界建议正好覆盖这一点：

  | 数据                                             | 推荐表示              |
  | vehicle_id、采集时间、场景、地理网格、软件/模型版本  | 正式类型列            |
  | CAN/诊断信号、感知事件摘要、场景标签、模型调试属性   | VARIANT，热路径 shredding |
  | 摄像头视频、点云、原始张量                         | BLOB 列               |

因此 schema.py 里 dt / capture_time / geo_grid / model_version / scene_tag 等
高选择率过滤列保持正式类型列（它们要参与分区、主键、join 和强 SLA 过滤），
长尾属性统一进 vector_meta VARIANT，热路径做 shredding。

a9 原文的三条量化结论（逐字）：
  · 对宽文档、重复读取少量路径的场景，Variant 相对 JSON String 的优势可以很大，10 倍提升！
  · Shredding 会把选定路径物化为带类型的 Parquet 子列，大幅提升 Projection 性能，30 倍提升！
  · Variant Replace / Set 是 Paimon 当前独有的路径级修改能力，30 倍提升！
反面结论同样逐字保留：对几百字节的小文档、只查一次或总是读取完整对象的场景，
Variant 可能不快，并且还带来了转换开销。

外部依赖 pypaimon / pyarrow 全部延迟导入：没装也能 import 本模块、渲染 DDL 与算回本点。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "VariantRepresentation",
    "VECTOR_META_HOT_PATHS",
    "VECTOR_META_SHREDDING_SCHEMA",
    "INFER_SHREDDING_OPTIONS",
    "SQL_ENGINE_REQUIREMENT",
    "VariantSizeBench",
    "VariantPathGetBench",
    "VariantFileBench",
    "ShreddingAbBench",
    "VariantReplaceBench",
    "SIZE_BENCH",
    "PATH_GET_BENCH",
    "ENCODE_COST_US",
    "FILE_BENCH",
    "SHREDDING_AB_BENCH",
    "REPLACE_BENCH",
    "BREAKEVEN_READS",
    "PYTHON_ENCODE_COST_US_PER_ROW",
    "DUAL_WRITE_COMPARE_DAYS",
    "recommend_representation",
    "breakeven_reads",
    "estimate_total_cost",
    "render_variant_get",
    "build_meta",
    "variant_get_column",
    "variant_set_paths",
    "to_variant_array",
    "WIDE_DOC_SIZE_OVERHEAD_PCT",
    "VARIANT_ENCODE_OVERHEAD_RANGE",
    "PER_READ_SAVING_US",
    "SMALL_DOC_VARIANT_SLOWDOWN_PCT",
    "FILE_BENCH_ROWS",
    "FILE_BENCH_FIELDS_PER_ROW",
    "FILE_BENCH_UNCOMPRESSED_JSON_KIB_PER_ROW",
    "SHREDDING_AB_FILE_SIZE_MIB",
    "SHREDDING_AB_READ_BYTES_MIB",
    "SHREDDING_AB_ROWS",
    "SHREDDING_AB_ROW_GROUP_MIB",
    "PRODUCTION_ECOMMERCE",
    "PRODUCTION_AUTOMOTIVE",
]

_log = logging.getLogger(__name__)


# ------------------------------------------------------------------ 表示形态选择


class VariantRepresentation(str, Enum):
    """半结构化数据的四种物理表示（a9 原文第 07 节「最终选择可以落到下面四类」）。"""

    #: 原文必须逐字节保留，几乎不查询内部字段 -> 写入最便宜，避免无收益的编码
    JSON_STRING = "json_string"
    #: schema 高频变化，会重复查询但热路径不稳定 -> 解析一次，保持 schema-on-read
    PLAIN_VARIANT = "plain_variant"
    #: 文档很宽，少数路径被反复投影/过滤 -> typed leaf 可列裁剪、压缩并使用统计量
    SHREDDED_VARIANT = "shredded_variant"
    #: 字段参与主键、分区、join、排序或强 SLA 过滤 -> 正式类型列，可同时保留 Variant
    TYPED_COLUMN = "typed_column"


def recommend_representation(
    *,
    fields_change_often: bool,
    hot_paths_stable: bool,
    participates_in_key_or_filter: bool,
    reads_per_write: float,
    doc_is_wide: bool,
) -> VariantRepresentation:
    """按 a9 原文第 07 节的四类场景表给出表示形态建议。

    :param fields_change_often: schema 是否高频变化
    :param hot_paths_stable: 热路径是否稳定（稳定才值得显式 shredding）
    :param participates_in_key_or_filter: 是否参与主键 / 分区 / join / 排序 / 强 SLA 过滤
    :param reads_per_write: 每次写入对应的读取次数——用于对照回本点（见 BREAKEVEN_READS）
    :param doc_is_wide: 是否宽文档（a9 的 200 字段档位属于宽文档）
    :return: 推荐表示形态

    判定顺序遵循原文：先看是否该提升为正式列，再看读写比是否够摊平编码成本。
    """
    if participates_in_key_or_filter:
        return VariantRepresentation.TYPED_COLUMN
    threshold = (
        BREAKEVEN_READS["flat_200_fields"] if doc_is_wide else BREAKEVEN_READS["flat_20_fields"]
    )
    if reads_per_write < threshold:
        # 读得太少，摊不平首次编码成本——原文：写入最便宜，避免无收益的编码
        return VariantRepresentation.JSON_STRING
    if doc_is_wide and hot_paths_stable:
        return VariantRepresentation.SHREDDED_VARIANT
    if fields_change_often or not hot_paths_stable:
        return VariantRepresentation.PLAIN_VARIANT
    return VariantRepresentation.SHREDDED_VARIANT


# ------------------------------------------------------------------ vector_meta 设计

#: vector_meta 里被检索链路反复投影 / 过滤的热路径。
#: ⚠️ 原文未明确，本项目设计：a9 只给出「感知事件摘要、场景标签、模型调试属性放 VARIANT，
#: 热路径 shredding」的原则与 payload:perception.weather / payload:diagnostics.code
#: 两个路径示例，具体路径集由本项目按向量检索场景定义。
VECTOR_META_HOT_PATHS: dict[str, str] = {
    "$.perception.weather": "STRING",  # a9 原文示例路径 payload:perception.weather
    "$.perception.object_count": "INT",
    "$.scene.tags": "STRING",
    "$.diagnostics.code": "STRING",  # a9 原文示例路径 payload:diagnostics.code
    "$.model.debug_score": "DOUBLE",
}


def _shredding_schema_json(column: str = "vector_meta") -> str:
    """把热路径编译成 Paimon 的 variant.shreddingSchema JSON（显式 schema）。

    a9 原文：「显式 schema 适合热路径稳定的生产表；自动推断适合探索期」——
    向量表的热路径由检索 API 固定，属于前者。
    """
    fields = []
    for path, sql_type in VECTOR_META_HOT_PATHS.items():
        # $.a.b -> a_b：Paimon shreddingSchema 的字段名不含路径分隔符
        name = path.removeprefix("$.").replace(".", "_")
        fields.append({"name": name, "type": sql_type})
    schema = {
        "type": "ROW",
        "fields": [{"name": column, "type": {"type": "ROW", "fields": fields}}],
    }
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


#: 生产表用的显式 shredding schema（写进 Paimon TBLPROPERTIES）。
VECTOR_META_SHREDDING_SCHEMA: str = _shredding_schema_json()

#: 自动推断参数，四个数值逐字取自 a9 原文第 04 节的 TBLPROPERTIES 示例。
#: 「自动推断会缓存每个文件开头的最多 4096 行，超过宽度/深度限制、过于稀有或类型冲突的
#: 字段不做 typed shredding。」探索期可打开，生产表用显式 schema。
INFER_SHREDDING_OPTIONS: dict[str, str] = {
    "variant.inferShreddingSchema": "true",
    "variant.shredding.maxInferBufferRow": "4096",
    "variant.shredding.maxSchemaWidth": "300",
    "variant.shredding.maxSchemaDepth": "50",
    "variant.shredding.minFieldCardinalityRatio": "0.1",
}

#: a9 原文第 03 节：「SQL 侧要求 Spark 4.0+ 或 Flink 2.1+，数据文件必须是 Parquet」。
SQL_ENGINE_REQUIREMENT: dict[str, str] = {
    "spark": "4.0+",
    "flink": "2.1+",
    "file_format": "parquet",
}

#: a9 原文第 07 节迁移建议：「先做双写：保留原始 JSON，同时生成 Variant；
#: 用真实查询回放比较 7–14 天」。
DUAL_WRITE_COMPARE_DAYS: tuple[int, int] = (7, 14)


# ------------------------------------------------------------------------ 基准数据
# 下列常量全部逐字誊录自 a9 原文的基准表，用于容量规划与选型复核，不做四舍五入。
# 基准方法（原文第 02 节）：每个独立 JVM 预热 5 轮、测量 9 轮取中位数，共运行 3 个 JVM，
# 再取三次中位数；Variant 使用 GenericVariant.fromJson 和 variantGet，
# JSON 使用同版本 Jackson readTree。


@dataclass(frozen=True, slots=True)
class VariantSizeBench:
    """未压缩的单值大小对比（a9 第 02 节表一）。"""

    doc_cn: str
    json_bytes: int
    variant_bytes: int
    ratio: float


#: 「Variant 的裸字节并不保证更小；宽对象的 metadata 甚至让单值大了 6.8%。」
SIZE_BENCH: tuple[VariantSizeBench, ...] = (
    VariantSizeBench("4 层嵌套小对象", 223, 215, 0.964),
    VariantSizeBench("扁平 20 字段", 241, 236, 0.979),
    VariantSizeBench("扁平 200 字段", 2781, 2971, 1.068),
)
#: 宽对象单值增大的比例（原文逐字：6.8%）。
WIDE_DOC_SIZE_OVERHEAD_PCT: float = 6.8


@dataclass(frozen=True, slots=True)
class VariantPathGetBench:
    """单路径读取对比，单位 μs/op（a9 第 02 节表二）。"""

    doc_cn: str
    json_parse_get_us: float
    variant_path_get_us: float
    speedup_cn: str


PATH_GET_BENCH: tuple[VariantPathGetBench, ...] = (
    VariantPathGetBench("223 B 小对象", 0.582, 0.664, "Variant 慢 14%"),
    VariantPathGetBench("20 字段，$.field19", 0.642, 0.378, "Variant 1.70×"),
    VariantPathGetBench("200 字段，$.field199", 8.871, 0.257, "Variant 34.6×"),
)

#: 写入侧：Jackson 只构建 JSON tree vs Paimon 构建完整 Variant，单位 μs（a9 第 02 节正文）。
#: 「Variant 编码约为单纯 JSON 解析的 2.9–4.9 倍」。
ENCODE_COST_US: dict[str, tuple[float, float]] = {
    "flat_20_fields": (0.779, 2.687),
    "nested_small": (0.562, 1.621),
    "flat_200_fields": (8.691, 42.171),
}
VARIANT_ENCODE_OVERHEAD_RANGE: tuple[float, float] = (2.9, 4.9)


@dataclass(frozen=True, slots=True)
class VariantFileBench:
    """文件级读写对比（a9 第 03 节表）。

    测试条件逐字：1 万行、每行 200 个 DOUBLE 字段、Snappy 和 PyArrow 默认 dictionary 设置；
    未压缩 JSON 平均约 4.16 KiB/row；路径读取运行 5 次、写入和完整读取运行 3 次，
    均报告热 page cache 中位数；shredded 文件仅物化一个热字段。
    """

    representation: str
    file_size_mb: float
    parquet_write_ms: float
    hot_path_read_ms: float
    full_decode_ms: float
    note_cn: str


FILE_BENCH: tuple[VariantFileBench, ...] = (
    VariantFileBench("JSON String", 12.424, 31.997, 227.847, 224.412, ""),
    VariantFileBench(
        "plain Variant",
        10.620,
        43.774,
        30.471,
        2215.983,
        "文件少 14.5%，窄读快 7.48×，完整解码慢 9.87×",
    ),
    VariantFileBench(
        "shredded Variant",
        10.675,
        43.219,
        11.663,
        3506.071,
        "文件少 14.1%，窄读快 19.54×，完整解码慢 15.62×",
    ),
)
#: 测试样本规模与基线（逐字）。
FILE_BENCH_ROWS: int = 10_000
FILE_BENCH_FIELDS_PER_ROW: int = 200
FILE_BENCH_UNCOMPRESSED_JSON_KIB_PER_ROW: float = 4.16

#: Python 侧编码成本，单位 μs/row（a9 第 03 节正文，逐字）：
#: 「从已编码 plain Variant 再执行 Python shredding 需要 223.0 μs/row；加上宽对象
#: from_python 的约 223.5 μs/row 后，应用侧编码成本远高于约 20.8 μs/row 的 json.dumps。」
PYTHON_ENCODE_COST_US_PER_ROW: dict[str, float] = {
    "python_shredding": 223.0,
    "from_python_wide": 223.5,
    "json_dumps": 20.8,
}


@dataclass(frozen=True, slots=True)
class ShreddingAbBench:
    """plain vs shredded 的 A/B 读取对比（a9 第 04 节表）。

    测试条件逐字：两份文件写入完全相同的 20 万行 Variant，使用 ZSTD 和 16 MiB row group；
    每行包含一个 target INT 和若干整数路径，shredded 文件只物化 target；
    读取端只投影 $.target，逐行求和校验结果；预热后运行 3–5 次，取最好耗时。
    """

    paths_per_row: int
    plain_best_ms: float
    shredded_best_ms: float
    plain_read_mib: float
    shredded_read_mib: float
    speedup: float


SHREDDING_AB_BENCH: tuple[ShreddingAbBench, ...] = (
    ShreddingAbBench(8, 130.4, 10.8, 5.35, 0.50, 12.0),
    ShreddingAbBench(32, 144.0, 10.2, 22.45, 0.50, 14.2),
    ShreddingAbBench(64, 176.3, 10.8, 45.29, 0.50, 16.3),
    ShreddingAbBench(256, 388.1, 12.6, 186.26, 0.64, 30.8),
)
#: 64 路径样本的文件总大小与实际读取列（逐字）：plain 45.32 MiB / shredded 45.23 MiB；
#: plain 需要读取 45.284 MiB 的顶层 value，shredded 只需读取 0.003 MiB metadata、
#: 0.001 MiB fallback 和 0.490 MiB typed leaf。
SHREDDING_AB_FILE_SIZE_MIB: dict[str, float] = {"plain": 45.32, "shredded": 45.23}
SHREDDING_AB_READ_BYTES_MIB: dict[str, float] = {
    "plain_top_level_value": 45.284,
    "shredded_metadata": 0.003,
    "shredded_fallback": 0.001,
    "shredded_typed_leaf": 0.490,
}
SHREDDING_AB_ROWS: int = 200_000
SHREDDING_AB_ROW_GROUP_MIB: int = 16


@dataclass(frozen=True, slots=True)
class VariantReplaceBench:
    """variant_replace 路径级修改对比，单位 μs/row（a9 第 05 节表）。"""

    doc_cn: str
    json_parse_modify_dump_us: float
    variant_replace_us: float
    speedup: float


REPLACE_BENCH: tuple[VariantReplaceBench, ...] = (
    VariantReplaceBench("扁平 20 字段", 7.417, 0.201, 36.98),
    VariantReplaceBench("4 层嵌套小对象", 3.249, 0.146, 22.23),
    VariantReplaceBench("扁平 200 字段", 58.065, 2.046, 28.38),
)

#: CPU 回本点（a9 第 07 节，逐字）：
#: 20 字段对象：Variant 编码约 2.687 μs；每次路径查询比 JSON 少约 0.264 μs，
#:   纯 CPU 需要约 11 次读取才能摊平首次编码；
#: 200 字段对象：Variant 编码约 42.171 μs；每次路径查询少约 8.614 μs，约 5 次读取即可摊平；
#: 223 B 的小而深对象：Variant path get 本身比 JSON parse+get 慢约 14%，
#:   在这个微基准里不存在靠重复读取回本。
BREAKEVEN_READS: dict[str, float] = {
    "flat_20_fields": 11,
    "flat_200_fields": 5,
    "nested_small_223b": float("inf"),
}
PER_READ_SAVING_US: dict[str, float] = {
    "flat_20_fields": 0.264,
    "flat_200_fields": 8.614,
}
SMALL_DOC_VARIANT_SLOWDOWN_PCT: float = 14.0

#: 生产报告（a9 第 03 节「生产报告」）里的两组数字，逐字保留：
#: 电商：5 万条半结构化记录在旧链路中可能展开为 100 万行，每日近 100 GB 的 ETL 需要
#:       30–60 分钟；改为 VARIANT 后整条 pipeline 缩短到几分钟，ETL 代码减少 90%。
PRODUCTION_ECOMMERCE: dict[str, Any] = {
    "records": 50_000,
    "expanded_rows": 1_000_000,
    "daily_etl_gb": 100,
    "etl_minutes_before": (30, 60),
    "etl_code_reduction_pct": 90,
}
#: 智驾：遥测已经达到数千亿行，仍需近实时查看车辆状态与地理信息；动态内容进入单个
#:       VARIANT 列后，查询可保持亚秒级，迁移后计算与 BI 查询最高快 12×。
PRODUCTION_AUTOMOTIVE: dict[str, Any] = {
    "telemetry_scale_cn": "数千亿行",
    "latency_cn": "亚秒级",
    "max_speedup": 12,
}


def breakeven_reads(encode_cost_us: float, per_read_saving_us: float) -> float:
    """回本点：多少次读取才能摊平首次编码成本。

    a9 原文第 07 节的成本模型：
        总成本 = 写入次数 × 编码成本 + 查询次数 × 每次扫描、解析、抽取或重组成本

    保守地把 JSON String 写入解析成本视为 0，则回本点 = 编码成本 / 每次读取节省。
    用原文数字自检：2.687 / 0.264 ≈ 10.2 → 约 11 次读取；42.171 / 8.614 ≈ 4.9 → 约 5 次。

    :raises ValueError: 每次读取不省反亏（如 223 B 小对象慢 14%），此时无回本点
    """
    if per_read_saving_us <= 0:
        raise ValueError(
            "每次读取没有节省（例如 223 B 小而深对象 Variant 慢约 14%），"
            "靠重复读取回不了本；要靠 Parquet 压缩、列裁剪或统计量跳过带来额外收益"
        )
    import math

    return math.ceil(encode_cost_us / per_read_saving_us)


def estimate_total_cost(
    *, writes: int, reads: int, encode_cost_us: float, per_read_cost_us: float
) -> float:
    """按 a9 的成本模型估算总 CPU 成本（μs）。

    注意原文的免责声明逐字保留：这个回本计算没有包含文件 I/O、压缩、向量化、GC、网络和
    shredding 写放大，只能帮助理解趋势，不能替代集群 benchmark。
    """
    if writes < 0 or reads < 0:
        raise ValueError("写入次数与查询次数不能为负")
    return writes * encode_cost_us + reads * per_read_cost_us


# ---------------------------------------------------------------------- SQL / 运行时


def render_variant_get(column: str, path: str, sql_type: str, alias: str | None = None) -> str:
    """渲染 variant_get 表达式（Spark 4.0+ / Flink 2.1+ 语法，见 SQL_ENGINE_REQUIREMENT）。

    >>> render_variant_get("vector_meta", "$.perception.weather", "string", "weather_raw")
    "variant_get(vector_meta, '$.perception.weather', 'string') AS weather_raw"
    """
    expr = f"variant_get({column}, '{path}', '{sql_type.lower()}')"
    return f"{expr} AS {alias}" if alias else expr


def build_meta(
    *,
    perception: Mapping[str, Any] | None = None,
    scene: Mapping[str, Any] | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    model_debug: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按约定结构组装 vector_meta 的 Python 对象。

    ⚠️ 原文未明确，本项目设计：a9 只给出「哪类数据进 VARIANT」的边界，没有规定向量表
    元数据的对象结构。这里固定四个顶层键，和 VECTOR_META_HOT_PATHS 的热路径一一对应，
    保证 shredding schema 能命中。
    """
    return {
        "perception": dict(perception or {}),
        "scene": dict(scene or {}),
        "diagnostics": dict(diagnostics or {}),
        "model": dict(model_debug or {}),
    }


def variant_get_column(payload: Any, paths: Mapping[str, Any] | str, arrow_type: Any = None) -> Any:
    """批量按路径读取 Arrow Variant 列（PyPaimon ``variant_get`` 的薄封装）。

    a9 原文：「variant_get 直接处理 Arrow Array 或 ChunkedArray，不必先把每一行完整转换成
    Python dict」「多个路径共享一次路径规划和对象遍历」。

    :param payload: Arrow Variant 列（``struct<value: binary, metadata: binary>``）
    :param paths: 单个路径字符串（需同时给 arrow_type），或 {路径: Arrow 类型} 字典
    :param arrow_type: 单路径时的目标 Arrow 类型——原文强调「目标 Arrow 类型必须与 Variant
                       中的实际类型精确匹配」
    :raises RuntimeError: 未安装 pypaimon 时给出明确安装提示
    """
    variant_get = _import_pypaimon_fn("variant_get")
    if isinstance(paths, str):
        if arrow_type is None:
            raise ValueError("单路径读取必须显式给出 arrow_type（类型必须与实际类型精确匹配）")
        return variant_get(payload, paths, arrow_type)
    return variant_get(payload, dict(paths))


def variant_set_paths(payload: Any, updates: Mapping[str, Any]) -> Any:
    """在 Arrow Variant 列上按路径 upsert（PyPaimon ``variant_set`` 的薄封装）。

    这是向量流水线第 ④ 步「标签变图片不变不重算」的落地手段：标签变更只改
    vector_meta 里的对应路径，不触发 GPU 重新编码、不重写向量本体。
    a9 基准显示这比 json.loads → 改 dict → json.dumps 快 22.23×~36.98×（见 REPLACE_BENCH）。

    :param updates: {路径: 新值}，路径之间不能互为父子或重叠（原文约束）
    :raises ValueError: 路径互为前缀
    :raises RuntimeError: 未安装 pypaimon
    """
    paths = sorted(updates)
    for i, p in enumerate(paths):
        for q in paths[i + 1 :]:
            if q.startswith(p + "."):
                raise ValueError(f"路径之间不能互为父子或重叠: {p!r} 与 {q!r}")
    variant_set = _import_pypaimon_fn("variant_set")
    return variant_set(payload, dict(updates))


def _import_pypaimon_fn(name: str) -> Any:
    """延迟导入 PyPaimon 的 Variant 函数。没装库时给出可执行的修复建议，不炸在 import 期。"""
    try:
        module = __import__("pypaimon.data", fromlist=[name])
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            f"未安装 PyPaimon，无法使用 {name}；请执行 `pip install pypaimon`。"
            "PyPaimon 的 Variant 支持在 2026 年 4 月引入，批量路径操作与类型兼容性于 2026 年 8 月补齐"
        ) from exc
    try:
        return getattr(module, name)
    except AttributeError as exc:  # pragma: no cover
        raise RuntimeError(
            f"当前 PyPaimon 版本没有 {name}——variant_get / variant_replace / variant_set "
            "需要 2026 年 8 月之后的版本"
        ) from exc


def to_variant_array(rows: Sequence[Mapping[str, Any]]) -> Any:
    """把一批 Python 字典编码成 Arrow Variant 列（``GenericVariant`` 薄封装）。

    成本提醒（a9 逐字）：宽对象 from_python 约 223.5 μs/row，远高于 json.dumps 的
    约 20.8 μs/row——所以生产链路应优先批量读取热路径，而不应频繁把完整 Variant
    在 Python 对象与二进制之间往返。
    """
    try:
        module = __import__("pypaimon.data.generic_variant", fromlist=["GenericVariant"])
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 PyPaimon，无法编码 Variant；请执行 `pip install pypaimon`"
        ) from exc
    generic = module.GenericVariant
    _log.debug(
        "编码 %d 行 Variant（参考成本 %.1f μs/row）",
        len(rows),
        PYTHON_ENCODE_COST_US_PER_ROW["from_python_wide"],
    )
    return generic.to_arrow_array([generic.from_python(dict(r)) for r in rows])
