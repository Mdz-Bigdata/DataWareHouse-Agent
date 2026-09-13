"""管道第一步：字典映射——三来源标签归一到标准 tag_id，未匹配的进候选池。

原文第二章①：
    「标签先查字典（含别名表），命中则归一到标准 tag_id；
      未匹配的不直接入库，进候选池待审——这是防爆炸的第一道闸」

映射规则（判定顺序即防爆炸的优先级）：
  1. 标准名精确命中        → CANONICAL
  2. 别名命中（alias_json）→ ALIAS，改写成标准 tag_id
  3. 命中的条目是 merged   → MERGED_REDIRECT，跟随 merged_into_tag_id 改写
     （原文第三章：「重复标签并入目标标签，原名保留为别名」）
  4. 命中的条目是 deprecated → DEPRECATED_REJECTED，拒绝新写入
     （原文：「过时标签下线，历史数据仍可按原标签回溯」——只拦新写，不动历史）
  5. 命中的条目是 candidate  → STILL_CANDIDATE，不参与正式检索与统计
  6. 完全未匹配              → CANDIDATE，进候选池待审，**不入事实表**
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from .dictionary import (
    TagCategory,
    TagDictEntry,
    TagDictionary,
    TagStatus,
    default_dictionary,
    normalize_tag_text,
)
from .sources import TagSource, profile_for

__all__ = [
    "MappingOutcome",
    "MappingResult",
    "TagMapper",
    "MODEL_TAG_MIN_CONFIDENCE",
]

#: ⚠️ 原文未明确，本项目设计：模型标签入库的最低置信度。
#: 原文只说「VLM 推理标签天然带噪声」「confidence 要落表」，没给门限数值。
#: 本项目取 0.50——低于随机二分的置信度没有入库价值；真正的把关靠第四章的人工审核，
#: 所以这里只做「明显噪声」的粗筛，不做严格准入。可用 TagMapper(min_confidence=…) 覆盖。
MODEL_TAG_MIN_CONFIDENCE: Final[float] = 0.50


class MappingOutcome(str, Enum):
    """字典映射的判定结果。只有 ``accepted`` 为真的结果才允许进入事实表。"""

    CANONICAL = "canonical"
    ALIAS = "alias"
    MERGED_REDIRECT = "merged_redirect"
    CANDIDATE = "candidate"
    STILL_CANDIDATE = "still_candidate"
    DEPRECATED_REJECTED = "deprecated_rejected"
    SOURCE_NOT_ALLOWED = "source_not_allowed"
    LOW_CONFIDENCE = "low_confidence"
    EMPTY_INPUT = "empty_input"

    @property
    def accepted(self) -> bool:
        """该结果是否允许写入标签事实表。"""
        return self in _ACCEPTED

    @property
    def needs_candidate_pool(self) -> bool:
        """该结果是否应把原始写法送进候选池待审。"""
        return self is MappingOutcome.CANDIDATE


_ACCEPTED: frozenset[MappingOutcome] = frozenset(
    {
        MappingOutcome.CANONICAL,
        MappingOutcome.ALIAS,
        MappingOutcome.MERGED_REDIRECT,
    }
)


@dataclass(frozen=True, slots=True)
class MappingResult:
    """一次字典映射的完整结论，可直接落到事实表的 mapping_* 列上。

    :param raw_tag: 来源系统给的原始写法（保留，便于回溯「模型当时吐的是什么词」）
    :param normalized: 归一化后的匹配键
    :param outcome: 判定结果
    :param tag_id: 归一后的标准 tag_id；未命中时为 None
    :param entry: 命中的字典条目；未命中时为 None
    :param reason: 人类可读的判定理由，写入 ``mapping_note`` 便于排障
    """

    raw_tag: str
    normalized: str
    outcome: MappingOutcome
    tag_id: str | None = None
    entry: TagDictEntry | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.outcome.accepted

    @property
    def category(self) -> TagCategory | None:
        return self.entry.category if self.entry else None


class TagMapper:
    """字典映射器：把任意来源的任意写法收口成标准 tag_id。

    :param dictionary: 受控词表，默认用 :func:`dictionary.default_dictionary`
    :param min_confidence: 模型标签最低置信度，默认 :data:`MODEL_TAG_MIN_CONFIDENCE`
    """

    __slots__ = ("_dict", "_min_confidence")

    def __init__(
        self,
        dictionary: TagDictionary | None = None,
        *,
        min_confidence: float = MODEL_TAG_MIN_CONFIDENCE,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError(f"min_confidence 需在 [0,1]，收到 {min_confidence}")
        self._dict = dictionary if dictionary is not None else default_dictionary()
        self._min_confidence = min_confidence

    @property
    def dictionary(self) -> TagDictionary:
        return self._dict

    @property
    def min_confidence(self) -> float:
        return self._min_confidence

    def map(
        self,
        raw_tag: str,
        source: TagSource | str,
        *,
        confidence: float | None = None,
        expected_category: TagCategory | str | None = None,
    ) -> MappingResult:
        """把一个原始标签写法映射到标准 tag_id。

        :param raw_tag: 原始写法，例如「降雨」「rain」「AEB紧急制动」
        :param source: 三来源之一；未知来源直接抛错（原文：没有旁路）
        :param confidence: 模型标签的置信度，模型来源必填
        :param expected_category: 调用方声明的类别，用于抓「标签串类」的错误
        :return: :class:`MappingResult`
        :raises ValueError: 来源非法，或模型来源缺 confidence
        """
        src = profile_for(source).source
        normalized = normalize_tag_text(raw_tag or "")
        if not normalized:
            return MappingResult(
                raw_tag=raw_tag or "",
                normalized="",
                outcome=MappingOutcome.EMPTY_INPUT,
                reason="原始标签为空或只含分隔符，直接丢弃",
            )

        if src is TagSource.MODEL:
            if confidence is None:
                raise ValueError(
                    "模型标签必须携带 confidence（原文③：回答「这个标签从哪来、可信度多少」）"
                )
            if confidence < self._min_confidence:
                return MappingResult(
                    raw_tag=raw_tag,
                    normalized=normalized,
                    outcome=MappingOutcome.LOW_CONFIDENCE,
                    reason=(
                        f"模型标签 confidence={confidence} 低于门限 {self._min_confidence}"
                        "（⚠️ 门限为本项目设计，原文未给数值）"
                    ),
                )

        entry = self._dict.lookup(raw_tag)
        if entry is None:
            return MappingResult(
                raw_tag=raw_tag,
                normalized=normalized,
                outcome=MappingOutcome.CANDIDATE,
                reason="字典与别名表均未匹配，进候选池待审，不直接入库（防爆炸第一道闸）",
            )

        # merged：跟随合并链改写成目标标签，原名已作为别名保留在字典里
        if entry.status is TagStatus.MERGED:
            target = self._dict.resolve(raw_tag)
            if target is None:  # pragma: no cover - resolve 已保证非空或抛错
                raise ValueError(f"{entry.tag_id} 的合并目标解析失败")
            result = MappingResult(
                raw_tag=raw_tag,
                normalized=normalized,
                outcome=MappingOutcome.MERGED_REDIRECT,
                tag_id=target.tag_id,
                entry=target,
                reason=f"{entry.tag_id} 已合并到 {target.tag_id}，按目标标签落库",
            )
            return self._post_check(result, src, expected_category)

        if entry.status is TagStatus.DEPRECATED:
            return MappingResult(
                raw_tag=raw_tag,
                normalized=normalized,
                outcome=MappingOutcome.DEPRECATED_REJECTED,
                tag_id=entry.tag_id,
                entry=entry,
                reason="标签已废弃，拒绝新写入；历史数据仍可按原标签回溯",
            )

        if entry.status is TagStatus.CANDIDATE:
            return MappingResult(
                raw_tag=raw_tag,
                normalized=normalized,
                outcome=MappingOutcome.STILL_CANDIDATE,
                tag_id=entry.tag_id,
                entry=entry,
                reason="标签仍在候选池，未转正前不参与正式检索与统计",
            )

        hit_canonical = normalized == normalize_tag_text(entry.tag_name)
        result = MappingResult(
            raw_tag=raw_tag,
            normalized=normalized,
            outcome=MappingOutcome.CANONICAL if hit_canonical else MappingOutcome.ALIAS,
            tag_id=entry.tag_id,
            entry=entry,
            reason=(
                "标准名精确命中"
                if hit_canonical
                else f"别名命中，归一到标准标签「{entry.tag_name}」({entry.tag_id})"
            ),
        )
        return self._post_check(result, src, expected_category)

    def _post_check(
        self,
        result: MappingResult,
        source: TagSource,
        expected_category: TagCategory | str | None,
    ) -> MappingResult:
        """命中之后的准入检查：来源白名单 + 类别声明一致性。"""
        entry = result.entry
        assert entry is not None
        if not entry.accepts_source(source):
            allowed = ",".join(s.value for s in entry.applicable_sources)
            return MappingResult(
                raw_tag=result.raw_tag,
                normalized=result.normalized,
                outcome=MappingOutcome.SOURCE_NOT_ALLOWED,
                tag_id=entry.tag_id,
                entry=entry,
                reason=f"标签 {entry.tag_id} 只接受来源 [{allowed}]，当前来源 {source.value}",
            )
        if expected_category is not None:
            want = (
                expected_category
                if isinstance(expected_category, TagCategory)
                else TagCategory(str(expected_category).upper())
            )
            if want is not entry.category:
                return MappingResult(
                    raw_tag=result.raw_tag,
                    normalized=result.normalized,
                    outcome=MappingOutcome.SOURCE_NOT_ALLOWED,
                    tag_id=entry.tag_id,
                    entry=entry,
                    reason=(
                        f"调用方声明类别 {want.value}，字典归属 {entry.category.value}，"
                        "类别口径不一致，拒绝入库"
                    ),
                )
        return result

    def map_many(
        self, raw_tags: list[str] | tuple[str, ...], source: TagSource | str, **kwargs: object
    ) -> list[MappingResult]:
        """批量映射，逐条独立判定（一条失败不影响其余）。"""
        confidence = kwargs.get("confidence")
        expected = kwargs.get("expected_category")
        out: list[MappingResult] = []
        for raw in raw_tags:
            out.append(
                self.map(
                    raw,
                    source,
                    confidence=confidence,  # type: ignore[arg-type]
                    expected_category=expected,  # type: ignore[arg-type]
                )
            )
        return out
