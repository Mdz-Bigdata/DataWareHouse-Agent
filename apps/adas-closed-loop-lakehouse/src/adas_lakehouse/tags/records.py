"""标签管道的输入/输出记录：RawTag 进、DataTagRecord / ImageTagRecord 出。

原文第二章：「管道出口是两张标签事实表：dwd_mining_data_tag_detail（clip 级）与
dwd_mining_image_tag_detail（image 级）」，且每条标签都要携带血缘：
「tag_source、rule_id / model_name / model_version、confidence、infer_job_id」。

本模块只定义数据形状与落库行渲染，不含任何判定逻辑——
映射在 mapping.py，去重/冲突在 dedup.py，编排在 pipeline.py。

列名以 :mod:`catalog.registry` 为唯一事实源（见 :mod:`.tables` 的对照表）：内存里
``valid_flag`` 是一个布尔，落库是 ``tag_status`` 的 active/invalid——registry 侧该列的
注释写明了这就是「tags 侧的 valid_flag」。翻译只发生在 :meth:`TagRecord._base_row`
一处，别的地方不要再各拼一份。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..ids import ArtifactStatus
from .constants import FACT_TABLE_PK_FIELD_COUNT
from .dictionary import TagCategory, TagLevel
from .sources import TagSource, profile_for

__all__ = [
    "ReviewStatus",
    "TAG_STATUS_ACTIVE",
    "TAG_STATUS_INVALID",
    "DedupKey",
    "RawTag",
    "TagRecord",
    "DataTagRecord",
    "ImageTagRecord",
]

#: 事实表 ``tag_status`` 列的两个取值（registry：「active/invalid，互斥裁决落败置
#: invalid，即 tags 侧的 valid_flag」）。落败方只改状态不删行，保留可追溯。
TAG_STATUS_ACTIVE: str = "active"
TAG_STATUS_INVALID: str = "invalid"


class ReviewStatus(str, Enum):
    """人工审核状态，落到事实表 ``review_status``。

    原文第四章：「支持人工审核修正，审核结论回写 review_status / review_operator；
    未审核标签不得进入训练集圈选」。

    ⚠️ 原文未明确，本项目设计：原文只点名了 review_status 字段本身，没给枚举值。
    本项目定四态：pending（待审）/ approved（通过）/ rejected（驳回）/
    corrected（人工修正后通过，tag_id 被改写）。
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"

    @property
    def passed(self) -> bool:
        """是否算「审核通过」——approved 与 corrected 都算过审。"""
        return self in (ReviewStatus.APPROVED, ReviewStatus.CORRECTED)


@dataclass(frozen=True, slots=True)
class DedupKey:
    """事实表联合主键：(data_id/image_id, tag_id, tag_source)。

    原文第二章②：「标签事实表是 Paimon 主键表，联合主键（data_id/image_id,
    tag_id, tag_source）Upsert——重复写入无副作用，任务重跑不会产生重复标签」。
    """

    entity_id: str
    tag_id: str
    tag_source: TagSource

    def __post_init__(self) -> None:
        if not self.entity_id or not self.tag_id:
            raise ValueError("主键字段不能为空：entity_id / tag_id 必填")

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.entity_id, self.tag_id, self.tag_source.value)

    def __str__(self) -> str:
        return "|".join(self.as_tuple())


assert len(DedupKey.__slots__) == FACT_TABLE_PK_FIELD_COUNT, "联合主键必须是三段"


@dataclass(slots=True)
class RawTag:
    """管道入口：某个来源吐出来的一条原始标签，写法未归一。

    :param raw_tag: 原始写法（「降雨」「rain」「AEB紧急制动」……）
    :param source: 三来源之一
    :param data_id: clip 级锚点，必填——所有标签最终都挂在某个 clip 上
    :param image_id: 图片 ID；给了就走 image 级事实表，不给走 clip 级
    :param confidence: 模型标签必填，其余来源可空
    :param rule_id / rule_version: 规则标签的血缘（原文③）
    :param model_name / model_version: 模型标签的血缘（原文③）
    :param infer_job_id: 推理作业 ID（原文③）
    :param caption_text: tag_category=CAPTION 时的说明正文
    :param expected_category: 来源系统声明的类别，用于抓串类错误
    """

    raw_tag: str
    source: TagSource
    data_id: str
    image_id: str | None = None
    confidence: float | None = None
    rule_id: str | None = None
    rule_version: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    infer_job_id: str | None = None
    caption_text: str | None = None
    expected_category: TagCategory | None = None
    project_code: str = ""
    vehicle_code: str = ""
    parent_artifact_id: str | None = None
    tag_time: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.data_id:
            raise ValueError("RawTag.data_id 必填：标签必须挂在 clip 级锚点上")
        if isinstance(self.source, str):
            self.source = profile_for(self.source).source
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 需在 [0,1]，收到 {self.confidence}")

    @property
    def is_image_level(self) -> bool:
        return bool(self.image_id)

    def missing_lineage_fields(self) -> tuple[str, ...]:
        """管道第三步「血缘填充」的校验：该来源缺哪些必填血缘字段。"""
        missing: list[str] = []
        for fname in profile_for(self.source).required_lineage:
            if fname == "tag_source":
                continue  # 由 source 字段本身承载
            if getattr(self, fname, None) in (None, ""):
                missing.append(fname)
        return tuple(missing)


