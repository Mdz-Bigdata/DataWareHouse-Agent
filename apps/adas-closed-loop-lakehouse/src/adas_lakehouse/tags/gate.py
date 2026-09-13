"""质量门禁联动：未审核标签进不了训练集；增量更新不覆盖历史。

原文第四章：
    「标签体系的最后一道闸在出口侧。VLM 推理标签天然带噪声——大模型可能把广告牌
      认成行人、把黄昏标成夜间。所以对模型标签的处理是：支持人工审核修正，审核结论
      回写 review_status / review_operator；未审核标签不得进入训练集圈选
      ——这与湖仓质量门禁的「自动标注准入」规则对齐，模型产出永远先过审再上岗。」
    「增量更新也不破坏历史：标签变更只 Upsert 变更行，不覆盖历史版本，
      配合 Paimon 时间旅行，任意历史时点的标签状态都可回溯。」

本模块实现两件事：
  1. :class:`TrainingSetGate`——训练集圈选的准入过滤（出口侧最后一道闸）；
  2. :func:`time_travel_options`——按历史时点回溯标签状态的 Paimon 读参数。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..ids import ArtifactStatus
from .dictionary import TagDictionary, TagStatus, default_dictionary
from .records import ReviewStatus, TagRecord
from .sources import profile_for

__all__ = [
    "RejectReason",
    "GateDecision",
    "TrainingSetGate",
    "apply_review",
    "time_travel_options",
]


class RejectReason(str, Enum):
    """训练集圈选被拦下的原因。"""

    NOT_REVIEWED = "not_reviewed"  # 未审核（原文：未审核标签不得进入训练集圈选）
    REVIEW_REJECTED = "review_rejected"  # 审核驳回
    INVALID_BY_CONFLICT = "invalid_by_conflict"  # 互斥裁决落败，valid_flag=false
    TAG_NOT_ACTIVE = "tag_not_active"  # 字典里不是 active 态（候选/废弃/合并）
    ARTIFACT_SUPERSEDED = "artifact_superseded"  # 产物已被新版本取代
    ARTIFACT_INVALID = "artifact_invalid"  # 产物被判废


@dataclass(frozen=True, slots=True)
class GateDecision:
    """一条标签的准入判定。"""

    admitted: bool
    reason: RejectReason | None = None
    detail: str = ""

    def __bool__(self) -> bool:
        return self.admitted


class TrainingSetGate:
    """训练集圈选准入闸。

    ⚠️ 原文未明确，本项目设计：原文只点名「未审核标签不得进入训练集圈选」，
    本项目把出口侧的拦截条件补全为五条（见 :class:`RejectReason`），其中
    「未审核」只对 ``needs_human_review`` 的来源（模型标签）生效——采集标签是
    人工约定、规则标签跟规则版本走，本身就是确定口径，不需要再过一次人工审核。
    """

    __slots__ = ("_dict",)

    def __init__(self, dictionary: TagDictionary | None = None) -> None:
        self._dict = dictionary if dictionary is not None else default_dictionary()

    def check(self, record: TagRecord) -> GateDecision:
        """判定单条标签能否进入训练集圈选。"""
        if not record.valid_flag:
            return GateDecision(
                False,
                RejectReason.INVALID_BY_CONFLICT,
                f"互斥裁决落败：{record.conflict_resolution}",
            )
        if record.artifact_status is ArtifactStatus.SUPERSEDED:
            return GateDecision(False, RejectReason.ARTIFACT_SUPERSEDED, "标签产物已被重刷版本取代")
        if record.artifact_status is ArtifactStatus.INVALID:
            return GateDecision(False, RejectReason.ARTIFACT_INVALID, "标签产物已判废")

        entry = self._dict.get(record.tag_id)
        if entry is not None and entry.status is not TagStatus.ACTIVE:
            return GateDecision(
                False,
                RejectReason.TAG_NOT_ACTIVE,
                f"字典状态 {entry.status.value}：{entry.status.meaning}",
            )

        if record.review_status is ReviewStatus.REJECTED:
            return GateDecision(False, RejectReason.REVIEW_REJECTED, "人工审核驳回")

        if profile_for(record.tag_source).needs_human_review and not record.review_status.passed:
            return GateDecision(
                False,
                RejectReason.NOT_REVIEWED,
                "模型产出永远先过审再上岗：未审核标签不得进入训练集圈选",
            )
        return GateDecision(True)

    def filter(
        self, records: Iterable[TagRecord]
    ) -> tuple[list[TagRecord], list[tuple[TagRecord, GateDecision]]]:
        """批量过滤。

        :return: (可进训练集的记录, [(被拦记录, 判定), ...])
        """
        admitted: list[TagRecord] = []
        blocked: list[tuple[TagRecord, GateDecision]] = []
        for rec in records:
            decision = self.check(rec)
            (admitted.append(rec) if decision.admitted else blocked.append((rec, decision)))
        return admitted, blocked

    def reject_stats(self, records: Iterable[TagRecord]) -> dict[str, int]:
        """被拦原因分布，用于「自动标注准入」看板。"""
        out: dict[str, int] = {}
        for rec in records:
            decision = self.check(rec)
            if decision.reason is not None:
                out[decision.reason.value] = out.get(decision.reason.value, 0) + 1
        return out


def apply_review(
    record: TagRecord,
    status: ReviewStatus,
    operator: str,
    *,
    corrected_tag_id: str | None = None,
    dictionary: TagDictionary | None = None,
    reviewed_at: datetime | None = None,
) -> TagRecord:
    """回写审核结论（原文：审核结论回写 review_status / review_operator）。

    「人工审核修正」= 审核人可以把模型标错的 tag_id 改成正确的那个，此时状态是
    ``corrected``，原始写法仍留在 ``source_raw_tag`` 里可回溯。

    注意：修正 tag_id 等于换了联合主键，落库时是**新增一行**，
    原行需由调用方按需标 ``valid_flag=False``——原文要求「不覆盖历史版本」。

    :raises ValueError: 操作人为空、corrected_tag_id 不在字典、或状态与参数不匹配
    """
    if not operator:
        raise ValueError("审核必须记录操作人（review_operator 可追责）")
    if status is ReviewStatus.CORRECTED and not corrected_tag_id:
        raise ValueError("corrected 状态必须给出修正后的 corrected_tag_id")
    if corrected_tag_id:
        book = dictionary if dictionary is not None else default_dictionary()
        entry = book.require(corrected_tag_id)
        if entry.status is not TagStatus.ACTIVE:
            raise ValueError(f"修正目标 {corrected_tag_id} 不是 active 态，不能作为审核结论")
        record.tag_id = entry.tag_id
        record.tag_name = entry.tag_name
        record.tag_category = entry.category
        record.mapping_note = (
            f"{record.mapping_note} | 人工修正：{record.source_raw_tag} → {entry.tag_id}"
        ).strip(" |")
    record.review_status = status
    record.review_operator = operator
    record.review_time = reviewed_at or datetime.now()
    return record


def time_travel_options(as_of: datetime | int) -> dict[str, str]:
    """生成 Paimon 时间旅行读参数：任意历史时点的标签状态都可回溯。

    原文：「增量更新也不破坏历史：标签变更只 Upsert 变更行，不覆盖历史版本，
    配合 Paimon 时间旅行，任意历史时点的标签状态都可回溯。」

    :param as_of: 时间点（datetime）或 Paimon snapshot id（int）
    :return: 可直接塞进 Flink SQL 动态表参数的 dict
    :raises TypeError: 入参既不是 datetime 也不是 int
    """
    if isinstance(as_of, bool):  # bool 是 int 的子类，先挡掉
        raise TypeError("as_of 不能是 bool")
    if isinstance(as_of, int):
        return {"scan.mode": "from-snapshot", "scan.snapshot-id": str(as_of)}
    if isinstance(as_of, datetime):
        return {
            "scan.mode": "from-timestamp",
            "scan.timestamp-millis": str(int(as_of.timestamp() * 1000)),
        }
    raise TypeError(f"as_of 需要 datetime 或 snapshot id(int)，收到 {type(as_of).__name__}")
