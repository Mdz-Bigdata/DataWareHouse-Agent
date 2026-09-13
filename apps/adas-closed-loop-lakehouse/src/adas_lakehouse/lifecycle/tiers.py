"""五级分层模型：介质、分层状态、进入条件与单价量级。

来源：系列一第 7 篇《存储生命周期五级分层：智驾 PB 级数据成本治理实战》
（a14.md）第一、二章。

三类介质各管一段（原文第一章）::

    OSS 是数据的唯一事实源、数据湖是治理的决策中枢、NAS 只是训练加速的缓存层

流转方向是单向循环，任何实现都不得反转::

    文件落 OSS（唯一事实源）→ 元信息实时入湖 → 训练任务创建触发预热上 NAS
      → 训练高吞吐读写 → 任务结束触发淘汰回 OSS → OSS 按规则分层降冷

即「NAS 永远是借用，OSS 永远是归宿，湖仓元信息永远是账本」。

本模块只放「是什么」（枚举、常量、单价），「什么时候动」在 policy.py，
「这次动不动」在 decision.py，「花了多少」在 cost.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "StorageMedia",
    "LifecycleStage",
    "TierDefinition",
    "TIERS",
    "MEDIA_OF_STAGE",
    "STAGE_OF_MEDIA",
    "RELATIVE_PRICE",
    "NAS_RELATIVE_PRICE_RANGE",
    "COLD_HISTORY_SHARE",
    "ARCHIVE_VS_HOT_PRICE_DIVISOR",
    "SAMPLE_PRICE_YUAN_PER_GB_MONTH",
    "DAYS_PER_MONTH",
    "GB_PER_TB",
    "TB_PER_PB",
    "ARCHIVE_RESTORE_SLA_HOURS",
    "GOVERNANCE_PRINCIPLES",
    "HOT_TIER_MEDIA_LABEL",
    "STORAGE_CLASS_OF_MEDIA",
    "MEDIA_OF_STORAGE_CLASS",
    "FILE_STORAGE_CLASS_VALUES",
    "storage_class_of",
    "media_of_storage_class",
    "verify_storage_class_bridge",
    "tier_of_stage",
    "colder_than",
    "one_tier_warmer",
    "one_tier_colder",
    "verify_price_consistency",
]


# --------------------------------------------------------------------------- 枚举


class StorageMedia(str, Enum):
    """当前介质。取值逐字对齐 dwd_closed_loop_storage_lifecycle.storage_media：

    ``oss_standard / oss_ia / oss_archive / oss_deep_archive / nas``（原文第四章①）。
    """

    NAS = "nas"
    OSS_STANDARD = "oss_standard"
    OSS_IA = "oss_ia"
    OSS_ARCHIVE = "oss_archive"
    OSS_DEEP_ARCHIVE = "oss_deep_archive"

    @property
    def is_oss(self) -> bool:
        """是否为 OSS 介质（事实源侧）。NAS 只是缓存，不是事实源。"""
        return self is not StorageMedia.NAS

    @classmethod
    def parse(cls, value: str) -> StorageMedia:
        """按字段取值反查，并吃下几个书写别名。

        原文第一、二章把热层介质写作「CPFS / NAS」，而明细表
        ``storage_media`` 字段的取值域只有一个 ``nas``（原文第四章①）——
        上游把 ``cpfs`` 写进来是完全可能的，这里归一而不是报错。
        同理接受文件域 ``storage_class``（standard / infrequent / archive）的三个取值，
        映射关系见 ``MEDIA_OF_STORAGE_CLASS``。

        :raises ValueError: 既不是五个合法取值也不是已知别名。
        """
        raw = (value or "").strip().lower()
        try:
            return cls(raw)
        except ValueError:
            pass
        if raw in _MEDIA_ALIASES:
            return _MEDIA_ALIASES[raw]
        if raw in MEDIA_OF_STORAGE_CLASS:
            return MEDIA_OF_STORAGE_CLASS[raw]
        raise ValueError(
            f"未知存储介质 {value!r}；字段取值域为 {[m.value for m in cls]}"
            f"（原文第四章① storage_media），别名为 {sorted(_MEDIA_ALIASES)}"
        )


class LifecycleStage(str, Enum):
    """分层状态。取值逐字对齐 dwd_closed_loop_storage_lifecycle.lifecycle_stage：

    ``hot / warm / cold / archive / pending_delete / deleted``（原文第四章①）。
    """

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    ARCHIVE = "archive"
    PENDING_DELETE = "pending_delete"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class TierDefinition:
    """五级分层模型表的一行（原文第二章）。

    code 是原文给每一级的短代号：热 H1 / 温 H2 / 冷 C1 / 归档 C2 / 删除 D。
    """

    code: str
    label: str
    stage: LifecycleStage
    media: tuple[StorageMedia, ...]
    entry_condition: str
    typical_data: str


#: 五级分层模型（原文第二章表格逐行落地，entry_condition / typical_data 为原文原话）。
TIERS: tuple[TierDefinition, ...] = (
    TierDefinition(
        code="H1",
        label="热",
        stage=LifecycleStage.HOT,
        media=(StorageMedia.NAS,),
        entry_condition="数据集关联活跃训练任务并预热",
        typical_data="当期迭代数据集副本、训练中 Checkpoint",
    ),
    TierDefinition(
        code="H2",
        label="温",
        stage=LifecycleStage.WARM,
        media=(StorageMedia.OSS_STANDARD,),
        entry_condition="创建 30 天内或 30 天内有访问",
        typical_data="活跃项目期采集/中间/数据集数据",
    ),
    TierDefinition(
        code="C1",
        label="冷",
        stage=LifecycleStage.COLD,
        media=(StorageMedia.OSS_IA,),
        entry_condition="连续 90 天无访问",
        typical_data="历史数据集版本、已完结项目中间产物",
    ),
    TierDefinition(
        code="C2",
        label="归档",
        stage=LifecycleStage.ARCHIVE,
        media=(StorageMedia.OSS_ARCHIVE, StorageMedia.OSS_DEEP_ARCHIVE),
        entry_condition="连续 180 天无访问且过保留策略阈值",
        typical_data="需合规留痕、极少取回的原始数据",
    ),
    TierDefinition(
        code="D",
        label="删除",
        stage=LifecycleStage.PENDING_DELETE,
        media=(),
        entry_condition="过保留期 + 下游血缘引用数为 0",
        typical_data="临时文件、质检弃置数据",
    ),
)

#: 分层 → 该分层的默认落点介质。归档级原文给了「归档 / 深度归档」两档，
#: 默认取 oss_archive（案例第 6 个快照点用的就是它）。
MEDIA_OF_STAGE: dict[LifecycleStage, StorageMedia] = {
    LifecycleStage.HOT: StorageMedia.NAS,
    LifecycleStage.WARM: StorageMedia.OSS_STANDARD,
    LifecycleStage.COLD: StorageMedia.OSS_IA,
    LifecycleStage.ARCHIVE: StorageMedia.OSS_ARCHIVE,
}

#: 介质 → 分层（MEDIA_OF_STAGE 的逆向，深度归档也归到 archive 级）。
STAGE_OF_MEDIA: dict[StorageMedia, LifecycleStage] = {
    StorageMedia.NAS: LifecycleStage.HOT,
    StorageMedia.OSS_STANDARD: LifecycleStage.WARM,
    StorageMedia.OSS_IA: LifecycleStage.COLD,
    StorageMedia.OSS_ARCHIVE: LifecycleStage.ARCHIVE,
    StorageMedia.OSS_DEEP_ARCHIVE: LifecycleStage.ARCHIVE,
}

#: 热层介质在原文里的写法是「CPFS / NAS」（第一、二章两处表格都是这个写法），
#: 但明细表 ``storage_media`` 字段只给了一个取值 ``nas``（第四章①）。
#: 文案用这个标签，字段一律用 ``StorageMedia.NAS.value``。
HOT_TIER_MEDIA_LABEL: str = "CPFS / NAS"

#: ``storage_media`` 的书写别名 → 字段取值。CPFS 与 NAS 在原文里是同一档介质。
_MEDIA_ALIASES: dict[str, StorageMedia] = {
    "cpfs": StorageMedia.NAS,
    "cpfs/nas": StorageMedia.NAS,
    "cpfs / nas": StorageMedia.NAS,
    "oss_infrequent": StorageMedia.OSS_IA,
    "oss_standard_ia": StorageMedia.OSS_IA,
}


# --------------------------------------------------------------- 文件域 storage_class 桥

#: 文件域 ``storage_class`` 的三个取值（[a8] 第五章点名的三个关键字段之一：
#: 「storage_class 对接存储生命周期管理——标准 / 低频 / 归档的降冷策略，
#: 正是系列一收官篇讲过的五级分层在文件域的应用」）。
#: 权威定义在 ``ingest.oss.StorageClass``，这里只登记取值，由
#: ``verify_storage_class_bridge()`` 负责核对两边没有漂移。
FILE_STORAGE_CLASS_VALUES: tuple[str, str, str] = ("standard", "infrequent", "archive")

#: 介质 → 文件域 storage_class。NAS 不是 OSS 的存储级别，映射为 None——
#: 「热」在原文里是 CPFS/NAS 介质，不是 OSS 的一档降冷策略，这正是
#: ``ingest.oss.StorageClass`` 只有三个值而介质有五个值的原因。
#: ⚠️ 原文未明确：深度归档在文件域没有独立取值（[a8] 只给了标准/低频/归档三档），
#: 本项目把 oss_deep_archive 也映射到 archive——文件域只表达「降冷到归档」，
#: 归档里再分普通/深度是 OSS 侧的实现细节。
STORAGE_CLASS_OF_MEDIA: dict[StorageMedia, str | None] = {
    StorageMedia.NAS: None,
    StorageMedia.OSS_STANDARD: "standard",
    StorageMedia.OSS_IA: "infrequent",
    StorageMedia.OSS_ARCHIVE: "archive",
    StorageMedia.OSS_DEEP_ARCHIVE: "archive",
}

#: 文件域 storage_class → 介质（反向，归档默认落普通归档而非深度归档）。
MEDIA_OF_STORAGE_CLASS: dict[str, StorageMedia] = {
    "standard": StorageMedia.OSS_STANDARD,
    "infrequent": StorageMedia.OSS_IA,
    "archive": StorageMedia.OSS_ARCHIVE,
}


def storage_class_of(media: StorageMedia | str) -> str | None:
    """介质 → 文件域 ``storage_class`` 取值。

    降冷执行完成后要把新的 storage_class 回写到文件域元信息
    （``ods_data_file_meta.storage_class``），下游门禁与成本口径才对得上。

    :param media: ``StorageMedia`` 或其字段取值/别名。
    :returns: standard / infrequent / archive；NAS 返回 None（NAS 不是 OSS 存储级别）。
    """
    m = media if isinstance(media, StorageMedia) else StorageMedia.parse(media)
    return STORAGE_CLASS_OF_MEDIA[m]


def media_of_storage_class(storage_class: str) -> StorageMedia:
    """文件域 ``storage_class`` → 介质。

    入湖侧只知道 standard/infrequent/archive，生命周期扫描要的是五值介质——
    这个函数是两者之间唯一的翻译口。

    :raises ValueError: 取值不在 [a8] 给的三档之内。
    """
    key = (storage_class or "").strip().lower()
    try:
        return MEDIA_OF_STORAGE_CLASS[key]
    except KeyError as exc:
        raise ValueError(
            f"未知 storage_class {storage_class!r}；[a8] 给定三档 {FILE_STORAGE_CLASS_VALUES}"
        ) from exc


def verify_storage_class_bridge() -> dict[str, object]:
    """核对本模块的 storage_class 桥与 ``ingest.oss.StorageClass`` 没有漂移。

    入湖子系统按 [a8] 定义了 ``StorageClass``（取值 + 中文名 + 成本系数），
    生命周期子系统按 [a14] 定义了 ``StorageMedia`` + ``RELATIVE_PRICE``。
    两套定义必须对得上，否则「入湖时标 infrequent、治理时按标准存储计价」这种
    错位会让成本看板整体失真。

    入湖子系统不可用时（未安装/未装配）返回 ``available=False`` 而不是抛异常——
    本模块不该对别的子系统产生硬依赖。

    :returns: 逐档对照结果与是否一致。
    """
    try:
        from ..ingest.oss import StorageClass  # 局部导入：不制造包级硬依赖
    except Exception as exc:  # pragma: no cover - 入湖子系统缺失时的降级路径
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    upstream = tuple(sc.value for sc in StorageClass)
    checks: list[dict[str, object]] = []
    for sc in StorageClass:
        media = media_of_storage_class(sc.value)
        checks.append(
            {
                "storage_class": sc.value,
                "media": media.value,
                "roundtrip_ok": storage_class_of(media) == sc.value,
                "upstream_cost_factor": sc.cost_factor,
                "lifecycle_relative_price": RELATIVE_PRICE[media],
                "cost_factor_match": abs(sc.cost_factor - RELATIVE_PRICE[media]) < 1e-9,
            }
        )
    return {
        "available": True,
        "values_match": upstream == FILE_STORAGE_CLASS_VALUES,
        "upstream_values": upstream,
        "checks": checks,
        "all_consistent": upstream == FILE_STORAGE_CLASS_VALUES
        and all(c["roundtrip_ok"] and c["cost_factor_match"] for c in checks),
        "note": "NAS 没有对应的 storage_class——热层是介质维度，不是 OSS 的降冷档位。",
    }


#: 温度序：数值越大越冷。降冷 = 往数值大的方向走，取回/预热 = 往数值小的方向走。
#: ⚠️ 原文未明确：原文只给了五级的先后顺序，未给数值刻度，此处是本项目的实现手段。
_TEMPERATURE_ORDER: tuple[LifecycleStage, ...] = (
    LifecycleStage.HOT,
    LifecycleStage.WARM,
    LifecycleStage.COLD,
    LifecycleStage.ARCHIVE,
    LifecycleStage.PENDING_DELETE,
    LifecycleStage.DELETED,
)


# --------------------------------------------------------------------------- 单价

#: 相对 OSS 标准存储的单价量级（原文第二章第二张表，「示意值，以云厂商实际计价为准」）。
#: NAS 原文给的是区间「约 8–10x」，此处取区间下界 8.0 作为点估计，
#: 完整区间见 NAS_RELATIVE_PRICE_RANGE。
RELATIVE_PRICE: dict[StorageMedia, float] = {
    StorageMedia.NAS: 8.0,  # 原文「约 8–10x」
    StorageMedia.OSS_STANDARD: 1.0,  # 原文「1x（基准）」
    StorageMedia.OSS_IA: 0.5,  # 原文「约 0.5x」
    StorageMedia.OSS_ARCHIVE: 0.15,  # 原文「约 0.15x」
    StorageMedia.OSS_DEEP_ARCHIVE: 0.05,  # 原文「约 0.05x」
}

#: CPFS / NAS 单价量级区间「约 8–10x」（原文第二章表；引言另有「NAS 单价约为 OSS
#: 标准存储的 8–10 倍」，同一组数字）。
NAS_RELATIVE_PRICE_RANGE: tuple[float, float] = (8.0, 10.0)

#: 原文引言：「超过 80% 的历史数据（旧版本数据集、完结项目产物）几乎不再被访问」。
#: 这是立项依据，不是判定阈值——判定阈值在 policy.py。
COLD_HISTORY_SHARE: float = 0.80

#: 原文第二章：「同一份数据，放在归档层的价格只有热层的 1/50 甚至更低」。
ARCHIVE_VS_HOT_PRICE_DIVISOR: float = 50.0

#: 案例示例单价（原文第四章案例：「示例单价：NAS 1.0 元/GB·月，
#: OSS 标准 0.12 / 低频 0.06 / 归档 0.018 元/GB·月」）。
#: 深度归档原文未给示例单价，按 0.05x 相对量级换算，见下方 ⚠️。
SAMPLE_PRICE_YUAN_PER_GB_MONTH: dict[StorageMedia, float] = {
    StorageMedia.NAS: 1.0,
    StorageMedia.OSS_STANDARD: 0.12,
    StorageMedia.OSS_IA: 0.06,
    StorageMedia.OSS_ARCHIVE: 0.018,
    # ⚠️ 原文未明确，本项目设计：原文案例只给了四档示例单价，深度归档按第二章的
    # 0.05x 相对量级 × 标准存储 0.12 折算 = 0.006 元/GB·月。
    StorageMedia.OSS_DEEP_ARCHIVE: 0.12 * 0.05,
}

#: ⚠️ 原文未明确，本项目设计：按 30 天/月折算月费到天。
#: 依据是案例自身可反推——NAS「占用 9 天 ¥72」，240GB × 1.0 元/GB·月 × 9/30 = 72，
#: 只有按 30 天/月折算才能对上 ¥72 这个数。
DAYS_PER_MONTH: int = 30

#: ⚠️ 原文未明确，本项目设计：容量单位按二进制换算（1 TB = 1024 GB，1 PB = 1024 TB）。
#: 原文混用 GB / TB / PB（240GB、210TB、1.8PB）但未声明进制。
GB_PER_TB: int = 1024
TB_PER_PB: int = 1024

#: 归档数据取回的标准恢复时长上限（原文第三章「归档数据取回的标准恢复 ≤ 4 小时」，
#: 第五章第四道闸再次确认「标准恢复 ≤ 4 小时，取回后自动回升温层并重置访问计时」）。
ARCHIVE_RESTORE_SLA_HOURS: int = 4

#: 治理方案的四条原则（原文第二章原话）。规则调优时的宪法，任何新规则都要能挂到其中一条。
GOVERNANCE_PRINCIPLES: tuple[tuple[str, str], ...] = (
    ("元信息驱动", "一切决策由湖仓表计算得出，不依赖人工判断"),
    ("分层存储", "按访问温度匹配介质价格"),
    ("血缘保护", "被活跃数据集或训练任务引用的数据禁止删除、暂缓降冷"),
    ("成本稳态", "增量入湖与存量下沉动态对冲"),
)


# --------------------------------------------------------------------------- 工具


def tier_of_stage(stage: LifecycleStage) -> TierDefinition:
    """取某个分层状态对应的五级分层定义。

    ``deleted`` 是 ``pending_delete`` 执行完成后的终态，共用 D 级定义。

    :raises KeyError: 传入了不属于五级模型的状态。
    """
    target = LifecycleStage.PENDING_DELETE if stage is LifecycleStage.DELETED else stage
    for tier in TIERS:
        if tier.stage is target:
            return tier
    raise KeyError(f"未知分层状态: {stage!r}")


def colder_than(left: LifecycleStage, right: LifecycleStage) -> bool:
    """left 是否比 right 更冷（更便宜、更难取回）。"""
    return _TEMPERATURE_ORDER.index(left) > _TEMPERATURE_ORDER.index(right)


def one_tier_warmer(
    stage: LifecycleStage, *, floor: LifecycleStage | None = None
) -> LifecycleStage:
    """往热的方向退一档。用于「血缘保护：被引用的数据自动提升一档保留」与归档取回。

    已经是 hot 则原样返回。

    :param floor: 最热只能退到这一档，再热就不退了。血缘提档必须传
        ``LifecycleStage.WARM``——**提档是生命周期档位上的动作，不是介质动作**：
        热层 H1 的定义是「数据集关联活跃训练任务并预热」到 CPFS/NAS，
        只能由预热动作产生；被引用的温层数据再怎么提档也不会自己跳上 NAS，
        否则就把「分层档位」与「介质档位」这两个维度混成了一个。
    """
    idx = _TEMPERATURE_ORDER.index(stage)
    warmest = 0 if floor is None else _TEMPERATURE_ORDER.index(floor)
    return _TEMPERATURE_ORDER[max(warmest, idx - 1)]


def one_tier_colder(stage: LifecycleStage) -> LifecycleStage:
    """往冷的方向降一档。到 pending_delete 为止，不会自己走到 deleted——
    deleted 只能由删除三重确认通过后的执行动作写入。
    """
    idx = _TEMPERATURE_ORDER.index(stage)
    stop = _TEMPERATURE_ORDER.index(LifecycleStage.PENDING_DELETE)
    return _TEMPERATURE_ORDER[min(stop, idx + 1)]


def verify_price_consistency() -> dict[str, object]:
    """核对原文两套价格口径（相对量级 vs 案例示例单价）是否自洽。

    原文在第二章给相对量级、在第四章给示例单价，两处是否互相打架，值得先对一遍账：

    ====================  ==========================  ==================
    口径                   计算                         原文说法
    ====================  ==========================  ==================
    NAS / 标准             1.0 / 0.12 = 8.33x          约 8–10x ✅
    低频 / 标准            0.06 / 0.12 = 0.5x          约 0.5x ✅
    归档 / 标准            0.018 / 0.12 = 0.15x        约 0.15x ✅
    归档 / 热              0.018 / 1.0 = 1/55.6        1/50 甚至更低 ✅
    ====================  ==========================  ==================

    :returns: 每项的实测值、原文说法与是否一致（``consistent`` 全 True 表示两套口径自洽）。
    """
    std = SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.OSS_STANDARD]
    nas = SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.NAS]
    ia = SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.OSS_IA]
    arc = SAMPLE_PRICE_YUAN_PER_GB_MONTH[StorageMedia.OSS_ARCHIVE]

    nas_ratio = nas / std
    lo, hi = NAS_RELATIVE_PRICE_RANGE
    checks: list[dict[str, object]] = [
        {
            "item": "NAS / OSS标准",
            "measured": nas_ratio,
            "source_claim": "约 8–10x",
            "consistent": lo <= nas_ratio <= hi,
        },
        {
            "item": "OSS低频 / OSS标准",
            "measured": ia / std,
            "source_claim": "约 0.5x",
            "consistent": abs(ia / std - RELATIVE_PRICE[StorageMedia.OSS_IA]) < 1e-9,
        },
        {
            "item": "OSS归档 / OSS标准",
            "measured": arc / std,
            "source_claim": "约 0.15x",
            "consistent": abs(arc / std - RELATIVE_PRICE[StorageMedia.OSS_ARCHIVE]) < 1e-9,
        },
        {
            "item": "OSS归档 / NAS热层",
            "measured": nas / arc,
            "source_claim": "1/50 甚至更低",
            "consistent": nas / arc >= ARCHIVE_VS_HOT_PRICE_DIVISOR,
        },
    ]
    return {
        "checks": checks,
        "all_consistent": all(c["consistent"] for c in checks),
        "note": "原文第二章相对量级与第四章案例示例单价互相自洽，可放心混用；"
        "但两者都是示意值，生产必须以云厂商实际计价覆盖。",
    }
