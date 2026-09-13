"""统一标签服务：三源收口管道（字典映射 → 幂等去重 → 血缘填充）。

原文第二章：
    「字典只是静态的词表，真正防爆炸靠的是写入路径的收口。
      三来源标签一律经统一标签服务写入，没有旁路。管道三步：
      ① 字典映射 ② 幂等去重 ③ 血缘填充」
    「管道出口是两张标签事实表：dwd_mining_data_tag_detail（clip 级）与
      dwd_mining_image_tag_detail（image 级）」
    「VLM 生成的关键说明（caption）以 tag_category=CAPTION 的特殊标签写入图片标签表，
      与结构化标签同条记录口径并存，同时冗余一份到向量表——
      结构化过滤和语义检索用同一份说明，不用两套维护」

本模块是「没有旁路」这句话的代码化：任何来源想写标签，只有 :class:`UnifiedTagService`
一个入口，三步一步都绕不过去。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..config import settings
from ..ids import ArtifactStatus, derive_artifact_id, new_run_id
from .constants import (
    DATA_TAG_TABLE,
    DICT_TABLE,
    IMAGE_TAG_TABLE,
    PIPELINE_STEPS,
    VECTOR_TABLE,
)
from .dedup import ConflictDecision, ConflictResolver, DedupStats, IdempotentBuffer
from .dictionary import TagCategory, TagDictionary, TagLevel, TagStatus, default_dictionary
from .lifecycle import CandidatePool, TagLifecycleManager
from .mapping import MappingOutcome, MappingResult, TagMapper
from .records import DataTagRecord, ImageTagRecord, RawTag, ReviewStatus, TagRecord
from .sources import TagSource, profile_for

__all__ = [
    "TagWriter",
    "InMemoryTagWriter",
    "FlinkSqlTagWriter",
    "PipelineResult",
    "UnifiedTagService",
    "TAG_STAGE",
]

#: 标签环节在三级 ID 里的 stage 段：artifact_id = {data_id}_tag_{algo_version}_{hash}
TAG_STAGE = "tag"


# --------------------------------------------------------------------------- 写出口


@runtime_checkable
class TagWriter(Protocol):
    """标签落库出口。实现方负责真正的 Paimon Upsert。"""

    def write(self, table: str, rows: list[dict[str, Any]]) -> int:
        """写入若干行，返回写入行数。"""
        ...


class InMemoryTagWriter:
    """内存出口：默认实现，供干跑、单测与批量核对使用（不依赖任何外部客户端）。"""

    __slots__ = ("buffers",)

    def __init__(self) -> None:
        self.buffers: dict[str, list[dict[str, Any]]] = {}

    def write(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        self.buffers.setdefault(table, []).extend(rows)
        return len(rows)

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.buffers.get(table, [])

    def clear(self) -> None:
        self.buffers.clear()


class FlinkSqlTagWriter:
    """Flink SQL Gateway 出口：把行渲染成 INSERT 语句并提交。

    连接信息一律取自 :func:`adas_lakehouse.config.settings`，不在本模块硬编码。
    ``requests`` 未安装时**不影响 import 本模块**——只有真正提交时才会报错，
    这样全量 import 检查与离线渲染都能跑。
    """

    __slots__ = ("_catalog", "_database", "_gateway", "_dry_run", "statements")

    def __init__(self, *, dry_run: bool = True) -> None:
        cfg = settings()
        self._catalog = cfg.paimon.catalog
        self._database = cfg.paimon.database
        self._gateway = cfg.flink.sql_gateway_url
        self._dry_run = dry_run
        self.statements: list[str] = []

    # ---- 渲染 ----

    @staticmethod
    def _literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, datetime):
            return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
        return "'" + str(value).replace("'", "''") + "'"

    def render(self, table: str, rows: list[dict[str, Any]]) -> str:
        """渲染成一条 INSERT INTO ... VALUES 语句（Paimon 主键表天然 Upsert）。"""
        if not rows:
            return ""
        cols = list(rows[0].keys())
        values = ",\n  ".join(
            "(" + ", ".join(self._literal(row.get(c)) for c in cols) + ")" for row in rows
        )
        col_sql = ", ".join(f"`{c}`" for c in cols)
        return (
            f"INSERT INTO `{self._catalog}`.`{self._database}`.`{table}` ({col_sql})\nVALUES\n  "
            + values
            + ";"
        )

    # ---- 提交 ----

    def write(self, table: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        stmt = self.render(table, rows)
        self.statements.append(stmt)
        if self._dry_run:
            return len(rows)
        self._submit(stmt)
        return len(rows)

    def _submit(self, statement: str) -> None:
        """提交到 Flink SQL Gateway。

        :raises RuntimeError: 缺少 requests 依赖，或网关返回非 2xx
        """
        try:
            import requests  # 延迟 import：客户端库缺失不应让本模块 import 失败
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise RuntimeError(
                "提交 Flink SQL 需要 requests（pip install requests）；"
                "只想渲染语句请用 FlinkSqlTagWriter(dry_run=True)"
            ) from exc
        try:
            resp = requests.post(
                f"{self._gateway}/v1/sessions/adas-tags/statements",
                json={"statement": statement},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as exc:  # pragma: no cover - 网络异常
            raise RuntimeError(f"Flink SQL Gateway 提交失败：{self._gateway}") from exc


# --------------------------------------------------------------------------- 结果


@dataclass(slots=True)
class PipelineResult:
    """一次管道执行的全部产出与留痕。"""

    run_id: str
    data_tags: list[DataTagRecord] = field(default_factory=list)
    image_tags: list[ImageTagRecord] = field(default_factory=list)
    rejected: list[MappingResult] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    conflicts: list[ConflictDecision] = field(default_factory=list)
    dedup_stats: DedupStats = field(default_factory=DedupStats)
    lineage_errors: list[str] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.data_tags) + len(self.image_tags)

    def summary(self) -> dict[str, Any]:
        """管道三步的执行摘要，可直接打日志。"""
        return {
            "run_id": self.run_id,
            "steps": list(PIPELINE_STEPS),
            "accepted": self.accepted_count,
            "data_tags": len(self.data_tags),
            "image_tags": len(self.image_tags),
            "rejected": len(self.rejected),
            "candidates": len(self.candidates),
            "conflicts": len(self.conflicts),
            "lineage_errors": len(self.lineage_errors),
            **self.dedup_stats.as_dict(),
        }


# --------------------------------------------------------------------------- 服务


class UnifiedTagService:
    """统一标签服务——三来源写标签的唯一入口，没有旁路。

    :param dictionary: 受控词表
    :param writer: 落库出口，默认内存出口（干跑）
    :param algo_version: 本次打标的算法版本，参与 artifact_id 生成
    :param strict_ids: True 时 data_id 不合法直接抛错；False 时降级为「不生成
        artifact_id 并记 lineage_errors」，便于存量回补
    """

    __slots__ = (
        "_dict",
        "_mapper",
        "_resolver",
        "_lifecycle",
        "_writer",
        "_algo_version",
        "_strict",
    )

    def __init__(
        self,
        dictionary: TagDictionary | None = None,
        *,
        writer: TagWriter | None = None,
        mapper: TagMapper | None = None,
        lifecycle: TagLifecycleManager | None = None,
        algo_version: str = "v1",
        strict_ids: bool = False,
    ) -> None:
        self._dict = dictionary if dictionary is not None else default_dictionary()
        self._mapper = mapper if mapper is not None else TagMapper(self._dict)
        self._resolver = ConflictResolver(self._dict)
        self._lifecycle = lifecycle if lifecycle is not None else TagLifecycleManager(self._dict)
        self._writer: TagWriter = writer if writer is not None else InMemoryTagWriter()
        self._algo_version = self._normalize_algo_version(algo_version)
        self._strict = strict_ids

    # ---- 属性 ----

    @property
    def dictionary(self) -> TagDictionary:
        return self._dict

    @property
    def candidate_pool(self) -> CandidatePool:
        """候选池：管道第一步拦下来的未匹配写法都在这里等审核。"""
        return self._lifecycle.pool

    @property
    def lifecycle(self) -> TagLifecycleManager:
        return self._lifecycle

    @property
    def writer(self) -> TagWriter:
        return self._writer

    @staticmethod
    def _normalize_algo_version(version: str) -> str:
        """算法版本归一成 ids 要求的 ``v*`` 形态。

        ⚠️ 原文未明确，本项目设计：模型版本写法五花八门（``1.2``/``v1.2``），
        这里统一补 ``v`` 前缀，避免 derive_artifact_id 直接抛错。
        """
        version = (version or "").strip()
        if not version:
            raise ValueError("algo_version 不能为空——artifact_id 需要算法版本段")
        return version if version.startswith("v") else f"v{version}"

    # ---- 主流程 ----

    def ingest(
        self,
        raw_tags: Iterable[RawTag],
        *,
        run_id: str | None = None,
        started_at: datetime | None = None,
    ) -> PipelineResult:
        """三源收口管道主入口：一批原始标签进，两张事实表的行出。

        :param raw_tags: 任意来源的原始标签
        :param run_id: 三级 ID 之处理运行 ID；不给则自动生成 ``run_tag_*``
        :param started_at: 打标时间，落到 ``first_tag_time``
        :return: :class:`PipelineResult`
        """
        rid = run_id or str(new_run_id(TAG_STAGE, started_at))
        moment = started_at or datetime.now()
        result = PipelineResult(run_id=rid)
        buffer = IdempotentBuffer()

        for raw in raw_tags:
            # ---- ① 字典映射 ----
            mapping = self._mapper.map(
                raw.raw_tag,
                raw.source,
                confidence=raw.confidence,
                expected_category=raw.expected_category,
            )
            if not mapping.accepted:
                result.rejected.append(mapping)
                if mapping.outcome is MappingOutcome.CANDIDATE:
                    self._lifecycle.pool.offer(
                        raw.raw_tag,
                        raw.source,
                        data_id=raw.data_id,
                        category_hint=raw.expected_category,
                        seen_at=moment,
                    )
                    result.candidates.append(mapping.normalized)
                continue

            # ---- ③ 血缘填充（先于去重构造记录，缺血缘的直接拦下）----
            record = self._build_record(raw, mapping, rid, moment, result)
            if record is None:
                continue

            # ---- ② 幂等去重 ----
            buffer.upsert(record)

        # 互斥冲突按实体逐个裁决
        for _entity, records in buffer.by_entity().items():
            result.conflicts.extend(self._resolver.resolve(records))

        result.dedup_stats = buffer.stats
        result.dedup_stats.conflict_resolved = len(result.conflicts)
        result.dedup_stats.invalidated = sum(1 for r in buffer if not r.valid_flag)

        for rec in buffer.records():
            if isinstance(rec, ImageTagRecord):
                result.image_tags.append(rec)
            elif isinstance(rec, DataTagRecord):
                result.data_tags.append(rec)
        return result

    def _build_record(
        self,
        raw: RawTag,
        mapping: MappingResult,
        run_id: str,
        moment: datetime,
        result: PipelineResult,
    ) -> TagRecord | None:
        """管道第三步：血缘填充，顺带做该来源的必填血缘校验。"""
        entry = mapping.entry
        assert entry is not None and mapping.tag_id is not None

        missing = raw.missing_lineage_fields()
        if missing:
            msg = (
                f"{raw.data_id}/{mapping.tag_id}: 来源 {raw.source.value} 缺必填血缘字段 "
                f"{list(missing)}（原文③：每条标签携带 tag_source、rule_id / model_name / "
                f"model_version、confidence、infer_job_id）"
            )
            result.lineage_errors.append(msg)
            result.rejected.append(
                MappingResult(
                    raw_tag=raw.raw_tag,
                    normalized=mapping.normalized,
                    outcome=MappingOutcome.SOURCE_NOT_ALLOWED,
                    tag_id=mapping.tag_id,
                    entry=entry,
                    reason=msg,
                )
            )
            return None

        artifact_id = self._derive_artifact_id(raw, mapping.tag_id, result)
        profile = profile_for(raw.source)
        # ⚠️ 原文未明确，本项目设计：只有「需要人工审核」的来源（模型标签）落 pending，
        # 采集/规则标签口径确定，落 approved 直接可用；原文只对模型标签强调了审核。
        review_status = (
            ReviewStatus.PENDING if profile.needs_human_review else ReviewStatus.APPROVED
        )

        common: dict[str, Any] = {
            "tag_id": mapping.tag_id,
            "tag_name": entry.tag_name,
            "tag_category": entry.category,
            "tag_source": raw.source,
            "tag_level": entry.tag_level,
            "confidence": raw.confidence,
            "rule_id": raw.rule_id,
            "rule_version": raw.rule_version,
            "model_name": raw.model_name,
            "model_version": raw.model_version,
            "infer_job_id": raw.infer_job_id,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "parent_artifact_id": raw.parent_artifact_id,
            "artifact_status": ArtifactStatus.ACTIVE,
            "review_status": review_status,
            "source_raw_tag": raw.raw_tag,
            "mapping_type": mapping.outcome.value,
            "mapping_note": mapping.reason,
            "project_code": raw.project_code,
            "vehicle_code": raw.vehicle_code,
            "first_tag_time": raw.tag_time or moment,
        }

        if raw.is_image_level:
            assert raw.image_id is not None
            return ImageTagRecord(
                image_id=raw.image_id,
                data_id=raw.data_id,
                caption_text=raw.caption_text,
                inherited_from_data_tag=False,
                **common,
            )
        return DataTagRecord(data_id=raw.data_id, **common)

    def _derive_artifact_id(self, raw: RawTag, tag_id: str, result: PipelineResult) -> str | None:
        """派生二级 ID。内容哈希取「data_id|image_id|tag_id|来源|原始写法」，重跑幂等。"""
        payload = f"{raw.data_id}|{raw.image_id or ''}|{tag_id}|{raw.source.value}|{raw.raw_tag}"
        try:
            return str(derive_artifact_id(raw.data_id, TAG_STAGE, self._algo_version, payload))
        except ValueError as exc:
            if self._strict:
                raise
            result.lineage_errors.append(
                f"{raw.data_id}: artifact_id 生成失败（{exc}），本条只落 run_id 血缘"
            )
            return None

    # ---- 标签继承 ----

    def inherit_to_images(
        self,
        data_tags: list[DataTagRecord],
        image_ids_by_data_id: dict[str, list[str]],
    ) -> list[ImageTagRecord]:
        """clip 标签自动继承到它抽出来的每一张图片。

        原文第一章：「适用粒度（tag_level 区分 clip 级 / image 级 / 可继承——
        clip 标签可自动继承到它抽出来的每一张图片）」。
        只有 ``tag_level=inheritable`` 的标签会继承；clip 级专属标签（如「驾驶员接管」）
        留在 clip 表，不往图片表灌。

        :param data_tags: clip 级标签记录
        :param image_ids_by_data_id: 抽帧产物，{data_id: [image_id, ...]}
        :return: 继承出来的 image 级记录（``inherited_from_data_tag=True``）
        """
        inheritable = self._dict.inheritable_tag_ids()
        out: list[ImageTagRecord] = []
        for rec in data_tags:
            if rec.tag_id not in inheritable or not rec.valid_flag:
                continue
            for image_id in image_ids_by_data_id.get(rec.data_id, ()):
                out.append(
                    ImageTagRecord(
                        image_id=image_id,
                        data_id=rec.data_id,
                        inherited_from_data_tag=True,
                        tag_id=rec.tag_id,
                        tag_name=rec.tag_name,
                        tag_category=rec.tag_category,
                        tag_source=rec.tag_source,
                        tag_level=TagLevel.INHERITABLE,
                        confidence=rec.confidence,
                        rule_id=rec.rule_id,
                        rule_version=rec.rule_version,
                        model_name=rec.model_name,
                        model_version=rec.model_version,
                        infer_job_id=rec.infer_job_id,
                        run_id=rec.run_id,
                        artifact_id=rec.artifact_id,
                        parent_artifact_id=rec.parent_artifact_id,
                        artifact_status=rec.artifact_status,
                        review_status=rec.review_status,
                        review_operator=rec.review_operator,
                        review_time=rec.review_time,
                        source_raw_tag=rec.source_raw_tag,
                        mapping_type=rec.mapping_type,
                        mapping_note="继承自 clip 标签（tag_level=inheritable）",
                        project_code=rec.project_code,
                        vehicle_code=rec.vehicle_code,
                        first_tag_time=rec.first_tag_time,
                    )
                )
        return out

    # ---- caption ----

    def ingest_caption(
        self,
        image_id: str,
        data_id: str,
        caption_text: str,
        *,
        model_name: str,
        model_version: str,
        confidence: float | None = None,
        infer_job_id: str | None = None,
        run_id: str | None = None,
        project_code: str = "",
        vehicle_code: str = "",
        tag_time: datetime | None = None,
    ) -> ImageTagRecord:
        """把 VLM 的关键说明写成 tag_category=CAPTION 的特殊标签。

        原文：「VLM 生成的关键说明（caption）以 tag_category=CAPTION 的特殊标签
        写入图片标签表，与结构化标签同条记录口径并存，同时冗余一份到向量表——
        结构化过滤和语义检索用同一份说明，不用两套维护」。

        :raises ValueError: caption 正文为空
        """
        if not (caption_text or "").strip():
            raise ValueError("caption 正文不能为空")
        rid = run_id or str(new_run_id(TAG_STAGE))
        moment = tag_time or datetime.now()
        entry = self._dict.require(TagCategory.CAPTION.value)
        payload = f"{data_id}|{image_id}|CAPTION|{caption_text}"
        try:
            artifact_id: str | None = str(
                derive_artifact_id(data_id, TAG_STAGE, self._algo_version, payload)
            )
        except ValueError:
            if self._strict:
                raise
            artifact_id = None
        return ImageTagRecord(
            image_id=image_id,
            data_id=data_id,
            caption_text=caption_text,
            tag_id=entry.tag_id,
            tag_name=entry.tag_name,
            tag_category=TagCategory.CAPTION,
            tag_source=TagSource.MODEL,
            tag_level=TagLevel.IMAGE,
            confidence=confidence,
            model_name=model_name,
            model_version=model_version,
            infer_job_id=infer_job_id,
            run_id=rid,
            artifact_id=artifact_id,
            # 模型产出永远先过审再上岗
            review_status=ReviewStatus.PENDING,
            source_raw_tag=caption_text,
            mapping_type=MappingOutcome.CANONICAL.value,
            mapping_note="CAPTION 特殊类别：不走受控词表匹配，正文原样落库",
            project_code=project_code,
            vehicle_code=vehicle_code,
            first_tag_time=moment,
        )

    @staticmethod
    def caption_vector_row(record: ImageTagRecord) -> dict[str, Any]:
        """caption 冗余到向量表的那一行（结构化过滤与语义检索共用一份说明）。

        :raises ValueError: 传入的不是 CAPTION 记录
        """
        if record.tag_category is not TagCategory.CAPTION:
            raise ValueError("只有 tag_category=CAPTION 的记录才需要冗余到向量表")
        return {
            "image_id": record.image_id,
            "data_id": record.data_id,
            "caption_text": record.caption_text,
            "model_name": record.model_name,
            "model_version": record.model_version,
            "artifact_id": record.artifact_id,
            "run_id": record.run_id,
            "update_time": record.first_tag_time,
        }

    # ---- 落库 ----

    def flush(
        self, result: PipelineResult, *, write_captions_to_vector: bool = True
    ) -> dict[str, int]:
        """把管道产出写到出口，返回各表写入行数。

        写入顺序：字典表（若有变更）→ clip 标签表 → image 标签表 → 向量表 caption 冗余。
        """
        written: dict[str, int] = {}
        if result.data_tags:
            written[DATA_TAG_TABLE] = self._writer.write(
                DATA_TAG_TABLE, [r.to_row() for r in result.data_tags]
            )
        if result.image_tags:
            written[IMAGE_TAG_TABLE] = self._writer.write(
                IMAGE_TAG_TABLE, [r.to_row() for r in result.image_tags]
            )
            if write_captions_to_vector:
                captions = [
                    self.caption_vector_row(r)
                    for r in result.image_tags
                    if r.tag_category is TagCategory.CAPTION
                ]
                if captions:
                    written[VECTOR_TABLE] = self._writer.write(VECTOR_TABLE, captions)
        return written

    def flush_dictionary(self) -> int:
        """把当前字典整体 Upsert 到 dwd_mining_tag_dict_detail。"""
        rows = [e.to_row() for e in self._dict]
        return self._writer.write(DICT_TABLE, rows)

    # ---- 观测 ----

    def dictionary_health(self) -> dict[str, Any]:
        """字典健康度：四态分布 + 类别分布 + 候选池积压。"""
        return {
            "status_counts": self._lifecycle.status_counts(),
            "category_counts": self._dict.counts_by_category(),
            "candidate_pool_size": len(self._lifecycle.pool),
            "candidate_ready_for_review": len(self._lifecycle.pool.pending()),
            "active_tags": sum(1 for e in self._dict if e.status is TagStatus.ACTIVE),
        }
