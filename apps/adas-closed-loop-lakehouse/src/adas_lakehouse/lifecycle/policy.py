"""流转规则与淘汰纪律：TTL 表、NAS 四条淘汰场景、血缘保护与白名单。

来源：a14.md 第二章（五级分层进入条件）、第三章（OSS 保留期表 + NAS 淘汰纪律）。

⚠️ 原文内部存在一处口径冲突，本模块两套阈值都保留，不做"择一抹平"：

  * 第二章「五级分层模型」按**通用访问温度**给阈值：
    温 = 创建 30 天内或 30 天内有访问；冷 = 连续 90 天无访问；
    归档 = 连续 180 天无访问且过保留策略阈值。
  * 第三章「OSS 侧保留期表」按**数据类型**给阈值：
    原始数据 30 天转低频、90 天转归档、365 天可删；中间产物 90/180/365；
    数据集 180/365/永久；模型 90/180/永久；临时 7 天删。

  同一份原始数据，按第二章要 90 天才转低频，按第三章 30 天就转低频。
  第四章的案例（240GB 原始数据）走的是第三章口径（03-01 落湖 → 04-30 转低频），
  因此本项目以 **第三章按数据类型的保留期表为主规则**，第二章阈值作为
  数据类型未知时的兜底，二者分别是 ``RETENTION_SCHEDULE`` 与 ``TIER_MODEL_THRESHOLDS``。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .tiers import LifecycleStage, StorageMedia

__all__ = [
    "DataType",
    "ExpirePolicy",
    "EvictStatus",
    "EvictScenario",
    "RetentionRule",
    "RETENTION_SCHEDULE",
    "TIER_MODEL_THRESHOLDS",
    "EVICT_RULES",
    "LINEAGE_BUMP_TIERS",
    "LINEAGE_BUMP_GRACE_DAYS",
    "LINEAGE_BUMP_FLOOR_STAGE",
    "COLD_TO_ARCHIVE_NO_ACCESS_DAYS",
    "DEEP_ARCHIVE_NO_ACCESS_DAYS",
    "NAS_TRAINING_DONE_BUFFER_DAYS",
    "NAS_CHECKPOINT_KEEP_VERSIONS",
    "NAS_WATERMARK_USAGE",
    "NAS_LRU_NO_ACCESS_DAYS",
    "DELETE_TRIPLE_CONFIRM",
    "SAFETY_GATES",
    "PIPELINE_STEPS",
    "retention_for",
    "target_stage_by_age",
    "expire_policy_for",
    "media_for_stage",
    "EvictRule",
]


# --------------------------------------------------------------------------- 枚举


class DataType(Enum):
    """OSS 侧保留期按数据类型配置（原文第三章表格的五行）。

    value 是入湖字段取值，label 是原文中文表述。
    """

    RAW = ("raw", "原始数据")
    INTERMEDIATE = ("intermediate", "中间过程产物")
    DATASET = ("dataset", "数据集文件")
    MODEL = ("model", "模型文件")
    TEMP = ("temp", "临时文件")

    def __init__(self, code: str, label: str) -> None:
        self._code = code
        self._label = label

    @property
    def code(self) -> str:
        return self._code

    @property
    def label(self) -> str:
        return self._label

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"{self._code}({self._label})"

    @classmethod
    def parse(cls, code: str) -> DataType:
        """按字段取值反查；未知类型抛 ValueError，绝不静默当成 raw 处理。"""
        for item in cls:
            if item.code == code:
                return item
        raise ValueError(
            f"未知数据类型 {code!r}；可选 {[i.code for i in cls]}（原文第三章保留期表五行）"
        )


class ExpirePolicy(str, Enum):
    """保留策略，落 dwd_closed_loop_storage_lifecycle.expire_policy。

    原文第四章①只举了三个例子：``raw_365d / dataset_forever / model_top_n 等``。
    带 ⚠️ 的两项是本项目按保留期表补齐的。
    """

    RAW_365D = "raw_365d"  # 原文原词
    DATASET_FOREVER = "dataset_forever"  # 原文原词
    MODEL_TOP_N = "model_top_n"  # 原文原词
    # ⚠️ 原文未明确，本项目设计：原文以「等」省略，按保留期表给中间产物与临时文件补名。
    INTERMEDIATE_365D = "intermediate_365d"
    TEMP_7D = "temp_7d"


class EvictStatus(str, Enum):
    """淘汰状态，落 dwd_closed_loop_storage_lifecycle.evict_status。

    取值逐字对齐原文第四章①：``none / pending / done / skipped``。
    """

    NONE = "none"
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class EvictScenario(str, Enum):
    """NAS 侧四条淘汰场景（原文第三章「四条淘汰场景，全部自动执行」）。"""

    TRAINING_DONE = "training_done"  # 训练任务完成后
    CHECKPOINT_ROTATE = "checkpoint"  # Checkpoint 产物版本轮转
    CAPACITY_WATERMARK = "watermark"  # 容量水位
    WHITELIST_EXEMPT = "whitelist"  # 白名单豁免（唯一一条「不淘汰」的场景）


# --------------------------------------------------------------------------- OSS 保留期


@dataclass(frozen=True, slots=True)
class RetentionRule:
    """一种数据类型的保留期配置（原文第三章 OSS 侧表格的一行）。

    区间语义按原文表格逐字落地::

        原始数据 | 标准 30 天 | 低频 30–90 天 | 归档 90–365 天 | 365 天后且血缘零引用

    即 ``standard_days=30`` 表示落湖后 30 天内留标准存储；``ia_until_days=90``
    表示第 30～90 天在低频；``archive_until_days=365`` 表示第 90～365 天在归档；
    ``delete_after_days=365`` 表示 365 天后满足删除条件可删。

    ``delete_after_days is None`` 表示永久保留（数据集与模型）。
    """

    data_type: DataType
    standard_days: int
    ia_until_days: int | None
    archive_until_days: int | None
    delete_after_days: int | None
    delete_condition: str
    expire_policy: ExpirePolicy
    #: 原文表格中「归档存储」列写 ``365 天+`` / ``180 天+``，即归档段无上界
    archive_open_ended: bool = False


#: OSS 侧保留期表（原文第三章，五行逐字落地，天数一个不改）。
RETENTION_SCHEDULE: dict[DataType, RetentionRule] = {
    DataType.RAW: RetentionRule(
        data_type=DataType.RAW,
        standard_days=30,  # 原文「标准存储 30 天」
        ia_until_days=90,  # 原文「低频存储 30–90 天」
        archive_until_days=365,  # 原文「归档存储 90–365 天」
        delete_after_days=365,  # 原文「365 天后且血缘零引用」
        delete_condition="365 天后且血缘零引用",
        expire_policy=ExpirePolicy.RAW_365D,
    ),
    DataType.INTERMEDIATE: RetentionRule(
        data_type=DataType.INTERMEDIATE,
        standard_days=90,  # 原文「标准存储 90 天」
        ia_until_days=180,  # 原文「低频存储 90–180 天」
        archive_until_days=365,  # 原文「归档存储 180–365 天」
        delete_after_days=365,  # 原文「365 天后且血缘零引用」
        delete_condition="365 天后且血缘零引用",
        expire_policy=ExpirePolicy.INTERMEDIATE_365D,
    ),
    DataType.DATASET: RetentionRule(
        data_type=DataType.DATASET,
        standard_days=180,  # 原文「标准存储 180 天」
        ia_until_days=365,  # 原文「低频存储 180–365 天」
        archive_until_days=None,  # 原文「归档存储 365 天+」——无上界
        delete_after_days=None,  # 原文「永久保留（非最新版本转低频/归档）」
        delete_condition="永久保留（非最新版本转低频/归档）",
        expire_policy=ExpirePolicy.DATASET_FOREVER,
        archive_open_ended=True,
    ),
    DataType.MODEL: RetentionRule(
        data_type=DataType.MODEL,
        standard_days=90,  # 原文「标准存储 90 天」
        ia_until_days=180,  # 原文「低频存储 90–180 天」
        archive_until_days=None,  # 原文「归档存储 180 天+」——无上界
        delete_after_days=None,  # 原文「永久保留（仅最新 N 版本留标准存储）」
        delete_condition="永久保留（仅最新 N 版本留标准存储）",
        expire_policy=ExpirePolicy.MODEL_TOP_N,
        archive_open_ended=True,
    ),
    DataType.TEMP: RetentionRule(
        data_type=DataType.TEMP,
        standard_days=7,  # 原文「标准存储 7 天」
        ia_until_days=None,  # 原文低频列为「—」，临时文件不降冷，直接删
        archive_until_days=None,  # 原文归档列为「—」
        delete_after_days=7,  # 原文「7 天后自动删除」
        delete_condition="7 天后自动删除",
        expire_policy=ExpirePolicy.TEMP_7D,
    ),
}


#: 第二章「五级分层模型」的通用访问温度阈值（单位：天）。
#: 数据类型未知时的兜底口径，也是分层看板上「温/冷/归档」三档的语义定义。
TIER_MODEL_THRESHOLDS: dict[LifecycleStage, int] = {
    LifecycleStage.WARM: 30,  # 原文「创建 30 天内或 30 天内有访问」
    LifecycleStage.COLD: 90,  # 原文「连续 90 天无访问」
    LifecycleStage.ARCHIVE: 180,  # 原文「连续 180 天无访问且过保留策略阈值」
}


# --------------------------------------------------------------------------- 血缘保护

#: 血缘保护：「被数据集版本或训练任务引用的数据自动提升一档保留」（原文第三章）。
#: 提升 1 档 —— 规则算出的目标分层往热的方向退一级。
LINEAGE_BUMP_TIERS: int = 1

#: ⚠️ 原文未明确，本项目设计：原文只说「自动提升一档保留」，没说提档保留能续多久。
#: 从第四章案例反推：240GB 原始数据 03-01 落湖，原始数据标准存储保留期 30 天，
#: 但案例到 04-30（落湖后 60 天）才降冷，理由写的是「30 天无访问且**提档保留期满**」，
#: 且成本表里「OSS 标准存储 2 个月 ¥58」也印证是 2 个月。
#: 60 - 30 = 30，故本项目取提档宽限期 = 30 天。
LINEAGE_BUMP_GRACE_DAYS: int = 30

#: ⚠️ 原文两处口径不一致，本项目按案例落地：
#: 第二章说归档是「连续 180 天无访问」，但第四章案例 04-30 转冷、07-29 转归档，
#: 间隔正好 90 天，案例标注也写「连续 90 天无访问，归档流转」。
#: 冷 → 归档按 90 天计（案例口径），180 天的通用阈值保留在 TIER_MODEL_THRESHOLDS 里备查。
#: 补充证据：[a5] 第八章③ 把同一处矛盾原样复制了一遍——分层表写「归档：连续 180 天无访问」，
#: 同章成本账却写「30 天无访问降冷 → 90 天无访问归档」。两篇独立给出同一个 90 天的
#: 案例口径，故本项目取 90 天不是孤证。
COLD_TO_ARCHIVE_NO_ACCESS_DAYS: int = 90

#: 血缘提档的**最热上界**：提到温层为止，再热就不提了。
#: 原文第二章把热 H1 的进入条件写死为「数据集关联活跃训练任务并**预热**」——
#: 热层是 CPFS/NAS 介质上的训练副本，只能由预热动作产生。血缘保护说的
#: 「自动提升一档保留」是在 OSS 侧的降冷序列（温 → 冷 → 归档）里往回退一格，
#: 不是把数据搬上 NAS。少了这个上界，分层档位与介质档位这两个维度就混成一个了。
LINEAGE_BUMP_FLOOR_STAGE: LifecycleStage = LifecycleStage.WARM

#: 普通归档 → 深度归档的无访问阈值（天）。
#: ⚠️ 原文两套口径的调和，本项目设计：原文第二章给归档级的进入条件是
#: 「连续 180 天无访问且过保留策略阈值」，而第四章案例走的是「连续 90 天无访问，归档流转」。
#: 与其二选一抹掉一个数字，本项目把归档级 C2 原文给的两档介质
#: （「OSS 归档 / 深度归档」）分别挂上这两个口径：
#:   · 连续 90 天无访问  → oss_archive      （案例口径，0.15x）
#:   · 连续 180 天无访问 → oss_deep_archive （第二章口径，0.05x）
#: 两个数字都活着，深度归档那 0.05x 也不再是没人调用的死常量。
#: 第二章「且过保留策略阈值」这半句一并落地：还没过保留期的不进深度归档。
DEEP_ARCHIVE_NO_ACCESS_DAYS: int = 180


# --------------------------------------------------------------------------- NAS 淘汰

#: 训练任务完成后的缓冲期（原文第三章「任务已结束且副本未被下一任务引用，
#: 7 天缓冲期后淘汰（checksum 校验通过方可释放）」）。
NAS_TRAINING_DONE_BUFFER_DAYS: int = 7

#: NAS 仅保留最新 N 个 Checkpoint 版本，默认 3（原文第三章「NAS 仅保留最新 N 个版本
#: （默认 3），历史版本转存 OSS 归档」）。
NAS_CHECKPOINT_KEEP_VERSIONS: int = 3

#: NAS 容量水位告警/淘汰线：使用率 > 80%（原文第三章；第六章预算告警线再次出现
#: 「NAS 使用率持续 > 80% 自动告警」，同一个数）。
NAS_WATERMARK_USAGE: float = 0.80

#: 水位淘汰的 LRU 口径：优先淘汰近 30 天无访问数据（原文第三章）。
#: 与 dwd_closed_loop_storage_lifecycle.access_count_30d 字段口径一致。
NAS_LRU_NO_ACCESS_DAYS: int = 30


@dataclass(frozen=True, slots=True)
class EvictRule:
    """NAS 侧一条淘汰规则（原文第三章表格的一行，rule 为原文原话）。"""

    scenario: EvictScenario
    rule: str
    #: 是否必须先做 checksum 校验才能释放副本
    require_checksum: bool = True


#: NAS 侧四条淘汰场景（原文第三章表格逐行落地）。
EVICT_RULES: dict[EvictScenario, EvictRule] = {
    EvictScenario.TRAINING_DONE: EvictRule(
        EvictScenario.TRAINING_DONE,
        "任务已结束且副本未被下一任务引用，7 天缓冲期后淘汰（checksum 校验通过方可释放）",
    ),
    EvictScenario.CHECKPOINT_ROTATE: EvictRule(
        EvictScenario.CHECKPOINT_ROTATE,
        "NAS 仅保留最新 N 个版本（默认 3），历史版本转存 OSS 归档",
    ),
    EvictScenario.CAPACITY_WATERMARK: EvictRule(
        EvictScenario.CAPACITY_WATERMARK,
        "NAS 使用率 > 80% 触发水位淘汰，按 LRU 优先淘汰近 30 天无访问数据",
    ),
    EvictScenario.WHITELIST_EXEMPT: EvictRule(
        EvictScenario.WHITELIST_EXEMPT,
        "活跃调试 / 在研迭代数据打白名单标签，不参与自动淘汰",
        require_checksum=False,
    ),
}


# --------------------------------------------------------------------------- 安全闸

#: 删除三重确认（原文第五章第一道闸：「过保留期 + 血缘零引用 + 白名单校验，
#: 三者同时满足才允许删除」）。三个条件的机读名，decision.py 逐条求值。
DELETE_TRIPLE_CONFIRM: tuple[str, str, str] = (
    "past_retention",  # 过保留期
    "zero_lineage_ref",  # 血缘零引用（lineage_ref_count == 0）
    "not_whitelisted",  # 白名单校验（whitelist_flag 为 False）
)

#: 四道安全闸（原文第五章表格，说明为原文原话）。
SAFETY_GATES: tuple[tuple[str, str], ...] = (
    ("删除三重确认", "过保留期 + 血缘零引用 + 白名单校验，三者同时满足才允许删除"),
    ("淘汰校验", "NAS 副本与 OSS 对象 checksum 一致方可清除，不一致则告警保留"),
    ("审计留痕", "所有流转 / 淘汰 / 删除操作全量登记，可追溯、可复盘"),
    ("冷数据取回", "归档数据标准恢复 ≤ 4 小时，取回后自动回升温层并重置访问计时"),
)

#: 日级调度五步闭环（原文第五章表格：步骤 / 执行者 / 关键动作，全部原话）。
PIPELINE_STEPS: tuple[tuple[str, str, str], ...] = (
    (
        "扫描决策",
        "StarRocks 定时任务（T+1）",
        "扫描生命周期状态表，按规则逐条计算，产出降冷 / 淘汰 / 删除候选清单",
    ),
    (
        "演练审计",
        "治理演练服务",
        "生成执行预览（文件清单 / 容量 / 成本变化），大批量操作需人工确认后放行",
    ),
    ("执行", "存储执行服务", "调用 OSS 生命周期 API / NAS 清理任务，限速执行，失败可断点续做"),
    ("回写", "存储执行服务", "结果回写生命周期状态表，更新成本日表"),
    (
        "告警",
        "告警服务 → 数据平台值班",
        "执行失败 / checksum 不一致 / 成本异动告警；结论反哺治理规则调优",
    ),
)


# --------------------------------------------------------------------------- 查询函数


def retention_for(data_type: DataType | str) -> RetentionRule:
    """取某数据类型的保留期规则。

    :param data_type: ``DataType`` 或其字段取值字符串（raw/intermediate/...）。
    :raises ValueError: 数据类型不在原文保留期表的五行之内。
    """
    dt = DataType.parse(data_type) if isinstance(data_type, str) else data_type
    return RETENTION_SCHEDULE[dt]


def expire_policy_for(data_type: DataType | str) -> ExpirePolicy:
    """取某数据类型的 expire_policy 字段取值。"""
    return retention_for(data_type).expire_policy


def target_stage_by_age(
    data_type: DataType | str | None,
    *,
    days_since_create: int,
    days_since_access: int,
) -> LifecycleStage:
    """按「年龄 + 无访问天数」算出规则期望的分层（尚未叠加血缘保护与白名单）。

    主规则走第三章按数据类型的保留期表；``data_type`` 为 None 时退回第二章的通用
    访问温度阈值（30 / 90 / 180 天）。两条路径都以「无访问天数」与「创建天数」中
    较严格者为准——原文温层的进入条件写的就是「创建 30 天内**或** 30 天内有访问」，
    即两者任一成立就还算温层。

    :param data_type: 数据类型；None 表示元信息缺失，走通用阈值兜底。
    :param days_since_create: 距落湖天数。
    :param days_since_access: 距最后一次访问天数（从未访问则等于 days_since_create）。
    :returns: 规则期望的 ``LifecycleStage``（不含 hot——hot 只由预热动作产生）。
    :raises ValueError: 天数为负。
    """
    if days_since_create < 0 or days_since_access < 0:
        raise ValueError(
            f"天数不能为负: days_since_create={days_since_create}, "
            f"days_since_access={days_since_access}"
        )

    if data_type is None:
        warm, cold, archive = (
            TIER_MODEL_THRESHOLDS[LifecycleStage.WARM],
            TIER_MODEL_THRESHOLDS[LifecycleStage.COLD],
            TIER_MODEL_THRESHOLDS[LifecycleStage.ARCHIVE],
        )
        if days_since_create <= warm or days_since_access <= warm:
            return LifecycleStage.WARM
        if days_since_access < cold:
            return LifecycleStage.WARM
        if days_since_access < archive:
            return LifecycleStage.COLD
        return LifecycleStage.ARCHIVE

    rule = retention_for(data_type)

    # 临时文件没有低频/归档段：过 7 天直接进删除候选（原文「7 天后自动删除」）
    if rule.ia_until_days is None and rule.archive_until_days is None:
        if days_since_create >= rule.standard_days:
            return LifecycleStage.PENDING_DELETE
        return LifecycleStage.WARM

    # 温层：创建 N 天内，或 N 天内有访问（原文第二章温层进入条件）
    if days_since_create < rule.standard_days or days_since_access < rule.standard_days:
        return LifecycleStage.WARM

    ia_until = rule.ia_until_days
    if ia_until is not None and days_since_create < ia_until:
        return LifecycleStage.COLD

    archive_until = rule.archive_until_days
    if rule.archive_open_ended or archive_until is None or days_since_create < archive_until:
        return LifecycleStage.ARCHIVE

    # 过归档段上界：进入删除候选，最终能不能删由删除三重确认说了算
    return LifecycleStage.PENDING_DELETE


def media_for_stage(stage: LifecycleStage, *, deep_archive: bool = False) -> StorageMedia | None:
    """分层 → 落点介质。``deep_archive=True`` 时归档级落深度归档。

    删除态（pending_delete / deleted）没有介质，返回 None。
    """
    from .tiers import MEDIA_OF_STAGE  # 局部导入避免循环

    if stage is LifecycleStage.ARCHIVE and deep_archive:
        return StorageMedia.OSS_DEEP_ARCHIVE
    return MEDIA_OF_STAGE.get(stage)
