"""管道第二步：幂等去重 + 冲突消解。

原文第二章②：
    「标签事实表是 Paimon 主键表，联合主键（data_id/image_id, tag_id, tag_source）
      Upsert——重复写入无副作用，任务重跑不会产生重复标签」

去重只解决「同一把钥匙写了两次」，解决不了「两把钥匙说了相反的话」。
后者（跨来源冲突、同层互斥冲突）原文没给细则，本模块的消解策略标注如下：

⚠️ 原文未明确，本项目设计：冲突消解四条规则
  R1 同主键重复      → Upsert 语义，后写覆盖先写（与 Paimon 行为一致），
                        但若两条都带 confidence，取高者，避免重跑把好结果覆盖成坏结果；
  R2 跨来源同标签    → **不裁决**，三来源各存一行（主键含 tag_source，这是原文的设计），
                        检索侧需要唯一值时用 source_priority 取代表行；
  R3 同层互斥冲突    → 来源优先级 → confidence → tag_id 字典序，逐级裁决；
                        败方不删除，标 valid_flag=false + conflict_resolution，保留可追溯；
  R4 CAPTION 类别    → 不参与互斥裁决（自由文本，天然不互斥）。

R3 的「同层互斥」出处是原文第一章：「ISO 34504 / SOTIF 场景本体（吸收层级树 +
同层互斥原则）」；具体裁决顺序是本项目补的。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .dictionary import TagCategory, TagDictionary, default_dictionary
from .records import DedupKey, TagRecord
from .sources import TagSource, source_priority

__all__ = [
    "ConflictKind",
    "ConflictDecision",
    "DedupStats",
    "IdempotentBuffer",
    "ConflictResolver",
    "cross_source_pairs",
]


class ConflictKind(str, Enum):
    """冲突类型。"""

    #: 同一联合主键重复写入——Upsert 吸收，无副作用
    DUPLICATE_KEY = "duplicate_key"
    #: 不同来源给出同一个 tag_id——按设计各存一行，不算错误
    CROSS_SOURCE = "cross_source"
    #: 同一互斥组内出现多个标签——需要裁决
    MUTEX = "mutex"


@dataclass(frozen=True, slots=True)
class ConflictDecision:
    """一次冲突裁决的结论，可直接写进事实表的 ``conflict_resolution`` 列。"""

    kind: ConflictKind
    entity_id: str
    winner_key: str
    loser_keys: tuple[str, ...]
    rule: str
    reason: str

    def note_for(self, key: DedupKey) -> str:
        """给某条记录生成留痕文本。"""
        role = "winner" if str(key) == self.winner_key else "loser"
        return f"{self.kind.value}:{role}:{self.rule}:{self.reason}"


@dataclass(slots=True)
class DedupStats:
    """去重统计，用于任务日志与覆盖度指标的 ``dedup_*`` 口径。

    :param received: 进入去重环节的记录数（已通过字典映射与血缘校验的那些）
    :param upserted: 去重后落库的行数
    :param duplicate_collapsed: 被同主键 Upsert 吸收掉的重复写入次数
    :param conflict_resolved: 互斥裁决次数
    :param invalidated: 裁决落败被置 valid_flag=false 的行数
    """

    received: int = 0
    upserted: int = 0
    duplicate_collapsed: int = 0
    conflict_resolved: int = 0
    invalidated: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "upserted": self.upserted,
            "duplicate_collapsed": self.duplicate_collapsed,
            "conflict_resolved": self.conflict_resolved,
            "invalidated": self.invalidated,
        }


class IdempotentBuffer:
    """按联合主键做 Upsert 的内存缓冲——在 Flink 之外复刻 Paimon 的幂等语义。

    存在的意义：批量回补、任务重跑、单测都需要在落库之前就确定「最终会有几行」，
    而不是写进去再靠 Paimon 收敛。
    """

    __slots__ = ("_rows", "_stats")

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], TagRecord] = {}
        self._stats = DedupStats()

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows.values())

    @property
    def stats(self) -> DedupStats:
        return self._stats

    def upsert(self, record: TagRecord) -> TagRecord:
        """写入一条记录，同主键则按 R1 合并。

        :return: 主键上最终生效的那条记录
        """
        self._stats.received += 1
        key = record.dedup_key.as_tuple()
        existing = self._rows.get(key)
        if existing is None:
            self._rows[key] = record
            self._stats.upserted += 1
            return record

        self._stats.duplicate_collapsed += 1
        winner = self._pick(existing, record)
        self._rows[key] = winner
        return winner

    @staticmethod
    def _pick(existing: TagRecord, incoming: TagRecord) -> TagRecord:
        """R1：同主键重复的取舍。两条都有 confidence 时取高者，否则后写覆盖先写。"""
        if existing.confidence is not None and incoming.confidence is not None:
            return incoming if incoming.confidence >= existing.confidence else existing
        return incoming

    def records(self) -> list[TagRecord]:
        return list(self._rows.values())

    def by_entity(self) -> dict[str, list[TagRecord]]:
        out: dict[str, list[TagRecord]] = {}
        for rec in self._rows.values():
            out.setdefault(rec.entity_id, []).append(rec)
        return out


class ConflictResolver:
    """冲突消解器：只处理同层互斥（R3），跨来源共存（R2）不裁决。

    :param dictionary: 提供 mutual_exclusive_group 的受控词表
    """

    __slots__ = ("_dict",)

    def __init__(self, dictionary: TagDictionary | None = None) -> None:
        self._dict = dictionary if dictionary is not None else default_dictionary()

    def resolve(self, records: list[TagRecord]) -> list[ConflictDecision]:
        """对同一实体（clip 或 image）的一组标签做互斥裁决。

        败方不会被删除——只把 ``valid_flag`` 置 False 并写 ``conflict_resolution``，
        这样「模型当时到底说了什么」永远可回溯（配合 Paimon 时间旅行）。

        :param records: 同一 entity_id 的标签记录；混入多个实体会抛错
        :return: 裁决列表；无冲突则为空
        :raises ValueError: records 跨实体
        """
        if not records:
            return []
        entities = {r.entity_id for r in records}
        if len(entities) > 1:
            raise ValueError(f"resolve() 只接受同一实体的记录，收到 {sorted(entities)}")
        entity_id = entities.pop()

        groups: dict[str, list[TagRecord]] = {}
        for rec in records:
            if rec.tag_category is TagCategory.CAPTION:
                continue  # R4：自由文本不参与互斥
            entry = self._dict.get(rec.tag_id)
            if entry is None or not entry.mutual_exclusive_group:
                continue
            groups.setdefault(entry.mutual_exclusive_group, []).append(rec)

        decisions: list[ConflictDecision] = []
        for group, members in groups.items():
            distinct_tags = {m.tag_id for m in members}
            if len(distinct_tags) < 2:
                continue  # 同一标签的多来源共存属于 R2，不算冲突
            ranked = sorted(members, key=self._rank)
            winner, losers = ranked[0], ranked[1:]
            decision = ConflictDecision(
                kind=ConflictKind.MUTEX,
                entity_id=entity_id,
                winner_key=str(winner.dedup_key),
                loser_keys=tuple(str(rec.dedup_key) for rec in losers),
                rule="source_priority>confidence>tag_id",
                reason=(
                    f"互斥组 {group} 内出现 {len(distinct_tags)} 个标签，"
                    f"按来源优先级/置信度裁定 {winner.tag_id}({winner.tag_source.value}) 生效"
                ),
            )
            decisions.append(decision)
            winner.conflict_resolution = decision.note_for(winner.dedup_key)
            for loser in losers:
                loser.valid_flag = False
                loser.conflict_resolution = decision.note_for(loser.dedup_key)
        return decisions

    @staticmethod
    def _rank(record: TagRecord) -> tuple[int, float, str]:
        """排序键：来源优先级升序 → confidence 降序 → tag_id 字典序（稳定 tie-break）。"""
        conf = record.confidence if record.confidence is not None else 1.0
        return (source_priority(record.tag_source), -conf, record.tag_id)

    def representative(self, records: list[TagRecord]) -> dict[str, TagRecord]:
        """R2 的检索侧配套：同一 tag_id 多来源共存时，挑一条代表行。

        :return: {tag_id: 代表记录}，按来源优先级 collect > rule > model
        """
        best: dict[str, TagRecord] = {}
        for rec in records:
            if not rec.valid_flag:
                continue
            cur = best.get(rec.tag_id)
            if cur is None or self._rank(rec) < self._rank(cur):
                best[rec.tag_id] = rec
        return best


def cross_source_pairs(records: list[TagRecord]) -> dict[str, tuple[TagSource, ...]]:
    """R2 观测口径：同一 tag_id 被哪些来源同时打上。

    这是「三源收口是否真的生效」的健康指标——同一个 tag_id 三来源都命中，
    说明字典映射把三套写法收到了一起，而不是各自长出一个新标签。
    """
    out: dict[str, set[TagSource]] = {}
    for rec in records:
        out.setdefault(rec.tag_id, set()).add(rec.tag_source)
    return {
        tag_id: tuple(sorted(srcs, key=lambda s: source_priority(s)))
        for tag_id, srcs in out.items()
        if len(srcs) > 1
    }
