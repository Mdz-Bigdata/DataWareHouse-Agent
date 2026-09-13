"""统一标签字典：五大类别的受控词表（dwd_mining_tag_dict_detail 的内存映射）。

原文第一章：
    「统一标签字典（dwd_mining_tag_dict_detail）是整个体系的地基。
      它把标签空间分成五大类别，类别下再支持二级分类（parent_tag_id）……
      字典上还有两个关键机制：适用粒度（tag_level 区分 clip 级 / image 级 / 可继承
      ——clip 标签可自动继承到它抽出来的每一张图片）和别名治理（alias_json 维护同义映射，
      「雨天/降雨/rain」全部指向同一个 tag_id）。」

五大类别与其二级分类、典型三级标签全部照抄原文表格（见 CATEGORY_TAXONOMY），
枚举参照的四套业界实践见 constants.REFERENCE_PRACTICES。

⚠️ 原文未明确，本项目设计（原文表格只给了「类别 → 二级分类」与「典型三级标签」两栏，
   没有逐个三级标签指定它挂在哪个二级分类下，也没给 tag_id 编码规则）：
     · tag_id 编码为 ``{CATEGORY}_{SECONDARY}_{ENTITY}`` 的大写蛇形串；
     · 每个三级标签挂到哪个二级分类，由本项目按语义指派，见各条目的 ``inferred_parent``；
     · tag_level 的具体取值（clip / image / inheritable）由本项目按标签语义指派；
     · mutual_exclusive_group（同层互斥组）取自 ISO 34504 / SOTIF 的「同层互斥原则」，
       但互斥到哪一组是本项目指派的。
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

from ..ids import content_hash
from .constants import (
    LEGACY_SCENE_TAG_TABLE,
    LEGACY_SCENE_TARGET_CATEGORY,
    TAG_CATEGORY_COUNT,
    TAG_EXPLOSION_EXAMPLE_ALIASES,
    TAG_TREE_DEPTH,
)
from .sources import TagSource

__all__ = [
    "TagCategory",
    "TagLevel",
    "TagStatus",
    "TagDictEntry",
    "TagDictionary",
    "CATEGORY_TAXONOMY",
    "SEED_ENTRIES",
    "default_dictionary",
    "normalize_tag_text",
    "slugify_tag_id",
    "legacy_scene_tag_entry",
]


# --------------------------------------------------------------------------- 枚举


class TagCategory(str, Enum):
    """标签五大类别（原文第一章表格）+ CAPTION 特殊类别。

    CAPTION 不属于「五大类别」，是原文第二章末尾的特殊约定：
    「VLM 生成的关键说明（caption）以 tag_category=CAPTION 的特殊标签写入图片标签表」。
    因此 :data:`TagCategory.controlled` 只把五大类别算作受控词表。
    """

    SCENE = "SCENE"
    ENV = "ENV"
    ROAD = "ROAD"
    PARTICIPANT = "PARTICIPANT"
    BEHAVIOR = "BEHAVIOR"
    #: 特殊类别：VLM 关键说明，不参与受控词表的同层互斥与覆盖度分子口径
    CAPTION = "CAPTION"

    @property
    def name_cn(self) -> str:
        return _CATEGORY_CN[self]

    @property
    def is_controlled(self) -> bool:
        """是否属于「五大类别受控词表」（CAPTION 不是）。"""
        return self is not TagCategory.CAPTION

    @classmethod
    def controlled(cls) -> tuple[TagCategory, ...]:
        """五大类别，顺序同原文表格。"""
        return (cls.SCENE, cls.ENV, cls.ROAD, cls.PARTICIPANT, cls.BEHAVIOR)


_CATEGORY_CN: dict[TagCategory, str] = {
    TagCategory.SCENE: "场景",
    TagCategory.ENV: "环境",
    TagCategory.ROAD: "道路",
    TagCategory.PARTICIPANT: "参与者",
    TagCategory.BEHAVIOR: "行为事件",
    TagCategory.CAPTION: "关键说明（VLM caption，特殊类别）",
}

#: 原文第一章表格逐字照抄：类别 → (二级分类, 典型三级标签)
CATEGORY_TAXONOMY: dict[TagCategory, tuple[tuple[str, ...], tuple[str, ...]]] = {
    TagCategory.SCENE: (
        ("高速", "城市", "特殊"),
        ("隧道", "收费站", "环岛", "学校路段", "无保护左转"),
    ),
    TagCategory.ENV: (
        ("天气", "光照", "路面"),
        ("大雨", "雾", "夜间", "逆光", "湿滑", "结冰"),
    ),
    TagCategory.ROAD: (
        ("道路等级", "几何", "设施"),
        ("城市快速路", "弯道", "车道线磨损", "信号灯"),
    ),
    TagCategory.PARTICIPANT: (
        ("机动车", "非机动与行人", "异常行为"),
        ("工程车", "电动两轮车", "行人横穿", "鬼探头"),
    ),
    TagCategory.BEHAVIOR: (
        ("自车行为", "风险事件", "交通流"),
        ("AEB 紧急制动", "驾驶员接管", "险肇事件", "前车急停"),
    ),
}

assert len(CATEGORY_TAXONOMY) == TAG_CATEGORY_COUNT, "五大类别常量与词表不一致"


class TagLevel(str, Enum):
    """适用粒度（原文：tag_level 区分 clip 级 / image 级 / 可继承）。"""

    CLIP = "clip"
    IMAGE = "image"
    #: 「clip 标签可自动继承到它抽出来的每一张图片」
    INHERITABLE = "inheritable"

    @property
    def inheritable_to_image(self) -> bool:
        return self is TagLevel.INHERITABLE


class TagStatus(str, Enum):
    """四态状态机（原文第三章表格，含义逐字照抄）。"""

    #: 字典未匹配的新标签进候选池，不参与正式检索与统计，等待审核
    CANDIDATE = "candidate"
    #: 审核通过后转正，可被检索、圈选、统计使用
    ACTIVE = "active"
    #: 过时标签下线，历史数据仍可按原标签回溯
    DEPRECATED = "deprecated"
    #: 重复标签并入目标标签，原名保留为别名
    MERGED = "merged"

    @property
    def meaning(self) -> str:
        return _STATUS_MEANING[self]

    @property
    def searchable(self) -> bool:
        """是否可被正式检索/圈选/统计使用——只有 active 可以。"""
        return self is TagStatus.ACTIVE


_STATUS_MEANING: dict[TagStatus, str] = {
    TagStatus.CANDIDATE: "候选：字典未匹配的新标签进候选池，不参与正式检索与统计，等待审核",
    TagStatus.ACTIVE: "生效：审核通过后转正，可被检索、圈选、统计使用",
    TagStatus.DEPRECATED: "废弃：过时标签下线，历史数据仍可按原标签回溯",
    TagStatus.MERGED: "合并：重复标签并入目标标签，原名保留为别名",
}


# --------------------------------------------------------------------------- 文本归一


#: 归一化时剔除的分隔符/标点。原文没给归一算法，见模块 docstring 的 ⚠️ 说明。
_STRIP_CHARS = set(" \t\r\n-_/\\·•.,，。、；;:：!！?？()（）[]【】{}\"'“”‘’")


def normalize_tag_text(raw: str) -> str:
    """标签写法归一：别名匹配前的预处理。

    ⚠️ 原文未明确，本项目设计：原文只说「采集标签与模型输出的写法差异在这一步归一」，
    没给具体算法。本项目的归一 = NFKC（全角转半角、兼容字符折叠）→ 小写 →
    去空白与常见标点。这样「AEB 紧急制动」「aeb紧急制动」「AEB-紧急制动」同形，
    而「雨天/降雨/rain/下雨天」这类**同义不同形**仍需靠 alias_json 治理。

    :param raw: 来源系统给的原始标签写法
    :return: 归一化文本；入参为空白时返回空串
    :raises TypeError: raw 不是字符串
    """
    if not isinstance(raw, str):
        raise TypeError(f"标签文本必须是 str，收到 {type(raw).__name__}")
    folded = unicodedata.normalize("NFKC", raw).strip().lower()
    return "".join(ch for ch in folded if ch not in _STRIP_CHARS)


def slugify_tag_id(text: str) -> str:
    """把任意标签名压成 ASCII 的 tag_id 段。

    ⚠️ 原文未明确，本项目设计：原文没给 tag_id 编码规则。本项目要求 tag_id 全 ASCII
    （下游 SQL、URL、图库节点 ID 都会直接用它），而中文名没有稳定音译方案，
    因此含非 ASCII 字符时改用内容哈希——同名恒等，天然幂等。

    >>> slugify_tag_id("urban expressway")
    'URBANEXPRESSWAY'
    """
    key = normalize_tag_text(text)
    ascii_part = "".join(ch for ch in key if ch.isascii() and ch.isalnum())
    if not key:
        raise ValueError(f"无法从 {text!r} 生成 tag_id 段：归一后为空")
    if len(ascii_part) == len(key):
        return ascii_part.upper()
    digest = content_hash(key, length=6).upper()
    return f"{ascii_part.upper()}_{digest}" if ascii_part else f"CN{digest}"


# --------------------------------------------------------------------------- 字典条目


@dataclass(slots=True)
class TagDictEntry:
    """统一标签字典的一行，字段与 dwd_mining_tag_dict_detail 一一对应。

    :param tag_id: 标准标签 ID，全局唯一，事实表外键
    :param tag_name: 标准中文名（唯一口径名）
    :param category: 五大类别之一（或 CAPTION 特殊类别）
    :param parent_tag_id: 二级分类的父标签 ID（原文：类别下再支持二级分类）
    :param depth: 层级深度 1=类别 / 2=二级分类 / 3=三级标签
    :param tag_level: 适用粒度 clip / image / inheritable
    :param aliases: 别名集合（alias_json 的内存形态），「雨天/降雨/rain」同指一个 tag_id
    :param status: 四态之一
    :param merged_into_tag_id: status=merged 时指向的目标标签
    :param mutual_exclusive_group: 同层互斥组（ISO 34504 / SOTIF 同层互斥原则）
    :param applicable_sources: 允许写入该标签的来源；空表示三来源都可以
    :param ontology_ref: 该枚举参照的业界实践
    :param inferred_parent: 该条目的父子归属是否为本项目推断（原文只给了扁平示例）
    """

    tag_id: str
    tag_name: str
    category: TagCategory
    depth: int = 3
    parent_tag_id: str | None = None
    tag_name_en: str = ""
    tag_level: TagLevel = TagLevel.INHERITABLE
    aliases: tuple[str, ...] = ()
    status: TagStatus = TagStatus.ACTIVE
    merged_into_tag_id: str | None = None
    mutual_exclusive_group: str | None = None
    applicable_sources: tuple[TagSource, ...] = ()
    ontology_ref: str = ""
    description: str = ""
    inferred_parent: bool = False
    #: 引入/变更该标签的审核工单号（原文：字典变更走标签审核流）
    change_request_id: str | None = None
    reviewers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tag_id:
            raise ValueError("tag_id 不能为空")
        if not 1 <= self.depth <= TAG_TREE_DEPTH:
            raise ValueError(
                f"{self.tag_id}: depth={self.depth} 越界，字典只有 {TAG_TREE_DEPTH} 层"
                "（类别 / 二级分类 / 三级标签）"
            )
        if self.depth > 1 and not self.parent_tag_id:
            raise ValueError(f"{self.tag_id}: depth={self.depth} 的条目必须有 parent_tag_id")
        if self.status is TagStatus.MERGED and not self.merged_into_tag_id:
            raise ValueError(f"{self.tag_id}: merged 状态必须给出 merged_into_tag_id")
        if self.status is not TagStatus.MERGED and self.merged_into_tag_id:
            raise ValueError(
                f"{self.tag_id}: 非 merged 状态不应有 merged_into_tag_id={self.merged_into_tag_id}"
            )
        # 别名去重但保序：alias_json 是要落库的，顺序稳定才好做 diff
        seen: set[str] = set()
        kept: list[str] = []
        for alias in self.aliases:
            key = normalize_tag_text(alias)
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(alias)
        self.aliases = tuple(kept)

    # ---- 派生视图 ----

    @property
    def alias_json(self) -> str:
        """落库字段 alias_json：别名数组的 JSON 文本。"""
        return json.dumps(list(self.aliases), ensure_ascii=False)

    @property
    def normalized_forms(self) -> tuple[str, ...]:
        """该标签的全部可匹配写法（标准名 + 英文名 + 别名），均已归一。"""
        raw = (self.tag_name, self.tag_name_en, *self.aliases)
        out: list[str] = []
        for text in raw:
            key = normalize_tag_text(text) if text else ""
            if key and key not in out:
                out.append(key)
        return tuple(out)

    @property
    def is_searchable(self) -> bool:
        """能否参与正式检索/圈选/统计。"""
        return self.status.searchable

    def accepts_source(self, source: TagSource) -> bool:
        """该来源能否写这个标签。applicable_sources 为空表示三来源皆可。"""
        return not self.applicable_sources or source in self.applicable_sources

    def to_row(self) -> dict[str, object]:
        """渲染成 dwd_mining_tag_dict_detail 的一行（供 Upsert）。

        列名以 catalog.registry 为准：内存里 ``applicable_sources`` 是一串
        :class:`TagSource`、``reviewers`` 是一个元组，落库分别是 ``tag_source_type``
        的逗号分隔串与 ``review_operator`` / ``reviewer_secondary`` 两列（双人复核）。
        """
        return {
            "tag_id": self.tag_id,
            "tag_name": self.tag_name,
            "tag_name_en": self.tag_name_en,
            "tag_category": self.category.value,
            "parent_tag_id": self.parent_tag_id,
            "tag_depth": self.depth,
            "tag_level": self.tag_level.value,
            "alias_json": self.alias_json,
            "tag_status": self.status.value,
            "merged_into_tag_id": self.merged_into_tag_id,
            "mutual_exclusive_group": self.mutual_exclusive_group,
            "tag_source_type": ",".join(s.value for s in self.applicable_sources),
            "ontology_ref": self.ontology_ref,
            "tag_description": self.description,
            "change_request_id": self.change_request_id,
            "review_operator": self.reviewers[0] if len(self.reviewers) > 0 else None,
            "reviewer_secondary": self.reviewers[1] if len(self.reviewers) > 1 else None,
        }


# --------------------------------------------------------------------------- 字典容器


class TagDictionary:
    """受控词表容器：tag_id 索引 + 别名倒排索引 + 类别/父子索引。

    别名倒排是防标签爆炸的关键——「雨天/降雨/rain/下雨天」四个写法在这里指向
    同一个 tag_id，检索不会「查一个漏三个」。
    """

    __slots__ = ("_by_id", "_alias_index", "_children")

    def __init__(self, entries: list[TagDictEntry] | tuple[TagDictEntry, ...] = ()) -> None:
        self._by_id: dict[str, TagDictEntry] = {}
        self._alias_index: dict[str, str] = {}
        self._children: dict[str, list[str]] = {}
        for entry in entries:
            self.add(entry)

    # ---- 写 ----

    def add(self, entry: TagDictEntry, *, replace: bool = False) -> TagDictEntry:
        """登记一个条目。

        :param replace: True 时允许覆盖同名 tag_id（字典变更流水线用）
        :raises ValueError: tag_id 重复、父标签不存在、或别名与已有标签冲突
        """
        if entry.tag_id in self._by_id and not replace:
            raise ValueError(f"tag_id 重复: {entry.tag_id}")
        if entry.parent_tag_id and entry.parent_tag_id not in self._by_id:
            raise ValueError(
                f"{entry.tag_id}: 父标签 {entry.parent_tag_id} 未登记（字典必须先父后子）"
            )
        if entry.merged_into_tag_id and entry.merged_into_tag_id not in self._by_id:
            raise ValueError(f"{entry.tag_id}: 合并目标 {entry.merged_into_tag_id} 未登记")

        if replace and entry.tag_id in self._by_id:
            self._drop_alias_keys(entry.tag_id)

        for key in entry.normalized_forms:
            owner = self._alias_index.get(key)
            if owner is not None and owner != entry.tag_id:
                if entry.status is TagStatus.MERGED:
                    # 墓碑让位：merged 条目的旧写法已经转挂到目标标签，跳过即可
                    continue
                raise ValueError(
                    f"别名冲突：{key!r} 已属于 {owner}，不能再指给 {entry.tag_id}"
                    "（同一写法只能有一个归口，否则又是标签爆炸）"
                )
            self._alias_index[key] = entry.tag_id

        self._by_id[entry.tag_id] = entry
        if entry.parent_tag_id:
            kids = self._children.setdefault(entry.parent_tag_id, [])
            if entry.tag_id not in kids:
                kids.append(entry.tag_id)
        return entry

    def _drop_alias_keys(self, tag_id: str) -> None:
        for key in [k for k, v in self._alias_index.items() if v == tag_id]:
            del self._alias_index[key]

    def set_status(
        self,
        tag_id: str,
        status: TagStatus,
        *,
        merged_into_tag_id: str | None = None,
    ) -> TagDictEntry:
        """直接改写某条目的状态。

        ⚠️ 这是低阶写操作，**不**校验状态流转是否合法——四态状态机的合法性由
        :mod:`adas_lakehouse.tags.lifecycle` 把关（原文：谁也不能直接改字典，
        变更必须走「平台工单 + 双人复核」）。

        :raises KeyError: tag_id 不存在
        """
        entry = self.require(tag_id)
        entry.status = status
        entry.merged_into_tag_id = merged_into_tag_id if status is TagStatus.MERGED else None
        return entry

    def merge_into(self, source_tag_id: str, target_tag_id: str) -> TagDictEntry:
        """把 source 标签并入 target：原名与别名整体转挂到 target。

        原文第三章 merged 态：「重复标签并入目标标签，原名保留为别名——
        治理「雨天/降雨」这类重复的正式手段」。

        实现要点：source 变成墓碑（status=merged + merged_into_tag_id），
        它名下的全部写法改挂 target，因此检索任意旧写法都会落到 target，
        不会再出现「查一个漏三个」。

        :return: 合并后的 target 条目
        :raises KeyError: 任一 tag_id 不存在
        :raises ValueError: 自合并，或 target 本身已是墓碑
        """
        if source_tag_id == target_tag_id:
            raise ValueError(f"不能把 {source_tag_id} 合并到自身")
        source = self.require(source_tag_id)
        target = self.require(target_tag_id)
        if target.status is TagStatus.MERGED:
            raise ValueError(f"合并目标 {target_tag_id} 自身已是 merged 墓碑，请先解开合并链")

        transferred = [source.tag_name, *source.aliases]
        if source.tag_name_en:
            transferred.append(source.tag_name_en)

        self._drop_alias_keys(source_tag_id)
        source.status = TagStatus.MERGED
        source.merged_into_tag_id = target_tag_id
        # 墓碑不再持有写法，避免与 target 争夺同一个别名键
        source.aliases = ()

        merged_aliases = list(target.aliases)
        for alias in transferred:
            if alias and normalize_tag_text(alias) not in {
                normalize_tag_text(a)
                for a in (target.tag_name, target.tag_name_en, *merged_aliases)
            }:
                merged_aliases.append(alias)
        target.aliases = tuple(merged_aliases)

        self._drop_alias_keys(target_tag_id)
        for key in target.normalized_forms:
            owner = self._alias_index.get(key)
            if owner is not None and owner != target_tag_id:
                raise ValueError(f"合并失败：写法 {key!r} 已被 {owner} 占用")
            self._alias_index[key] = target_tag_id
        return target

    # ---- 读 ----

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, tag_id: object) -> bool:
        return tag_id in self._by_id

    def __iter__(self):
        return iter(self._by_id.values())

    def get(self, tag_id: str) -> TagDictEntry | None:
        return self._by_id.get(tag_id)

    def require(self, tag_id: str) -> TagDictEntry:
        """取条目，不存在直接报错。"""
        entry = self._by_id.get(tag_id)
        if entry is None:
            raise KeyError(f"字典中不存在 tag_id={tag_id!r}")
        return entry

    def lookup(self, raw_tag: str) -> TagDictEntry | None:
        """按任意写法（标准名 / 英文名 / 别名）查条目，查不到返回 None。"""
        key = normalize_tag_text(raw_tag)
        if not key:
            return None
        tag_id = self._alias_index.get(key)
        return self._by_id.get(tag_id) if tag_id else None

    def resolve(self, raw_tag: str) -> TagDictEntry | None:
        """查条目并跟随 merged 链（「雨天」并入「降雨」后仍能查到目标标签）。

        :raises ValueError: merged 链成环或超过 10 跳（字典数据已损坏）
        """
        entry = self.lookup(raw_tag)
        hops = 0
        while entry is not None and entry.status is TagStatus.MERGED:
            hops += 1
            if hops > 10:
                raise ValueError(f"merged 链过长或成环，起点 {raw_tag!r}")
            nxt = self._by_id.get(entry.merged_into_tag_id or "")
            if nxt is None or nxt.tag_id == entry.tag_id:
                raise ValueError(f"{entry.tag_id} 的合并目标缺失或自指")
            entry = nxt
        return entry

    def children_of(self, tag_id: str) -> tuple[TagDictEntry, ...]:
        return tuple(self._by_id[c] for c in self._children.get(tag_id, ()))

    def by_category(
        self, category: TagCategory, *, depth: int | None = None, active_only: bool = False
    ) -> tuple[TagDictEntry, ...]:
        return tuple(
            e
            for e in self._by_id.values()
            if e.category is category
            and (depth is None or e.depth == depth)
            and (not active_only or e.is_searchable)
        )

    def inheritable_tag_ids(self) -> frozenset[str]:
        """可从 clip 继承到 image 的标签集合（原文：clip 标签可自动继承到每一张图片）。"""
        return frozenset(e.tag_id for e in self._by_id.values() if e.tag_level.inheritable_to_image)

    def mutex_peers(self, tag_id: str) -> tuple[str, ...]:
        """同一互斥组里的其他标签（同层互斥原则的判定依据）。"""
        entry = self.get(tag_id)
        if entry is None or not entry.mutual_exclusive_group:
            return ()
        return tuple(
            e.tag_id
            for e in self._by_id.values()
            if e.mutual_exclusive_group == entry.mutual_exclusive_group and e.tag_id != tag_id
        )

    def validate(self) -> list[str]:
        """字典自检，返回问题列表；空列表表示健康。"""
        problems: list[str] = []
        for entry in self._by_id.values():
            if entry.parent_tag_id and entry.parent_tag_id not in self._by_id:
                problems.append(f"{entry.tag_id}: 父标签 {entry.parent_tag_id} 缺失")
            elif entry.parent_tag_id:
                parent = self._by_id[entry.parent_tag_id]
                if parent.depth != entry.depth - 1:
                    problems.append(
                        f"{entry.tag_id}: depth={entry.depth} 与父 {parent.tag_id}"
                        f"(depth={parent.depth}) 不连续"
                    )
                if parent.category is not entry.category:
                    problems.append(
                        f"{entry.tag_id}: 类别 {entry.category.value} 与父标签"
                        f" {parent.category.value} 不一致"
                    )
            if entry.status is TagStatus.MERGED and entry.merged_into_tag_id not in self._by_id:
                problems.append(f"{entry.tag_id}: 合并目标缺失")
        return problems

    def counts_by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self._by_id.values():
            out[entry.category.value] = out.get(entry.category.value, 0) + 1
        return out


# --------------------------------------------------------------------------- 种子词表


def _cat_root(category: TagCategory, ontology: str) -> TagDictEntry:
    return TagDictEntry(
        tag_id=category.value,
        tag_name=category.name_cn,
        tag_name_en=category.value.capitalize(),
        category=category,
        depth=1,
        tag_level=TagLevel.INHERITABLE,
        ontology_ref=ontology,
        description=f"五大类别之一：{category.value} {category.name_cn}",
    )


def _secondary(
    category: TagCategory, slug: str, name_cn: str, *, mutual_exclusive_group: str | None = None
) -> TagDictEntry:
    """二级分类。名称逐字取自原文表格「二级分类」栏。"""
    return TagDictEntry(
        tag_id=f"{category.value}_{slug}",
        tag_name=name_cn,
        category=category,
        depth=2,
        parent_tag_id=category.value,
        tag_level=TagLevel.INHERITABLE,
        mutual_exclusive_group=mutual_exclusive_group,
        description=f"{category.name_cn} 的二级分类：{name_cn}",
    )


def _leaf(
    parent: TagDictEntry,
    slug: str,
    name_cn: str,
    *,
    en: str = "",
    level: TagLevel = TagLevel.INHERITABLE,
    aliases: tuple[str, ...] = (),
    mutual_exclusive_group: str | None = None,
) -> TagDictEntry:
    """三级标签。父子归属为本项目推断，故 inferred_parent=True。

    ⚠️ 原文未明确，本项目设计：mutual_exclusive_group 只给「同一维度上物理互斥」的枚举
    （主场景 高速/城市、道路等级），不给「可以叠加」的枚举（雨天 + 夜间 + 湿滑
    完全可以同时成立，原文自己举的规则例子就是「雨天 + 夜间 + 无灯路口」）。
    互斥组只影响 dedup.ConflictResolver 的裁决，不影响入库。
    """
    return TagDictEntry(
        tag_id=f"{parent.tag_id}_{slug}",
        tag_name=name_cn,
        tag_name_en=en,
        category=parent.category,
        depth=3,
        parent_tag_id=parent.tag_id,
        tag_level=level,
        aliases=aliases,
        mutual_exclusive_group=mutual_exclusive_group,
        inferred_parent=True,
        description=f"原文「典型三级标签」枚举：{name_cn}",
    )


def _build_seed() -> list[TagDictEntry]:
    """构造种子词表：五大类别 → 二级分类 → 原文列举的典型三级标签。

    二级分类与三级标签的名称逐字取自原文表格；三级标签挂到哪个二级分类下
    是本项目按语义指派（见模块 docstring 的 ⚠️）。
    """
    iso = "ISO 34504 / SOTIF 场景本体（层级树 + 同层互斥原则）"
    odd = "ODD 运行设计域（五类映射时空/道路/参与者要素）"
    octopus = "华为云八爪鱼九大类标签（自车–他车–环境三视角）"
    world_model = "端到端世界模型三级标签体系（细化粒度 + 多源产出）"

    out: list[TagDictEntry] = []

    # ---- SCENE 场景：高速 / 城市 / 特殊 ----
    scene = _cat_root(TagCategory.SCENE, odd)
    out.append(scene)
    # 同层互斥原则（ISO 34504 / SOTIF）：一个 clip 的主场景只能是高速或城市之一
    s_highway = _secondary(
        TagCategory.SCENE, "HIGHWAY", "高速", mutual_exclusive_group="SCENE_MAIN"
    )
    s_urban = _secondary(TagCategory.SCENE, "URBAN", "城市", mutual_exclusive_group="SCENE_MAIN")
    s_special = _secondary(TagCategory.SCENE, "SPECIAL", "特殊")
    out += [s_highway, s_urban, s_special]
    out += [
        _leaf(s_special, "TUNNEL", "隧道", en="tunnel"),
        _leaf(s_highway, "TOLL_STATION", "收费站", en="toll station", aliases=("收费口",)),
        _leaf(s_urban, "ROUNDABOUT", "环岛", en="roundabout", aliases=("环形交叉口",)),
        _leaf(s_urban, "SCHOOL_ZONE", "学校路段", en="school zone"),
        _leaf(
            s_urban,
            "UNPROTECTED_LEFT",
            "无保护左转",
            en="unprotected left turn",
            level=TagLevel.CLIP,
            aliases=("无保护左转弯",),
        ),
    ]

    # ---- ENV 环境：天气 / 光照 / 路面 ----
    env = _cat_root(TagCategory.ENV, octopus)
    out.append(env)
    e_weather = _secondary(TagCategory.ENV, "WEATHER", "天气")
    e_light = _secondary(TagCategory.ENV, "LIGHT", "光照")
    e_surface = _secondary(TagCategory.ENV, "SURFACE", "路面")
    out += [e_weather, e_light, e_surface]
    out += [
        # 原文开篇的标签爆炸反面教材：四个写法归一到同一个 tag_id
        _leaf(
            e_weather,
            "RAIN",
            TAG_EXPLOSION_EXAMPLE_ALIASES[0],  # 雨天
            en="rain",
            aliases=TAG_EXPLOSION_EXAMPLE_ALIASES[1:],  # 降雨 / rain / 下雨天
        ),
        _leaf(e_weather, "HEAVY_RAIN", "大雨", en="heavy rain", aliases=("暴雨",)),
        _leaf(e_weather, "FOG", "雾", en="fog", aliases=("雾天", "起雾")),
        _leaf(e_light, "NIGHT", "夜间", en="night", aliases=("夜晚", "night")),
        _leaf(e_light, "BACKLIGHT", "逆光", en="backlight", aliases=("眩光",)),
        _leaf(e_surface, "WET", "湿滑", en="wet road", aliases=("湿路面",)),
        _leaf(e_surface, "ICY", "结冰", en="icy road", aliases=("路面结冰",)),
    ]

    # ---- ROAD 道路：道路等级 / 几何 / 设施 ----
    road = _cat_root(TagCategory.ROAD, odd)
    out.append(road)
    # 同层互斥：一个 clip 的道路等级唯一（城市快速路 / 高速公路 / 主干路…）
    r_class = _secondary(TagCategory.ROAD, "CLASS", "道路等级")
    r_geometry = _secondary(TagCategory.ROAD, "GEOMETRY", "几何")
    r_facility = _secondary(TagCategory.ROAD, "FACILITY", "设施")
    out += [r_class, r_geometry, r_facility]
    out += [
        _leaf(
            r_class,
            "URBAN_EXPRESSWAY",
            "城市快速路",
            en="urban expressway",
            mutual_exclusive_group="ROAD_CLASS_LEVEL",
        ),
        _leaf(r_geometry, "CURVE", "弯道", en="curve", aliases=("曲线路段",)),
        _leaf(
            r_facility,
            "LANE_LINE_WORN",
            "车道线磨损",
            en="worn lane marking",
            level=TagLevel.IMAGE,
            aliases=("车道线模糊",),
        ),
        _leaf(
            r_facility,
            "TRAFFIC_LIGHT",
            "信号灯",
            en="traffic light",
            level=TagLevel.IMAGE,
            aliases=("红绿灯", "交通信号灯"),
        ),
    ]

    # ---- PARTICIPANT 参与者：机动车 / 非机动与行人 / 异常行为 ----
    part = _cat_root(TagCategory.PARTICIPANT, octopus)
    out.append(part)
    p_vehicle = _secondary(TagCategory.PARTICIPANT, "VEHICLE", "机动车")
    p_vru = _secondary(TagCategory.PARTICIPANT, "VRU", "非机动与行人")
    p_abnormal = _secondary(TagCategory.PARTICIPANT, "ABNORMAL", "异常行为")
    out += [p_vehicle, p_vru, p_abnormal]
    out += [
        _leaf(
            p_vehicle,
            "ENGINEERING_TRUCK",
            "工程车",
            en="engineering truck",
            level=TagLevel.IMAGE,
            aliases=("工程车辆", "施工车"),
        ),
        _leaf(
            p_vru,
            "E_TWO_WHEELER",
            "电动两轮车",
            en="electric two-wheeler",
            level=TagLevel.IMAGE,
            aliases=("电瓶车", "电动车"),
        ),
        _leaf(
            p_abnormal,
            "PEDESTRIAN_CROSSING",
            "行人横穿",
            en="pedestrian crossing road",
            level=TagLevel.IMAGE,
            aliases=("行人横穿马路",),
        ),
        _leaf(
            p_abnormal,
            "GHOST_PROBE",
            "鬼探头",
            en="ghost probe",
            level=TagLevel.IMAGE,
            aliases=("遮挡突现",),
        ),
    ]

    # ---- BEHAVIOR 行为事件：自车行为 / 风险事件 / 交通流 ----
    beh = _cat_root(TagCategory.BEHAVIOR, world_model)
    out.append(beh)
    b_ego = _secondary(TagCategory.BEHAVIOR, "EGO", "自车行为")
    b_risk = _secondary(TagCategory.BEHAVIOR, "RISK", "风险事件")
    b_traffic = _secondary(TagCategory.BEHAVIOR, "TRAFFIC", "交通流")
    out += [b_ego, b_risk, b_traffic]
    out += [
        _leaf(
            b_ego,
            "AEB",
            "AEB 紧急制动",
            en="AEB",
            level=TagLevel.CLIP,
            aliases=("AEB", "紧急制动", "AEB紧急制动"),
        ),
        _leaf(
            b_ego,
            "DRIVER_TAKEOVER",
            "驾驶员接管",
            en="driver takeover",
            level=TagLevel.CLIP,
            aliases=("人工接管", "接管"),
        ),
        _leaf(
            b_risk,
            "NEAR_MISS",
            "险肇事件",
            en="near miss",
            level=TagLevel.CLIP,
            aliases=("险些碰撞", "near miss"),
        ),
        _leaf(
            b_traffic,
            "LEAD_VEHICLE_HARD_BRAKE",
            "前车急停",
            en="lead vehicle hard brake",
            level=TagLevel.CLIP,
            aliases=("前车急刹",),
        ),
    ]

    # ---- CAPTION 特殊类别（原文第二章末）----
    out.append(
        TagDictEntry(
            tag_id=TagCategory.CAPTION.value,
            tag_name="关键说明",
            tag_name_en="caption",
            category=TagCategory.CAPTION,
            depth=1,
            tag_level=TagLevel.IMAGE,
            applicable_sources=(TagSource.MODEL,),
            ontology_ref=iso,
            description=(
                "VLM 生成的关键说明（caption）以 tag_category=CAPTION 的特殊标签"
                "写入图片标签表，并冗余一份到向量表——结构化过滤与语义检索共用一份说明"
            ),
        )
    )
    return out


#: 种子词表（原文表格的全部枚举 + 标签爆炸示例别名）
SEED_ENTRIES: tuple[TagDictEntry, ...] = tuple(_build_seed())


@lru_cache(maxsize=1)
def default_dictionary() -> TagDictionary:
    """进程级默认字典（种子词表）。测试改动请自行 ``TagDictionary(SEED_ENTRIES)``。"""
    return TagDictionary(SEED_ENTRIES)


# --------------------------------------------------------------------------- 存量归并


def legacy_scene_tag_entry(
    scene_tag_id: str,
    scene_tag_name: str,
    *,
    aliases: tuple[str, ...] = (),
    parent_tag_id: str = "SCENE_SPECIAL",
    change_request_id: str | None = None,
) -> TagDictEntry:
    """把 ``ods_scene_tag`` 的存量场景标签包装成字典条目。

    原文第三章：「既有场景标签并入字典——湖仓里早已存在的 ods_scene_tag 场景标签
    统一归入 SCENE 类别，字典是它的超集，历史存量平滑过渡而非推倒重来。」

    ⚠️ 原文未明确，本项目设计：存量标签并入时默认挂到 ``SCENE_SPECIAL``（特殊）
    二级分类下、状态置 ``active``（存量已是既成口径，不必再走候选池），
    并把原 scene_tag_id 记进别名以便历史查询按原名回溯。

    :param scene_tag_id: ods_scene_tag 的原始标签 ID，将保留为别名
    :param scene_tag_name: 原始标签名
    :param parent_tag_id: 归入的二级分类，默认 SCENE_SPECIAL
    :return: 可直接 ``TagDictionary.add`` 的条目
    """
    if not scene_tag_name.strip():
        raise ValueError(f"{LEGACY_SCENE_TAG_TABLE}: 存量标签名为空，无法并入字典")
    slug = slugify_tag_id(scene_tag_id or scene_tag_name)
    return TagDictEntry(
        tag_id=f"{LEGACY_SCENE_TARGET_CATEGORY}_LEGACY_{slug}",
        tag_name=scene_tag_name,
        category=TagCategory.SCENE,
        depth=3,
        parent_tag_id=parent_tag_id,
        tag_level=TagLevel.INHERITABLE,
        aliases=(scene_tag_id, *aliases),
        status=TagStatus.ACTIVE,
        ontology_ref=f"存量归并：{LEGACY_SCENE_TAG_TABLE}",
        description=f"{LEGACY_SCENE_TAG_TABLE} 存量场景标签，统一归入 {LEGACY_SCENE_TARGET_CATEGORY} 类别",
        inferred_parent=True,
        change_request_id=change_request_id,
    )