@dataclass(slots=True)
class TagRecord:
    """两张标签事实表的公共字段。子类补各自的实体主键。"""

    tag_id: str
    tag_name: str
    tag_category: TagCategory
    tag_source: TagSource
    tag_level: TagLevel = TagLevel.INHERITABLE
    confidence: float | None = None
    # ---- 血缘（原文③）----
    rule_id: str | None = None
    rule_version: str | None = None
    model_name: str | None = None
    model_version: str | None = None
    infer_job_id: str | None = None
    run_id: str | None = None
    artifact_id: str | None = None
    parent_artifact_id: str | None = None
    artifact_status: ArtifactStatus = ArtifactStatus.ACTIVE
    # ---- 审核（原文第四章）----
    review_status: ReviewStatus = ReviewStatus.PENDING
    review_operator: str | None = None
    review_time: datetime | None = None
    # ---- 映射与冲突留痕 ----
    source_raw_tag: str = ""
    mapping_type: str = ""
    mapping_note: str = ""
    conflict_resolution: str | None = None
    #: 内存形态的有效位；落库为 ``tag_status`` 的 active/invalid。
    valid_flag: bool = True
    # ---- 通用维度 ----
    project_code: str = ""
    vehicle_code: str = ""
    #: 首次打标时间（最近一次打标由系统字段 update_time 承载），落库列 ``first_tag_time``。
    first_tag_time: datetime | None = None

    @property
    def entity_id(self) -> str:  # pragma: no cover - 子类覆写
        raise NotImplementedError

    @property
    def dedup_key(self) -> DedupKey:
        return DedupKey(self.entity_id, self.tag_id, self.tag_source)

    def _base_row(self) -> dict[str, Any]:
        return {
            "tag_id": self.tag_id,
            "tag_name": self.tag_name,
            "tag_category": self.tag_category.value,
            "tag_source": self.tag_source.value,
            "tag_level": self.tag_level.value,
            "confidence": self.confidence,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "infer_job_id": self.infer_job_id,
            "run_id": self.run_id,
            "artifact_id": self.artifact_id,
            "parent_artifact_id": self.parent_artifact_id,
            "artifact_status": self.artifact_status.value,
            "review_status": self.review_status.value,
            "review_operator": self.review_operator,
            "review_time": self.review_time,
            "source_raw_tag": self.source_raw_tag,
            "mapping_type": self.mapping_type,
            "mapping_note": self.mapping_note,
            "conflict_resolution": self.conflict_resolution,
            "tag_status": TAG_STATUS_ACTIVE if self.valid_flag else TAG_STATUS_INVALID,
            "project_code": self.project_code,
            "vehicle_code": self.vehicle_code,
            "first_tag_time": self.first_tag_time,
        }

    def to_row(self) -> dict[str, Any]:  # pragma: no cover - 子类覆写
        raise NotImplementedError


@dataclass(slots=True)
class DataTagRecord(TagRecord):
    """clip 级标签事实（dwd_mining_data_tag_detail 的一行）。"""

    data_id: str = ""

    def __post_init__(self) -> None:
        if not self.data_id:
            raise ValueError("DataTagRecord.data_id 必填（联合主键第一段）")

    @property
    def entity_id(self) -> str:
        return self.data_id

    def to_row(self) -> dict[str, Any]:
        row = {"data_id": self.data_id}
        row.update(self._base_row())
        return row


@dataclass(slots=True)
class ImageTagRecord(TagRecord):
    """image 级标签事实（dwd_mining_image_tag_detail 的一行）。

    ``inherited_from_data_tag`` 标记这条是不是从 clip 标签自动继承来的
    （原文：clip 标签可自动继承到它抽出来的每一张图片）。
    ``caption_text`` 只在 tag_category=CAPTION 时有值。
    """

    image_id: str = ""
    data_id: str = ""
    inherited_from_data_tag: bool = False
    caption_text: str | None = None

    def __post_init__(self) -> None:
        if not self.image_id:
            raise ValueError("ImageTagRecord.image_id 必填（联合主键第一段）")
        if not self.data_id:
            raise ValueError("ImageTagRecord.data_id 必填：图片必须能回溯到 clip")
        if self.tag_category is TagCategory.CAPTION and not (self.caption_text or "").strip():
            raise ValueError("tag_category=CAPTION 的记录必须带 caption_text")

    @property
    def entity_id(self) -> str:
        return self.image_id

    def to_row(self) -> dict[str, Any]:
        row = {"image_id": self.image_id, "data_id": self.data_id}
        row.update(self._base_row())
        row["inherited_from_data_tag"] = self.inherited_from_data_tag
        row["caption_text"] = self.caption_text
        return row
