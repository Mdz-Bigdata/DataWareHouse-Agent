"""PB 级大文件外置：湖仓只存元信息，文件本体留在对象存储。

来源：a14.md 第一章「三类存储介质，各管一段」。原文一句话定调::

    OSS 是数据的唯一事实源、数据湖是治理的决策中枢、NAS 只是训练加速的缓存层
    ——认清这三个定位，是理解整套生命周期方案的前提。

这个前提不是文档措辞，而是一条能被违反、因此必须被校验的工程约束。PB 级体量下，
图像与点云动辄几十 GB，**文件本体一旦进湖，湖仓立刻被撑爆、治理也就无从谈起**。
所以生命周期明细表里存的永远是「指针 + 元信息」：``file_path`` 指向对象，
``file_size_bytes`` / ``checksum_md5`` / ``data_id`` 描述对象，本体一个字节都不进湖。

本模块把第一章的三条定位落成可执行的三件事：

=========================  ====================================================
``MEDIA_ROLES``            三类介质的定位与角色（原文第一章表格逐行）
``FLOW_CYCLE``             六步单向流转链路，``check_flow_direction`` 守住方向性
``externalization_*``      外置校验与 PB 级杠杆核算（湖仓存了多少 vs 外置了多少）
=========================  ====================================================

铁律两条，本模块各有一个函数把守：

1. **NAS 永远是借用，OSS 永远是归宿**——任何动作都不能让 OSS 侧失去事实源副本，
   ``check_source_of_truth`` 负责；
2. **湖仓只存元信息**——明细表的一行里不许夹带文件本体，
   ``check_externalized`` 负责。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .records import LifecycleRecord
from .tiers import GB_PER_TB, TB_PER_PB, LifecycleStage, StorageMedia

__all__ = [
    "MediaRole",
    "MEDIA_ROLES",
    "FLOW_CYCLE",
    "LAKEHOUSE_METADATA_CONTENT",
    "EXTERNALIZED_META_FIELDS",
    "OBJECT_URI_SCHEMES",
    "LAKEHOUSE_ROW_BYTES",
    "GROWTH_SOURCES",
    "LOSS_OF_CONTROL",
    "REUSABLE_LESSONS",
    "check_externalized",
    "check_source_of_truth",
    "check_flow_direction",
    "externalization_leverage",
]


# --------------------------------------------------------------------------- 三类介质


@dataclass(frozen=True, slots=True)
class MediaRole:
    """三类存储介质中的一类（原文第一章表格的一行，三个字段全为原文原话）。"""

    media: str
    positioning: str
    role: str
    #: 该定位对应的 ``StorageMedia`` 取值；数据湖不是介质，为空元组
    storage_media: tuple[StorageMedia, ...] = ()
    #: 是不是事实源——全篇只有 OSS 是
    is_source_of_truth: bool = False


#: 三类存储介质各管一段（原文第一章表格逐行落地）。
MEDIA_ROLES: tuple[MediaRole, ...] = (
    MediaRole(
        media="OSS 对象存储",
        positioning="海量低成本 · 数据归宿",
        role="原始数据、中间产物、数据集、模型、仿真文件的最终落点与唯一事实源；"
        "支持标准/低频/归档分层降冷",
        storage_media=(
            StorageMedia.OSS_STANDARD,
            StorageMedia.OSS_IA,
            StorageMedia.OSS_ARCHIVE,
            StorageMedia.OSS_DEEP_ARCHIVE,
        ),
        is_source_of_truth=True,
    ),
    MediaRole(
        media="数据湖（DLF + Paimon）",
        positioning="元信息中枢 · 治理大脑",
        role="文件元信息、任务状态、数据集版本与血缘、生命周期状态、访问记录"
        "——一切预热/淘汰/降冷/删除决策均由湖仓元信息计算得出",
        storage_media=(),
    ),
    MediaRole(
        media="CPFS / NAS 高性能存储",
        positioning="高吞吐 · 训练加速",
        role="当前训练任务的数据集副本与 Checkpoint；单价高、仅作缓存，用毕按规则淘汰回 OSS",
        storage_media=(StorageMedia.NAS,),
    ),
)

#: 湖仓（数据湖）里存的五类内容（原文第一章「数据湖」行，逐字拆成五项）。
#: 注意五项全部是**元信息**，没有一项是文件本体——这就是大文件外置。
LAKEHOUSE_METADATA_CONTENT: tuple[str, str, str, str, str] = (
    "文件元信息",
    "任务状态",
    "数据集版本与血缘",
    "生命周期状态",
    "访问记录",
)

#: 大文件外置时湖仓必须保留的四项元信息（[a5] 第六章：「文件大小、脱敏标记、
#: 校验和、归属 data_id」）。本子系统的落点：file_size_bytes / （脱敏标记在入湖侧
#: ComplianceMarks）/ checksum_md5 / data_id。
#: 四项缺一不可的理由都在治理链路上——没有 file_size_bytes 算不出成本，
#: 没有 checksum_md5 过不了淘汰校验闸，没有 data_id 挂不进血缘也就没有删除保护。
EXTERNALIZED_META_FIELDS: tuple[str, str, str, str] = (
    "file_size_bytes",
    "compliance_marks",
    "checksum_md5",
    "data_id",
)

#: 文件本体允许落在哪些地方：对象存储 URI，或 NAS 的绝对路径（热层副本）。
#: ⚠️ 原文未明确协议清单，本项目按湖仓实际使用的对象存储协议登记。
OBJECT_URI_SCHEMES: tuple[str, ...] = ("s3://", "oss://", "obs://", "cos://", "file://")

#: ⚠️ 原文未明确，本项目估算：明细表一行元信息的字节数量级（18 列，
#: 以字符串路径为大头），用于算「外置杠杆」——湖仓存 1 行 vs 对象存储存 1 个文件。
#: 这个值只影响杠杆比的量级展示，不参与任何治理判定。
LAKEHOUSE_ROW_BYTES: int = 512


# --------------------------------------------------------------------------- 流转链路

#: 六步单向流转链路（原文第一章原话逐段切分）。
#: 「注意整条链路的方向性：NAS 永远是『借用』，OSS 永远是『归宿』，
#: 湖仓元信息永远是『账本』。」
FLOW_CYCLE: tuple[str, ...] = (
    "文件落 OSS（唯一事实源）",
    "元信息实时入湖",
    "训练任务创建触发预热上 NAS",
    "训练高吞吐读写",
    "任务结束触发淘汰回 OSS",
    "OSS 按规则分层降冷",
)

#: 数据闭环「只进不出」的六大增长源（原文引言）。
GROWTH_SOURCES: tuple[str, ...] = (
    "采集车队每天上传图像与点云",
    "量产车随规模放量持续回传",
    "每条产线执行产生中间副本",
    "每轮训练生成新的数据集版本",
    "数十 GB 的 Checkpoint",
    "仿真批量制造突发数据",
)

#: 不治理的三重失控（原文引言三条，附各自的量化依据）。
LOSS_OF_CONTROL: tuple[tuple[str, str], ...] = (
    ("高价介质被滥用", "NAS 单价约为 OSS 标准存储的 8–10 倍，训练后不淘汰，容量几个月就被打满"),
    ("冷数据占据热层", "超过 80% 的历史数据几乎不再被访问，却长期停留在高价介质上"),
    ("成本指数级增长", "存储费用随数据量同步上涨且没有下沉出口，不断挤压 GPU 等核心算力预算"),
)

#: 案例背后三条可复用的经验（原文第四章原话）。
REUSABLE_LESSONS: tuple[tuple[str, str], ...] = (
    ("快照即决策", "时间线上每个节点对应生命周期表的一次状态变更，治理服务只需扫表计算"),
    ("血缘定生死", "保留期满但引用数 > 0，三重确认把它拦在删除之外，训练永远可复现"),
    ("个体稳态即全局稳态", "单份数据「热循环 + 存量下沉」的轨迹放大到全湖，总成本收敛于稳态"),
)


# --------------------------------------------------------------------------- 校验


def check_externalized(record: LifecycleRecord) -> list[str]:
    """校验一条明细行确实是「只存元信息」的外置形态。

    查四件事：

    1. ``file_path`` 是对象存储 URI 或 NAS 绝对路径——湖仓存的是指针不是内容；
    2. 行里没有夹带文件本体（任何形如 payload/content/blob/body 的字段）；
    3. 外置必需的元信息四项齐全（[a5] 第六章）；
    4. 体积不为 0——外置的意义就是把体积留在对象存储，体积缺失说明元信息没采全，
       成本与容量会整体算少。

    :returns: 问题列表；空列表表示这条行合规。
    """
    problems: list[str] = []

    path = (record.file_path or "").strip()
    has_scheme = any(path.lower().startswith(s) for s in OBJECT_URI_SCHEMES)
    if not has_scheme and not path.startswith("/"):
        problems.append(
            f"file_path 既不是对象存储 URI（{'/'.join(OBJECT_URI_SCHEMES)}）也不是绝对路径: "
            f"{record.file_path!r}——湖仓只存指向本体的指针"
        )
    if record.storage_media.is_oss and not has_scheme:
        problems.append(
            f"介质是 {record.storage_media.value} 但 file_path 不是对象存储 URI: "
            f"{record.file_path!r}——OSS 是唯一事实源，路径必须指得回去"
        )

    for suspicious in ("payload", "content", "blob", "body", "data_bytes"):
        if hasattr(record, suspicious):
            problems.append(
                f"明细行带了疑似文件本体的字段 {suspicious!r}：几十 GB 的本体一旦进湖，"
                f"PB 级体量下湖仓立刻被撑爆"
            )

    if not record.checksum_md5 and record.storage_media is StorageMedia.NAS:
        problems.append("缺 checksum_md5：外置四项元信息之一，且淘汰校验闸靠它把关")
    if record.file_size_bytes <= 0 and record.lifecycle_stage is not LifecycleStage.DELETED:
        problems.append("file_size_bytes 为 0：外置四项元信息之一，缺它容量与成本全算不出来")
    if not record.data_id:
        problems.append("缺 data_id：外置四项元信息之一，缺它挂不进血缘，删除保护失效")

    return problems


def check_source_of_truth(
    *, from_media: StorageMedia, to_media: StorageMedia | None, action: str
) -> list[str]:
    """校验一次动作没有破坏「OSS 永远是归宿」这条铁律。

    原文第三章铁律：「淘汰 ≠ 删除。淘汰只清除 NAS 副本，OSS 始终是事实源」。
    落到检查上就是：淘汰的落点必须是 OSS 侧的某一档，不能是 None，更不能是 NAS。

    :param from_media: 动作前的介质。
    :param to_media: 动作后的介质；删除动作为 None。
    :param action: 动作名（``ActionType`` 的取值），用于措辞。
    :returns: 问题列表；空列表表示没有破坏铁律。
    """
    problems: list[str] = []
    if action == "evict":
        if from_media is not StorageMedia.NAS:
            problems.append(f"淘汰动作的起点必须是 NAS，实际是 {from_media.value}")
        if to_media is None:
            problems.append("淘汰的落点为空：淘汰 ≠ 删除，NAS 副本清除后必须落回 OSS 事实源")
        elif not to_media.is_oss:
            problems.append(f"淘汰的落点是 {to_media.value}，不是 OSS——OSS 永远是归宿")
    if action == "preheat":
        if to_media is not StorageMedia.NAS:
            problems.append(f"预热的落点必须是 NAS，实际是 {to_media.value if to_media else None}")
        if from_media is StorageMedia.NAS:
            problems.append("预热的起点已经是 NAS：重复预热，说明扫描没读到最新快照")
    if action == "tier_down" and to_media is not None and not to_media.is_oss:
        problems.append(f"降冷只在 OSS 内部逐级下沉，落点不该是 {to_media.value}")
    return problems


def check_flow_direction(
    from_media: StorageMedia, to_media: StorageMedia | None
) -> tuple[bool, str]:
    """校验一次介质变更符合第一章那条单向流转链路。

    合法的迁移只有四种：

    * OSS 任一档 → NAS：预热（链路第 3 步）；
    * NAS → OSS 标准：淘汰回归宿（链路第 5 步）；
    * OSS 内部由热到冷：分层降冷（链路第 6 步）；
    * OSS 内部由冷到热：归档取回（第五章第四道闸，链路之外的反向补丁，合法）。

    非法的是「NAS → NAS」「NAS → 归档」这类把 NAS 当归宿用的走法——
    Checkpoint 版本轮转看似 NAS → 归档，实际语义是「NAS 副本清除 +
    OSS 侧那份转归档」，仍然经过 OSS，不算反转。

    :returns: ``(是否合法, 说明)``。
    """
    if to_media is None:
        return (True, "删除动作没有落点介质，方向性不适用")
    if from_media is to_media:
        return (True, "介质未变更")
    if from_media is StorageMedia.NAS and to_media is StorageMedia.NAS:
        return (False, "NAS → NAS：NAS 永远是借用，不是归宿")
    if from_media is StorageMedia.NAS and to_media.is_oss:
        return (True, "NAS → OSS：淘汰回归宿（链路第 5 步）")
    if from_media.is_oss and to_media is StorageMedia.NAS:
        return (True, "OSS → NAS：训练预热（链路第 3 步）")
    return (True, "OSS 内部流转：分层降冷（链路第 6 步）或归档取回")


# --------------------------------------------------------------------------- PB 级核算


def externalization_leverage(records: Iterable[LifecycleRecord]) -> dict[str, object]:
    """算一算大文件外置到底省下了多大的湖仓体量——PB 级下这才是外置的意义。

    口径::

        外置本体字节 = Σ file_size_bytes         （全都躺在对象存储，一个字节不进湖）
        湖仓元信息字节 = 行数 × LAKEHOUSE_ROW_BYTES
        杠杆 = 外置本体字节 / 湖仓元信息字节

    原文第一章说数据湖是「元信息中枢」而不是数据仓库，这个比值就是那句话的量化形式：
    湖仓用 KB 级的元信息管住 PB 级的本体。

    :param records: 生命周期明细快照。
    :returns: 行数、外置体量（GB/TB/PB）、湖仓占用与杠杆倍数。
    """
    rows = list(records)
    payload_bytes = sum(max(0, r.file_size_bytes) for r in rows)
    lake_bytes = len(rows) * LAKEHOUSE_ROW_BYTES
    payload_gb = payload_bytes / (1024**3)
    return {
        "row_count": len(rows),
        "externalized_bytes": payload_bytes,
        "externalized_gb": round(payload_gb, 4),
        "externalized_tb": round(payload_gb / GB_PER_TB, 6),
        "externalized_pb": round(payload_gb / GB_PER_TB / TB_PER_PB, 9),
        "lakehouse_metadata_bytes": lake_bytes,
        "leverage_x": round(payload_bytes / lake_bytes, 2) if lake_bytes else 0.0,
        "note": (
            "湖仓只存元信息、本体放对象存储（原文第一章：OSS 是唯一事实源，"
            "数据湖是元信息中枢）。杠杆倍数越高，说明外置越彻底。"
        ),
    }
