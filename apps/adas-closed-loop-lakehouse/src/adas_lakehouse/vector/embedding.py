"""Embedding 五步流水线：T+1 增量向量化，每日凌晨 6 点前完成。

来源：原文第三章《Embedding 五步流水线：向量是怎么算出来的》，五步逐字落地：

  ① 增量识别  按 create_time / update_time 水位，仅处理新增与标签变更图片
  ② 成本分级  高价值数据全量处理，普通数据按比例抽样，GPU 空闲时段分批
  ③ 双路编码  图片与 caption 经同一 CLIP 模型双塔编码，保证向量同空间
  ④ 幂等写回  按 (image_id, embedding_version) Upsert，重跑无副作用；标签变图片不变不重算
  ⑤ 索引刷新  写入完成后通知 StarRocks 增量刷新当日分区索引，新数据当日可检索

为什么是 T+1 而不是实时（原文原话）：向量化是 GPU 密集任务，批处理才能充分利用算力、
控制成本；而检索场景对新数据的时效要求本来就是「当日可检索」级别，T+1 恰好匹配。
实现上采用 Spark / Ray + GPU 算子，批次失败可断点续跑。

本模块只负责**编排**：编码器（CLIP）、写回 sink（Spark/Ray/Flink 作业）都以协议注入，
因此没有 GPU、没有 StarRocks 也能完整跑通一次 dry-run，并把每一步的口径断言出来。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ..ids import ArtifactStatus, derive_artifact_id, new_run_id
from .index import validate_partition_value
from .params import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_GPU_WINDOW_START_HOUR,
    DEFAULT_INDEX_REFRESH_STATUS,
    DEFAULT_NORMAL_SAMPLE_RATIO,
    INDEX_REFRESH_DONE,
    PIPELINE_DEADLINE_HOUR,
    PIPELINE_STEPS,
    SIMILARITY_METRIC_NAME,
)
from .schema import (
    CAPTION_COLUMN,
    EMBED_TIME_COLUMN,
    UPSERT_KEY,
    VECTOR_TABLE_NAME,
    VectorStatus,
    vector_column_names,
)
from .versioning import validate_embedding_version

__all__ = [
    "CostTier",
    "ImageRecord",
    "EmbeddingVectors",
    "ClipEncoder",
    "VectorSink",
    "RecordingSink",
    "Watermark",
    "BatchCheckpoint",
    "StepReport",
    "PipelineRunReport",
    "EmbeddingPipeline",
    "render_upsert_sql",
    "render_mark_refreshed_sql",
    "resume_key_for",
    "EMBEDDING_STAGE",
]

_log = logging.getLogger(__name__)

#: 产物阶段名，进 artifact_id 的第二段（ids.derive_artifact_id 的 stage 参数）。
EMBEDDING_STAGE: str = "embedding"


def _reject_unknown_columns(rows: Sequence[dict[str, Any]]) -> None:
    """写回前的列名自检：行里出现向量表没有的列，直接报错。

    列名对不上不会让写入报错，只会让那一列的值凭空消失、读出来永远是 NULL——
    要到真实 Paimon 上才暴露。列名以 catalog.registry 为准（唯一事实源）。
    """
    if not rows:
        return
    known = vector_column_names()
    unknown = sorted({k for row in rows for k in row} - known)
    if unknown:
        raise ValueError(
            f"待写回的行里有 {VECTOR_TABLE_NAME} 不存在的列 {unknown}；"
            "表结构的唯一事实源是 catalog/tables/_mining.py，缺列请在那里补，"
            "不要在子系统里另立一份定义"
        )


_ALGO_VERSION_RE = re.compile(r"(v[0-9][0-9a-z.]*)$")


class CostTier(str, Enum):
    """成本分级（原文第三章第 ② 步）。GPU 是稀缺资源，成本必须分级。"""

    #: 高价值数据全量处理
    HIGH_VALUE = "high_value"
    #: 普通数据按比例抽样
    NORMAL = "normal"


@dataclass(frozen=True, slots=True)
class ImageRecord:
    """一张待向量化的抽帧图片。

    :param image_id: 图片 ID，向量表业务主键
    :param data_id: 所属 clip 的一级 ID
    :param dt: 所属采集日分区（yyyy-MM-dd）
    :param image_uri: 图片对象存储地址，编码器据此取图
    :param caption: 图片描述文本，文本塔的编码输入
    :param image_content_hash: 图片内容哈希——「标签变图片不变不重算」的判定依据
    :param create_time: 图片记录创建时间，增量识别水位字段之一
    :param update_time: 图片记录更新时间（标签变更会刷新它），增量识别水位字段之二
    :param cost_tier: 成本分级
    :param parent_artifact_id: 血缘父产物（抽帧产物）
    :param scalars: 标量列取值（capture_time / geo_grid / weather 等，直接落表）
    :param meta: 半结构化元数据，落 vector_meta VARIANT（见 variant.build_meta）
    """

    image_id: str
    data_id: str
    dt: str
    image_uri: str = ""
    caption: str = ""
    image_content_hash: str = ""
    create_time: datetime | None = None
    update_time: datetime | None = None
    cost_tier: CostTier = CostTier.NORMAL
    parent_artifact_id: str = ""
    scalars: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """内容指纹：图片内容 + caption。用于判定是否需要重新编码。"""
        raw = f"{self.image_content_hash}|{self.caption}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class EmbeddingVectors:
    """双塔编码结果。图文向量必须来自同一个 CLIP 模型，否则不在同一空间。"""

    image_id: str
    image_embedding: tuple[float, ...]
    text_embedding: tuple[float, ...]

    def validate(self, dim: int) -> None:
        """维度自检：维度不对的向量写进去会让 HNSW 索引直接建不起来。"""
        if len(self.image_embedding) != dim:
            raise ValueError(
                f"image_embedding 维度 {len(self.image_embedding)} != 约定维度 {dim}（image_id={self.image_id}）"
            )
        if len(self.text_embedding) != dim:
            raise ValueError(
                f"text_embedding 维度 {len(self.text_embedding)} != 约定维度 {dim}（image_id={self.image_id}）"
            )


class ClipEncoder(Protocol):
    """CLIP 双塔编码器协议（原文第三章第 ③ 步）。

    实现方可以是 Spark / Ray 上的 GPU 算子，也可以是本地推理服务；本模块只要求
    图片塔与文本塔来自**同一个模型**，这样向量才在同一空间，文搜图才成立。
    """

    #: 该编码器对应的 embedding_version，会写进主键
    embedding_version: str
    #: 模型版本（公共键 model_version）
    model_version: str
    #: 模型名（registry 的 model_name 列：「Embedding 模型名（图文双塔同模型）」）。
    #: 可选——实现方没给时回落到 model_version，但 model_name 列不能留空：
    #: 出处字段为空的向量行，换代之后没人说得清它是哪个模型编的。
    model_name: str
    #: 输出维度
    dim: int

    def encode_images(self, uris: Sequence[str]) -> list[Sequence[float]]:
        """图片塔编码。返回顺序必须与入参一致。"""
        ...

    def encode_texts(self, texts: Sequence[str]) -> list[Sequence[float]]:
        """文本塔编码。返回顺序必须与入参一致。"""
        ...


class VectorSink(Protocol):
    """向量写回 sink（原文第三章第 ④ 步）。按 (image_id, embedding_version) Upsert。"""

    def upsert(self, rows: Sequence[dict[str, Any]]) -> int:
        """写入并返回成功行数。实现必须幂等——重跑同一批不得产生副作用。"""
        ...


@dataclass(slots=True)
class RecordingSink:
    """只收集行、不写库的 sink（dry-run / 单测用）。

    同时校验幂等键唯一性：同一批里出现重复的 (image_id, embedding_version) 直接报错，
    因为那意味着上游增量识别出了问题。
    """

    rows: list[dict[str, Any]] = field(default_factory=list)

    def upsert(self, rows: Sequence[dict[str, Any]]) -> int:
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            key = tuple(row.get(k) for k in UPSERT_KEY)
            if key in seen:
                raise ValueError(f"同一批出现重复幂等键 {UPSERT_KEY}={key}，增量识别有重复")
            seen.add(key)
        self.rows.extend(rows)
        return len(rows)


# ------------------------------------------------------------------- 步骤 ① 水位


@dataclass(slots=True)
class Watermark:
    """增量识别水位（原文第三章第 ① 步：按 create_time / update_time 水位）。

    持久化成一个小 JSON 文件，调度重启后接着跑。⚠️ 原文未明确，本项目设计：
    原文只说「按水位」，没规定水位存哪；这里用文件，生产可换成控制面表。
    """

    create_time_wm: datetime | None = None
    update_time_wm: datetime | None = None
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> Watermark:
        """从文件载入水位。文件不存在视为首次全量（两个水位都为 None）。"""
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"水位文件损坏: {p}（{exc}）；确认后删除即可重跑全量") from exc
        return cls(
            _parse_dt(raw.get("create_time_wm")),
            _parse_dt(raw.get("update_time_wm")),
            p,
        )

    def save(self) -> None:
        """落盘。没配 path 的水位是内存态，静默跳过。"""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "create_time_wm": _fmt_dt(self.create_time_wm),
                    "update_time_wm": _fmt_dt(self.update_time_wm),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def accepts(self, record: ImageRecord) -> bool:
        """判定一条记录是否落在水位之后（新增或标签变更）。"""
        if self.create_time_wm is None and self.update_time_wm is None:
            return True  # 首次全量
        newly_created = (
            record.create_time is not None
            and self.create_time_wm is not None
            and record.create_time > self.create_time_wm
        )
        newly_updated = (
            record.update_time is not None
            and self.update_time_wm is not None
            and record.update_time > self.update_time_wm
        )
        # 任一水位缺失时退化为「另一维度说了算」
        if self.create_time_wm is None:
            return newly_updated
        if self.update_time_wm is None:
            return newly_created
        return newly_created or newly_updated

    def advance(self, records: Iterable[ImageRecord]) -> None:
        """按本批最大时间推进水位。只前进不后退。"""
        for r in records:
            if r.create_time and (
                self.create_time_wm is None or r.create_time > self.create_time_wm
            ):
                self.create_time_wm = r.create_time
            if r.update_time and (
                self.update_time_wm is None or r.update_time > self.update_time_wm
            ):
                self.update_time_wm = r.update_time


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _fmt_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


# ----------------------------------------------------------------- 断点续跑检查点


def resume_key_for(dt: str, embedding_version: str) -> str:
    """断点续跑的作用域键 = (分区, embedding_version)。

    **不能用 run_id 当键**：run_id 里含运行时间戳（``ids.new_run_id``），重跑必然换一个，
    拿它当键的话检查点每次都作废、每次都从头重编——「批次失败可断点续跑」就名存实亡了。
    真正决定「这批活是不是同一批」的，是要写哪个分区、用哪个 embedding_version。
    """
    return f"{dt}|{embedding_version}"


@dataclass(slots=True)
class BatchCheckpoint:
    """批次检查点：原文「批次失败可断点续跑」的落地。

    记录已完成的批次序号，重跑时直接跳过。⚠️ 原文未明确，本项目设计：文件存储。

    两条语义很要命，都是本次审计补上的：

    1. **作用域键是 (dt, embedding_version)，不是 run_id**——见 :func:`resume_key_for`；
    2. **「完成」指的是写回成功，不是编码成功**——批次标记必须发生在 Upsert 之后。
       先标记后写回的话，「编完 0 号批次 → 崩在写回之前 → 重跑跳过 0 号批次」会让
       那一批向量永远丢失，而且流水线还会报成功。
    """

    resume_key: str
    done_batches: set[int] = field(default_factory=set)
    path: Path | None = None

    @classmethod
    def load(cls, resume_key: str, path: str | Path | None) -> BatchCheckpoint:
        if path is None:
            return cls(resume_key)
        p = Path(path)
        if not p.exists():
            return cls(resume_key, path=p)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _log.warning("检查点文件损坏，按全量重跑: %s", p)
            return cls(resume_key, path=p)
        if raw.get("resume_key") != resume_key:
            # 换了分区或换了 embedding_version，上一次的批次编号对不上，检查点作废
            return cls(resume_key, path=p)
        return cls(resume_key, set(raw.get("done_batches", [])), p)

    def mark_done(self, batch_no: int) -> None:
        """标记一个批次已**写回**完成。调用点必须在 Upsert 之后，不能在编码之后。"""
        self.done_batches.add(batch_no)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"resume_key": self.resume_key, "done_batches": sorted(self.done_batches)}
                ),
                encoding="utf-8",
            )

    def is_done(self, batch_no: int) -> bool:
        return batch_no in self.done_batches


# ----------------------------------------------------------------------- 运行报告


@dataclass(frozen=True, slots=True)
class StepReport:
    """一步的执行统计。"""

    ordinal: int
    name_cn: str
    input_count: int
    output_count: int
    elapsed_sec: float
    note: str = ""


@dataclass(frozen=True, slots=True)
class PipelineRunReport:
    """一次流水线运行的完整报告。"""

    run_id: str
    dt: str
    embedding_version: str
    steps: tuple[StepReport, ...]
    encoded_count: int
    meta_only_count: int
    skipped_count: int
    upserted_count: int
    refresh_statements: tuple[str, ...]
    finished_at: datetime
    #: 分区索引刷完后，把 index_refresh_status 从 pending 改成 done 的 Flink SQL。
    #: 由调度在 StarRocks 刷新成功之后下发——它是「新数据当日可检索」这句承诺的落表凭证。
    refresh_status_sql: str = ""

    @property
    def meets_deadline(self) -> bool:
        """是否赶在原文要求的「每日凌晨 6 点前」完成。"""
        return self.finished_at.hour < PIPELINE_DEADLINE_HOUR

    def summary(self) -> str:
        lines = [
            f"向量化运行 {self.run_id} | 分区 {self.dt} | 版本 {self.embedding_version}",
        ]
        for s in self.steps:
            lines.append(
                f"  ({s.ordinal}) {s.name_cn}: {s.input_count} -> {s.output_count} "
                f"耗时 {s.elapsed_sec:.3f}s {s.note}".rstrip()
            )
        lines.append(
            f"  GPU 编码 {self.encoded_count} 条 / 仅改元数据 {self.meta_only_count} 条 / "
            f"抽样跳过 {self.skipped_count} 条 / 写回 {self.upserted_count} 行"
        )
        lines.append(
            f"  完成于 {self.finished_at:%Y-%m-%d %H:%M:%S}，"
            f"{'满足' if self.meets_deadline else '未满足'} 凌晨 {PIPELINE_DEADLINE_HOUR} 点前的 T+1 截止"
        )
        return "\n".join(lines)


# ------------------------------------------------------------------------- 流水线


@dataclass(slots=True)
class EmbeddingPipeline:
    """五步向量化流水线的编排器。

    :param encoder: CLIP 双塔编码器
    :param sink: 向量写回 sink
    :param watermark: 增量水位
    :param index_service: 索引服务，第 ⑤ 步刷新当日分区索引；None 则只生成语句不下发
    :param sample_ratio: 普通数据抽样比例（⚠️ 原文未给比例，见 params）
    :param batch_size: GPU 批大小（⚠️ 原文只说「分批」）
    :param checkpoint_path: 断点续跑检查点文件
    :param algo_version: 写进 artifact_id 的算法版本；缺省从 embedding_version 末尾提取
    """

    encoder: ClipEncoder
    sink: VectorSink = field(default_factory=RecordingSink)
    watermark: Watermark = field(default_factory=Watermark)
    index_service: Any = None
    sample_ratio: float = DEFAULT_NORMAL_SAMPLE_RATIO
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE
    checkpoint_path: str | Path | None = None
    algo_version: str | None = None
    #: 已存在向量的指纹表 {image_id: fingerprint}，用于「标签变图片不变不重算」
    known_fingerprints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 < self.sample_ratio <= 1.0:
            raise ValueError(f"抽样比例必须落在 (0, 1]，收到 {self.sample_ratio}")
        if self.batch_size <= 0:
            raise ValueError(f"批大小必须为正整数，收到 {self.batch_size}")

    def model_name(self) -> str:
        """写进 model_name 列的取值：编码器给了就用它，没给回落到 model_version。

        registry 的 model_name 列注释是「Embedding 模型名（图文双塔同模型）」——
        图文两塔必须同模型才在同一空间，这一列就是这条约束的可审计凭证。
        """
        return str(getattr(self.encoder, "model_name", "") or self.encoder.model_version)

    # ---- ① 增量识别 ----

    def identify_increment(self, records: Iterable[ImageRecord]) -> list[ImageRecord]:
        """按 create_time / update_time 水位筛出新增与标签变更图片。"""
        return [r for r in records if self.watermark.accepts(r)]

    # ---- ② 成本分级 ----

    def apply_cost_tiering(
        self, records: Sequence[ImageRecord]
    ) -> tuple[list[ImageRecord], list[ImageRecord]]:
        """高价值全量、普通按比例抽样。

        抽样用 image_id 的哈希做**确定性**取样，而不是随机数——同一条数据无论重跑多少次
        都做同样的取舍，否则流水线就不幂等了。

        :return: (进入编码的记录, 被抽样丢弃的记录)
        """
        kept: list[ImageRecord] = []
        dropped: list[ImageRecord] = []
        threshold = int(self.sample_ratio * 10_000)
        for r in records:
            if r.cost_tier is CostTier.HIGH_VALUE:
                kept.append(r)
                continue
            bucket = int(hashlib.md5(r.image_id.encode("utf-8")).hexdigest()[:8], 16) % 10_000
            (kept if bucket < threshold else dropped).append(r)
        return kept, dropped

    def gpu_window_hint(self) -> tuple[int, int]:
        """GPU 空闲时段窗口 [start, deadline)。

        ⚠️ 原文未明确，本项目设计：原文只说「GPU 空闲时段分批」，未给时段；
        这里取 [DEFAULT_GPU_WINDOW_START_HOUR, PIPELINE_DEADLINE_HOUR)，
        右端点 6 点是原文给的硬截止。
        """
        return DEFAULT_GPU_WINDOW_START_HOUR, PIPELINE_DEADLINE_HOUR

    # ---- ③ 双路编码 ----

    def dual_encode(
        self,
        records: Sequence[ImageRecord],
        checkpoint: BatchCheckpoint,
        *,
        on_batch: Callable[[int, Sequence[ImageRecord], Sequence[EmbeddingVectors]], None]
        | None = None,
    ) -> list[EmbeddingVectors]:
        """图片与 caption 经同一 CLIP 模型双塔编码，分批 + 断点续跑。

        :param on_batch: 每批编码完成后的落库回调 ``(batch_no, 记录, 向量)``。
            **给了它，检查点才会在回调返回之后才标记**——也就是「写回成功才算这批做完」。
            不给的话本方法只编码不落库，批次仍会被标记，适用于调用方自己保证原子性的场景。
        :raises RuntimeError: 编码器返回条数与入参不一致（顺序/条数错位会污染整批向量）
        """
        out: list[EmbeddingVectors] = []
        for batch_no, start in enumerate(range(0, len(records), self.batch_size)):
            if checkpoint.is_done(batch_no):
                _log.info("批次 %d 已写回完成，断点续跑跳过", batch_no)
                continue
            batch = records[start : start + self.batch_size]
            img_vecs = self.encoder.encode_images([r.image_uri for r in batch])
            txt_vecs = self.encoder.encode_texts([r.caption for r in batch])
            if len(img_vecs) != len(batch) or len(txt_vecs) != len(batch):
                raise RuntimeError(
                    f"编码器返回条数不匹配：入参 {len(batch)}，图片 {len(img_vecs)}，文本 {len(txt_vecs)}"
                )
            batch_vectors: list[EmbeddingVectors] = []
            for rec, iv, tv in zip(batch, img_vecs, txt_vecs, strict=True):
                vectors = EmbeddingVectors(rec.image_id, tuple(iv), tuple(tv))
                vectors.validate(self.encoder.dim)
                batch_vectors.append(vectors)
            # 先落库、再标记：顺序反过来就会「跳过一个从没写进去的批次」
            if on_batch is not None:
                on_batch(batch_no, batch, batch_vectors)
            checkpoint.mark_done(batch_no)
            out.extend(batch_vectors)
        return out

    # ---- ④ 幂等写回 ----

    def split_unchanged(
        self, records: Sequence[ImageRecord]
    ) -> tuple[list[ImageRecord], list[ImageRecord]]:
        """「标签变图片不变不重算」：按内容指纹分流。

        :return: (需要 GPU 重新编码的, 只需更新元数据的)

        只需更新元数据的那部分走 variant.variant_set_paths 做路径级修改——
        a9 基准显示比「整个 JSON 解析→改→回写」快 22.23×~36.98×，更重要的是完全不占 GPU。
        """
        to_encode: list[ImageRecord] = []
        meta_only: list[ImageRecord] = []
        for r in records:
            known = self.known_fingerprints.get(r.image_id)
            if known is not None and known == r.fingerprint():
                meta_only.append(r)
            else:
                to_encode.append(r)
        return to_encode, meta_only

    def build_rows(
        self,
        records: Sequence[ImageRecord],
        vectors: Sequence[EmbeddingVectors],
        *,
        run_id: str,
        encoded_at: datetime,
    ) -> list[dict[str, Any]]:
        """组装待 Upsert 的行。artifact_id 由「输入 + 算法版本 + 内容」派生，天然幂等。"""
        by_id = {v.image_id: v for v in vectors}
        algo = self.algo_version or _extract_algo_version(self.encoder.embedding_version)
        rows: list[dict[str, Any]] = []
        for r in records:
            vec = by_id.get(r.image_id)
            if vec is None:
                continue
            artifact_id = derive_artifact_id(
                r.data_id,
                EMBEDDING_STAGE,
                algo,
                f"{r.image_id}|{r.fingerprint()}|{self.encoder.embedding_version}",
            )
            row: dict[str, Any] = {
                "image_id": r.image_id,
                "embedding_version": self.encoder.embedding_version,
                "dt": r.dt,
                "data_id": r.data_id,
                "artifact_id": str(artifact_id),
                "parent_artifact_id": r.parent_artifact_id,
                "run_id": run_id,
                "artifact_status": ArtifactStatus.ACTIVE.value,
                "model_version": self.encoder.model_version,
                # 出处字段：向量行必须自己说清「谁编的、用什么度量、索引刷没刷」，
                # 否则换代 / 降级 / 混存一出问题就只能靠猜（这三列 registry 早就定义了，
                # 之前没人写 → 永远是 NULL，属于典型的静默失效）
                "model_name": self.model_name(),
                "similarity_metric": SIMILARITY_METRIC_NAME,
                "index_refresh_status": DEFAULT_INDEX_REFRESH_STATUS,
                "image_embedding": list(vec.image_embedding),
                "text_embedding": list(vec.text_embedding),
                "embedding_dim": self.encoder.dim,
                # 列名以 registry 为准：向量侧的 caption / encoded_at 在表里叫
                # caption_text / embed_time（见 schema.CAPTION_COLUMN / EMBED_TIME_COLUMN）
                CAPTION_COLUMN: r.caption,
                "vector_status": VectorStatus.ACTIVE.value,
                "cost_tier": r.cost_tier.value,
                EMBED_TIME_COLUMN: encoded_at,
                "vector_meta": r.meta,
            }
            row.update(r.scalars)
            rows.append(row)
        _reject_unknown_columns(rows)
        return rows

    def build_meta_only_rows(self, records: Sequence[ImageRecord]) -> list[dict[str, Any]]:
        """只更新元数据的行：不带向量列，走 Paimon 部分列更新，不占 GPU。"""
        rows = [
            {
                "image_id": r.image_id,
                "embedding_version": self.encoder.embedding_version,
                "dt": r.dt,
                "vector_meta": r.meta,
                **r.scalars,
            }
            for r in records
        ]
        _reject_unknown_columns(rows)
        return rows

    # ---- ⑤ 索引刷新 ----

    def refresh_index(self, dt: str) -> tuple[str, ...]:
        """通知 StarRocks 增量刷新当日分区索引，新数据当日可检索。"""
        from .index import render_partition_refresh_sql

        if self.index_service is None:
            return render_partition_refresh_sql(dt)
        return tuple(self.index_service.refresh_partition(dt))

    # ---- 编排 ----

    def run(
        self,
        dt: str,
        records: Iterable[ImageRecord],
        *,
        now: datetime | None = None,
    ) -> PipelineRunReport:
        """跑完五步，返回运行报告。

        :param dt: 目标分区（yyyy-MM-dd），决定第 ⑤ 步刷哪个分区的索引
        :param records: 候选图片（上游按 dt 捞出的当日 + 标签变更集合）
        :raises ValueError: 记录的 dt 与目标分区不一致——跨分区批次会把索引刷错
        """
        import time

        started = now or datetime.now()
        run = new_run_id(EMBEDDING_STAGE, started)
        run_id = str(run)
        # 断点续跑的作用域是 (分区, embedding_version)，不是 run_id——run_id 每次重跑都变
        checkpoint = BatchCheckpoint.load(
            resume_key_for(dt, self.encoder.embedding_version), self.checkpoint_path
        )
        steps: list[StepReport] = []
        all_records = list(records)
        for r in all_records:
            if r.dt != dt:
                raise ValueError(f"记录 {r.image_id} 的 dt={r.dt} 与目标分区 {dt} 不一致")

        # ① 增量识别
        t0 = time.perf_counter()
        incremental = self.identify_increment(all_records)
        steps.append(
            StepReport(
                1,
                PIPELINE_STEPS[0].name_cn,
                len(all_records),
                len(incremental),
                time.perf_counter() - t0,
            )
        )

        # ② 成本分级
        t0 = time.perf_counter()
        kept, dropped = self.apply_cost_tiering(incremental)
        win = self.gpu_window_hint()
        steps.append(
            StepReport(
                2,
                PIPELINE_STEPS[1].name_cn,
                len(incremental),
                len(kept),
                time.perf_counter() - t0,
                f"抽样丢弃 {len(dropped)} 条，抽样比例 {self.sample_ratio}，GPU 窗口 [{win[0]}:00, {win[1]}:00)",
            )
        )

        # ③ 双路编码 + ④ 幂等写回：**逐批写回**。
        # 编完一批就写一批、写成功才标记检查点，这样中途崩了重跑不会丢批
        # （先全量编码、最后统一写回的话，崩在写回之前那些批次会被检查点永久跳过）。
        to_encode, meta_only = self.split_unchanged(kept)
        write_seconds = 0.0
        written = 0
        row_count = 0

        def _persist(
            _batch_no: int, batch: Sequence[ImageRecord], vecs: Sequence[EmbeddingVectors]
        ) -> None:
            nonlocal write_seconds, written, row_count
            w0 = time.perf_counter()
            batch_rows = self.build_rows(batch, vecs, run_id=run_id, encoded_at=started)
            if batch_rows:
                written += self.sink.upsert(batch_rows)
                row_count += len(batch_rows)
            write_seconds += time.perf_counter() - w0

        t0 = time.perf_counter()
        vectors = self.dual_encode(to_encode, checkpoint, on_batch=_persist)
        steps.append(
            StepReport(
                3,
                PIPELINE_STEPS[2].name_cn,
                len(to_encode),
                len(vectors),
                max(time.perf_counter() - t0 - write_seconds, 0.0),
                f"跳过重算 {len(meta_only)} 条（标签变图片不变）",
            )
        )

        # ④' 只改元数据的那部分：不带向量列，走 Paimon 部分列更新，不占 GPU
        t0 = time.perf_counter()
        meta_rows = self.build_meta_only_rows(meta_only)
        if meta_rows:
            written += self.sink.upsert(meta_rows)
            row_count += len(meta_rows)
        write_seconds += time.perf_counter() - t0
        steps.append(
            StepReport(
                4,
                PIPELINE_STEPS[3].name_cn,
                row_count,
                written,
                write_seconds,
                f"幂等键 ({', '.join(UPSERT_KEY)})，逐批写回后才标记检查点",
            )
        )

        # ⑤ 索引刷新
        t0 = time.perf_counter()
        refresh = self.refresh_index(dt)
        status_sql = render_mark_refreshed_sql(
            dt=dt, embedding_version=self.encoder.embedding_version
        )
        steps.append(
            StepReport(
                5,
                PIPELINE_STEPS[4].name_cn,
                1,
                len(refresh),
                time.perf_counter() - t0,
                f"分区 {dt}，刷完回写 index_refresh_status=done",
            )
        )

        self.watermark.advance(incremental)
        self.watermark.save()
        for r in to_encode:
            self.known_fingerprints[r.image_id] = r.fingerprint()

        finished = datetime.now() if now is None else now
        report = PipelineRunReport(
            run_id=run_id,
            dt=dt,
            embedding_version=self.encoder.embedding_version,
            steps=tuple(steps),
            encoded_count=len(vectors),
            meta_only_count=len(meta_only),
            skipped_count=len(dropped),
            upserted_count=written,
            refresh_statements=tuple(refresh),
            finished_at=finished,
            refresh_status_sql=status_sql,
        )
        if not report.meets_deadline:
            _log.warning(
                "向量化在 %s 完成，晚于原文要求的每日凌晨 %d 点前，当日可检索承诺有风险",
                finished,
                PIPELINE_DEADLINE_HOUR,
            )
        return report


def _extract_algo_version(embedding_version: str) -> str:
    """从 embedding_version 里提取 ids 要求的算法版本段（形如 v2 / v4.1）。

    ids.derive_artifact_id 要求 algo_version 以 ``v`` 开头，而 embedding_version 习惯
    写成 ``clip_v2``——这里做一次转换，取不到就直接报错，不猜。
    """
    m = _ALGO_VERSION_RE.search(embedding_version)
    if not m:
        raise ValueError(
            f"无法从 embedding_version={embedding_version!r} 提取算法版本（需形如 clip_v2 / v4.1）；"
            "请在 EmbeddingPipeline(algo_version=...) 显式指定"
        )
    return m.group(1)


def render_upsert_sql(
    *, embedding_version: str, dt: str, source_table: str, table: str = VECTOR_TABLE_NAME
) -> str:
    """渲染幂等写回的 Flink SQL（按 (image_id, embedding_version) Upsert）。

    Paimon 主键表的 INSERT INTO 即 Upsert：主键相同则覆盖，重跑无副作用。
    这里给出的是「向量已由 Spark / Ray GPU 算子算好、落在临时表 source_table」之后的
    最后一跳；GPU 编码本身不在 Flink 里做。
    """
    validate_embedding_version(embedding_version)
    validate_partition_value(dt)
    return (
        f"-- 幂等写回：按 ({', '.join(UPSERT_KEY)}) Upsert，重跑无副作用（原文第三章第 ④ 步）\n"
        f"INSERT INTO `{table}`\n"
        f"SELECT * FROM `{source_table}`\n"
        f"WHERE `embedding_version` = '{embedding_version}' AND `dt` = '{dt}';\n"
    )


def render_mark_refreshed_sql(
    *, dt: str, embedding_version: str, table: str = VECTOR_TABLE_NAME
) -> str:
    """把当日分区的 index_refresh_status 从 pending 改成 done（第 ⑤ 步刷完之后）。

    registry 的 ``index_refresh_status`` 列注释是「当日分区索引刷新状态：
    pending/refreshing/done」。这一列以前没人写，永远是 NULL——于是「新数据当日可检索」
    这条承诺在表上没有任何凭证，出事时分不清是没向量化还是索引没刷。

    只改一列，走 Paimon 主键表的部分列更新，向量本体不动（和 versioning 里改
    vector_status 是同一个手法）。

    ⚠️ 原文未明确，本项目设计：原文第三章第 ⑤ 步只说「写入完成后通知 StarRocks 增量刷新
    当日分区索引」，没规定刷新状态怎么落表。
    """
    validate_embedding_version(embedding_version)
    validate_partition_value(dt)
    return (
        f"-- 第 ⑤ 步收尾：分区 {dt} 索引已刷完，回写 index_refresh_status=done\n"
        f"UPDATE `{table}` SET `index_refresh_status` = '{INDEX_REFRESH_DONE}'\n"
        f"WHERE `dt` = '{dt}' AND `embedding_version` = '{embedding_version}';\n"
    )
