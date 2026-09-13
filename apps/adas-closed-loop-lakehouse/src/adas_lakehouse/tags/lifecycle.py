"""四态生命周期：候选池 + 状态机 + 标签审核流（平台工单 + 双人复核）。

原文第三章：
    「标签不是一次写入永久有效的——场景认知会更新，标签会重复、会过时。
      字典为每个标签维护四态状态机：
        candidate 候选   字典未匹配的新标签进候选池，不参与正式检索与统计，等待审核
        active 生效      审核通过后转正，可被检索、圈选、统计使用
        deprecated 废弃  过时标签下线，历史数据仍可按原标签回溯
        merged 合并      重复标签并入目标标签，原名保留为别名
      两个关键配套机制：字典变更走标签审核流（平台工单 + 双人复核，谁也不能直接改字典）；
      既有场景标签并入字典。」

「双人复核」= 2 个复核人，且复核人不能是提交人（见 DICT_CHANGE_REVIEWER_COUNT）。

⚠️ 原文未明确，本项目设计：
  · 四态之间哪些流转合法，原文只给了状态含义没给状态图，见 ALLOWED_TRANSITIONS 的逐条注释；
  · 候选标签「攒够多少次命中才值得提审」原文没给阈值，见 CANDIDATE_PROMOTION_MIN_HITS；
  · 候选池滞留多久自动归档，原文没给 TTL，见 CANDIDATE_POOL_TTL_DAYS。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import ClassVar, Final

from .constants import DICT_CHANGE_REVIEWER_COUNT, LIFECYCLE_STATE_COUNT, LIFECYCLE_STATES
from .dictionary import (
    TagCategory,
    TagDictEntry,
    TagDictionary,
    TagLevel,
    TagStatus,
    default_dictionary,
    normalize_tag_text,
    slugify_tag_id,
)
from .sources import TagSource

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TransitionError",
    "ChangeRequestType",
    "ChangeRequestState",
    "ChangeRequest",
    "CandidateTag",
    "CandidatePool",
    "TagLifecycleManager",
    "CANDIDATE_PROMOTION_MIN_HITS",
    "CANDIDATE_POOL_TTL_DAYS",
]

#: ⚠️ 原文未明确，本项目设计：候选标签提审的最小命中次数。
#: 原文只说「未匹配的不直接入库，进候选池待审」，没说攒多少次才提审。
#: 本项目取 10——低于这个次数多半是模型的一次性口误，不值得占用双人复核的人力。
CANDIDATE_PROMOTION_MIN_HITS: Final[int] = 10

#: ⚠️ 原文未明确，本项目设计：候选池滞留 TTL（天）。超期未提审则自动归档，
#: 防止候选池本身变成第二个「标签爆炸」的垃圾场。本项目取 90 天。
CANDIDATE_POOL_TTL_DAYS: Final[int] = 90

#: 四态状态机的合法流转。
#: ⚠️ 原文未明确，本项目设计：原文只给了四态含义，没给状态图。逐条依据：
#:   candidate → active      「审核通过后转正」，原文明写
#:   candidate → merged      候选与既有标签重复时直接并入，不必先转正
#:   candidate → deprecated  候选被驳回归档；仍留痕以免同一个错词反复提审
#:   active    → deprecated  「过时标签下线」，原文明写
#:   active    → merged      「重复标签并入目标标签」，原文明写
#:   deprecated → active     误下线的召回通道，同样要走工单 + 双人复核
#:   merged     → 终态       墓碑不再流转，否则合并链会成环
ALLOWED_TRANSITIONS: dict[TagStatus, frozenset[TagStatus]] = {
    TagStatus.CANDIDATE: frozenset({TagStatus.ACTIVE, TagStatus.MERGED, TagStatus.DEPRECATED}),
    TagStatus.ACTIVE: frozenset({TagStatus.DEPRECATED, TagStatus.MERGED}),
    TagStatus.DEPRECATED: frozenset({TagStatus.ACTIVE}),
    TagStatus.MERGED: frozenset(),
}

assert len(ALLOWED_TRANSITIONS) == LIFECYCLE_STATE_COUNT, "状态机必须覆盖四态"
assert {s.value for s in ALLOWED_TRANSITIONS} == set(LIFECYCLE_STATES), "四态取值与原文不一致"


class TransitionError(RuntimeError):
    """非法的状态流转（例如想把 merged 墓碑改回 active）。"""


# --------------------------------------------------------------------------- 候选池


@dataclass(slots=True)
class CandidateTag:
    """候选池里的一条待审标签。

    原文①：「未匹配的不直接入库，进候选池待审——这是防爆炸的第一道闸」。
    候选标签**不写事实表**，只在池子里累计证据（命中次数、来源、样本 data_id），
    攒够证据再走审核流进字典。
    """

    normalized: str
    raw_forms: tuple[str, ...]
    sources: tuple[TagSource, ...] = ()
    hit_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sample_data_ids: tuple[str, ...] = ()
    proposed_category: TagCategory | None = None
    #: 已提交的字典变更工单号
    change_request_id: str | None = None

    #: ⚠️ 原文未明确，本项目设计：样本最多留 20 个，够审核人判断即可，不做无界累积
    SAMPLE_LIMIT: ClassVar[int] = 20

    @property
    def display_name(self) -> str:
        """最常见的原始写法，作为提审时的建议标准名。"""
        return self.raw_forms[0] if self.raw_forms else self.normalized

    def promotable(self, *, min_hits: int = CANDIDATE_PROMOTION_MIN_HITS) -> bool:
        """证据是否足够提审。"""
        return self.hit_count >= min_hits

    def expired(
        self, *, now: datetime | None = None, ttl_days: int = CANDIDATE_POOL_TTL_DAYS
    ) -> bool:
        """是否已在池中滞留超过 TTL。"""
        if self.last_seen is None:
            return False
        return (now or datetime.now()) - self.last_seen > timedelta(days=ttl_days)


class CandidatePool:
    """候选池：未匹配标签的暂存区，写入路径的第一道闸的背面。"""

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[str, CandidateTag] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items.values())

    def __contains__(self, normalized: object) -> bool:
        return normalized in self._items

    def offer(
        self,
        raw_tag: str,
        source: TagSource,
        *,
        data_id: str = "",
        category_hint: TagCategory | None = None,
        seen_at: datetime | None = None,
    ) -> CandidateTag:
        """把一个未匹配写法投进候选池（重复投递只累计证据）。

        :raises ValueError: raw_tag 归一后为空
        """
        key = normalize_tag_text(raw_tag)
        if not key:
            raise ValueError(f"候选标签写法为空：{raw_tag!r}")
        now = seen_at or datetime.now()
        item = self._items.get(key)
        if item is None:
            item = CandidateTag(
                normalized=key,
                raw_forms=(raw_tag,),
                sources=(source,),
                hit_count=1,
                first_seen=now,
                last_seen=now,
                sample_data_ids=(data_id,) if data_id else (),
                proposed_category=category_hint,
            )
            self._items[key] = item
            return item

        item.hit_count += 1
        item.last_seen = now
        if raw_tag not in item.raw_forms:
            item.raw_forms = (*item.raw_forms, raw_tag)
        if source not in item.sources:
            item.sources = (*item.sources, source)
        if (
            data_id
            and data_id not in item.sample_data_ids
            and len(item.sample_data_ids) < CandidateTag.SAMPLE_LIMIT
        ):
            item.sample_data_ids = (*item.sample_data_ids, data_id)
        if item.proposed_category is None:
            item.proposed_category = category_hint
        return item

    def get(self, raw_tag: str) -> CandidateTag | None:
        return self._items.get(normalize_tag_text(raw_tag))

    def pending(self, *, min_hits: int = CANDIDATE_PROMOTION_MIN_HITS) -> list[CandidateTag]:
        """证据足够、尚未提审的候选，按命中次数降序——审核人从这里排期。"""
        out = [
            c
            for c in self._items.values()
            if c.promotable(min_hits=min_hits) and c.change_request_id is None
        ]
        out.sort(key=lambda c: (-c.hit_count, c.normalized))
        return out

    def expired(self, *, now: datetime | None = None) -> list[CandidateTag]:
        """滞留超 TTL 的候选，可批量归档。"""
        return [c for c in self._items.values() if c.expired(now=now)]

    def discard(self, raw_tag: str) -> bool:
        """从池中移除（转正或归档后调用）。"""
        return self._items.pop(normalize_tag_text(raw_tag), None) is not None


# --------------------------------------------------------------------------- 审核流


class ChangeRequestType(str, Enum):
    """字典变更工单类型。"""

    CREATE = "create"  # 候选转正，新增字典条目
    ACTIVATE = "activate"  # deprecated → active 召回
    DEPRECATE = "deprecate"  # 标签下线
    MERGE = "merge"  # 重复标签并入目标标签
    UPDATE_ALIAS = "update_alias"  # 别名治理：给既有标签补写法


class ChangeRequestState(str, Enum):
    """工单自身的状态（与标签四态无关）。"""

    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


@dataclass(slots=True)
class ChangeRequest:
    """一张字典变更工单。

    原文：「字典变更走标签审核流（平台工单 + 双人复核，谁也不能直接改字典）」。
    因此本类强制：复核人 ≥ DICT_CHANGE_REVIEWER_COUNT（2）人，且不含提交人。
    """

    request_id: str
    kind: ChangeRequestType
    submitter: str
    tag_id: str
    reason: str = ""
    target_tag_id: str | None = None  # MERGE 用
    payload: dict[str, object] = field(default_factory=dict)
    approvals: tuple[str, ...] = ()
    rejections: tuple[str, ...] = ()
    state: ChangeRequestState = ChangeRequestState.OPEN
    created_at: datetime | None = None
    applied_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.submitter:
            raise ValueError("工单必须有提交人（可追责）")
        if self.kind is ChangeRequestType.MERGE and not self.target_tag_id:
            raise ValueError("MERGE 工单必须给出 target_tag_id（并入哪个标签）")
        if self.created_at is None:
            self.created_at = datetime.now()

    @property
    def approved(self) -> bool:
        """是否已满足双人复核。"""
        return len(self.approvals) >= DICT_CHANGE_REVIEWER_COUNT

    def approve(self, reviewer: str) -> ChangeRequest:
        """复核通过。

        :raises ValueError: 复核人是提交人、重复复核、或工单已关闭
        """
        self._assert_open()
        if reviewer == self.submitter:
            raise ValueError(
                f"{reviewer} 是工单提交人，不能自审——双人复核要求 "
                f"{DICT_CHANGE_REVIEWER_COUNT} 个独立复核人"
            )
        if reviewer in self.approvals:
            raise ValueError(f"{reviewer} 已复核过工单 {self.request_id}")
        self.approvals = (*self.approvals, reviewer)
        if self.approved:
            self.state = ChangeRequestState.APPROVED
        return self

    def reject(self, reviewer: str, reason: str = "") -> ChangeRequest:
        """复核驳回：一票否决。"""
        self._assert_open()
        if reviewer == self.submitter:
            raise ValueError(f"{reviewer} 是工单提交人，不能自审")
        self.rejections = (*self.rejections, reviewer)
        self.state = ChangeRequestState.REJECTED
        if reason:
            self.reason = f"{self.reason} | 驳回：{reason}".strip(" |")
        return self

    def _assert_open(self) -> None:
        if self.state in (ChangeRequestState.REJECTED, ChangeRequestState.APPLIED):
            raise ValueError(f"工单 {self.request_id} 已是 {self.state.value} 态，不可再复核")


def _new_request_id(kind: ChangeRequestType, moment: datetime | None = None) -> str:
    """工单号：``TCR_{type}_{yyyyMMddHHmmss}_{seq}``，与三级 ID 的时间戳格式一致。"""
    ts = (moment or datetime.now()).strftime("%Y%m%d%H%M%S")
    return f"TCR_{kind.value}_{ts}_{uuid.uuid4().hex[:4]}"


# --------------------------------------------------------------------------- 管理器


class TagLifecycleManager:
    """标签生命周期管理器：候选池 + 工单 + 状态机，三件套一起用。

    典型用法::

        mgr = TagLifecycleManager()
        mgr.pool.offer("水坑路面", TagSource.MODEL, data_id=did)   # 未匹配 → 候选池
        cr = mgr.submit_create("水坑路面", TagCategory.ENV, "ENV_SURFACE", submitter="alice")
        cr.approve("bob"); cr.approve("carol")                      # 双人复核
        mgr.apply(cr)                                               # 转正进字典
    """

    __slots__ = ("_dict", "pool", "_requests")

    def __init__(self, dictionary: TagDictionary | None = None) -> None:
        self._dict = dictionary if dictionary is not None else default_dictionary()
        self.pool = CandidatePool()
        self._requests: dict[str, ChangeRequest] = {}

    @property
    def dictionary(self) -> TagDictionary:
        return self._dict

    @property
    def requests(self) -> tuple[ChangeRequest, ...]:
        return tuple(self._requests.values())

    # ---- 状态机 ----

    @staticmethod
    def can_transition(current: TagStatus, target: TagStatus) -> bool:
        return target in ALLOWED_TRANSITIONS[current]

    def assert_transition(self, tag_id: str, target: TagStatus) -> TagDictEntry:
        """校验流转合法性。

        :raises TransitionError: 非法流转
        """
        entry = self._dict.require(tag_id)
        if entry.status is target:
            raise TransitionError(f"{tag_id} 已经是 {target.value} 态")
        if not self.can_transition(entry.status, target):
            allowed = sorted(s.value for s in ALLOWED_TRANSITIONS[entry.status])
            raise TransitionError(
                f"{tag_id}: {entry.status.value} → {target.value} 不是合法流转，可流转到 {allowed}"
            )
        return entry

    # ---- 提单 ----

    def _register(self, req: ChangeRequest) -> ChangeRequest:
        self._requests[req.request_id] = req
        return req

    def submit_create(
        self,
        tag_name: str,
        category: TagCategory,
        parent_tag_id: str,
        *,
        submitter: str,
        tag_id: str | None = None,
        tag_level: TagLevel = TagLevel.INHERITABLE,
        aliases: tuple[str, ...] = (),
        reason: str = "",
    ) -> ChangeRequest:
        """候选转正工单：把候选池里的写法变成正式字典条目。"""
        if parent_tag_id not in self._dict:
            raise KeyError(f"父标签 {parent_tag_id} 不在字典中，无法挂载新标签")
        new_id = tag_id or f"{parent_tag_id}_{slugify_tag_id(tag_name)}"
        if new_id in self._dict:
            raise ValueError(f"tag_id {new_id} 已存在，请改用 UPDATE_ALIAS 或 MERGE 工单")
        req = ChangeRequest(
            request_id=_new_request_id(ChangeRequestType.CREATE),
            kind=ChangeRequestType.CREATE,
            submitter=submitter,
            tag_id=new_id,
            reason=reason,
            payload={
                "tag_name": tag_name,
                "category": category.value,
                "parent_tag_id": parent_tag_id,
                "tag_level": tag_level.value,
                "aliases": list(aliases),
            },
        )
        candidate = self.pool.get(tag_name)
        if candidate is not None:
            candidate.change_request_id = req.request_id
        return self._register(req)

    def submit_merge(
        self, source_tag_id: str, target_tag_id: str, *, submitter: str, reason: str = ""
    ) -> ChangeRequest:
        """合并工单：治理「雨天/降雨」这类重复的正式手段。"""
        self.assert_transition(source_tag_id, TagStatus.MERGED)
        self._dict.require(target_tag_id)
        return self._register(
            ChangeRequest(
                request_id=_new_request_id(ChangeRequestType.MERGE),
                kind=ChangeRequestType.MERGE,
                submitter=submitter,
                tag_id=source_tag_id,
                target_tag_id=target_tag_id,
                reason=reason,
            )
        )

    def submit_deprecate(self, tag_id: str, *, submitter: str, reason: str = "") -> ChangeRequest:
        """下线工单：过时标签不再接受新写入，历史数据仍可按原标签回溯。"""
        self.assert_transition(tag_id, TagStatus.DEPRECATED)
        return self._register(
            ChangeRequest(
                request_id=_new_request_id(ChangeRequestType.DEPRECATE),
                kind=ChangeRequestType.DEPRECATE,
                submitter=submitter,
                tag_id=tag_id,
                reason=reason,
            )
        )

    def submit_activate(self, tag_id: str, *, submitter: str, reason: str = "") -> ChangeRequest:
        """召回工单：deprecated → active。"""
        self.assert_transition(tag_id, TagStatus.ACTIVE)
        return self._register(
            ChangeRequest(
                request_id=_new_request_id(ChangeRequestType.ACTIVATE),
                kind=ChangeRequestType.ACTIVATE,
                submitter=submitter,
                tag_id=tag_id,
                reason=reason,
            )
        )

    def submit_alias_update(
        self, tag_id: str, aliases: tuple[str, ...], *, submitter: str, reason: str = ""
    ) -> ChangeRequest:
        """别名治理工单：给既有标签补同义写法（alias_json 维护同义映射）。"""
        self._dict.require(tag_id)
        if not aliases:
            raise ValueError("别名工单至少要带一个新写法")
        return self._register(
            ChangeRequest(
                request_id=_new_request_id(ChangeRequestType.UPDATE_ALIAS),
                kind=ChangeRequestType.UPDATE_ALIAS,
                submitter=submitter,
                tag_id=tag_id,
                reason=reason,
                payload={"aliases": list(aliases)},
            )
        )

    # ---- 落地 ----

    def apply(self, req: ChangeRequest, *, applied_at: datetime | None = None) -> TagDictEntry:
        """执行已通过双人复核的工单，真正改字典。

        :raises PermissionError: 未满足双人复核（谁也不能直接改字典）
        :raises TransitionError: 状态流转非法
        """
        if req.state is ChangeRequestState.REJECTED:
            raise PermissionError(f"工单 {req.request_id} 已被驳回，不能执行")
        if req.state is ChangeRequestState.APPLIED:
            raise PermissionError(f"工单 {req.request_id} 已执行过，拒绝重复执行")
        if not req.approved:
            raise PermissionError(
                f"工单 {req.request_id} 只有 {len(req.approvals)} 个复核，"
                f"需要 {DICT_CHANGE_REVIEWER_COUNT} 人复核才能改字典"
            )

        reviewers = req.approvals[:DICT_CHANGE_REVIEWER_COUNT]
        if req.kind is ChangeRequestType.CREATE:
            entry = TagDictEntry(
                tag_id=req.tag_id,
                tag_name=str(req.payload["tag_name"]),
                category=TagCategory(str(req.payload["category"])),
                depth=3,
                parent_tag_id=str(req.payload["parent_tag_id"]),
                tag_level=TagLevel(str(req.payload["tag_level"])),
                aliases=tuple(req.payload.get("aliases") or ()),  # type: ignore[arg-type]
                status=TagStatus.ACTIVE,
                description="候选转正（审核通过后转正，可被检索、圈选、统计使用）",
                inferred_parent=True,
                change_request_id=req.request_id,
                reviewers=reviewers,
            )
            self._dict.add(entry)
            self.pool.discard(entry.tag_name)
            result = entry
        elif req.kind is ChangeRequestType.MERGE:
            self.assert_transition(req.tag_id, TagStatus.MERGED)
            assert req.target_tag_id is not None
            result = self._dict.merge_into(req.tag_id, req.target_tag_id)
            tombstone = self._dict.require(req.tag_id)
            tombstone.change_request_id = req.request_id
            tombstone.reviewers = reviewers
        elif req.kind is ChangeRequestType.DEPRECATE:
            self.assert_transition(req.tag_id, TagStatus.DEPRECATED)
            result = self._dict.set_status(req.tag_id, TagStatus.DEPRECATED)
            result.change_request_id = req.request_id
            result.reviewers = reviewers
        elif req.kind is ChangeRequestType.ACTIVATE:
            self.assert_transition(req.tag_id, TagStatus.ACTIVE)
            result = self._dict.set_status(req.tag_id, TagStatus.ACTIVE)
            result.change_request_id = req.request_id
            result.reviewers = reviewers
        elif req.kind is ChangeRequestType.UPDATE_ALIAS:
            current = self._dict.require(req.tag_id)
            new_aliases = tuple(req.payload.get("aliases") or ())  # type: ignore[arg-type]
            updated = replace(current, aliases=(*current.aliases, *new_aliases))
            updated.change_request_id = req.request_id
            updated.reviewers = reviewers
            self._dict.add(updated, replace=True)
            result = updated
        else:  # pragma: no cover - 枚举已穷尽
            raise ValueError(f"未知工单类型 {req.kind}")

        req.state = ChangeRequestState.APPLIED
        req.applied_at = applied_at or datetime.now()
        return result

    # ---- 观测 ----

    def status_counts(self) -> dict[str, int]:
        """字典四态分布，字典健康度看板的基础指标。"""
        out = {s.value: 0 for s in TagStatus}
        for entry in self._dict:
            out[entry.status.value] += 1
        return out
