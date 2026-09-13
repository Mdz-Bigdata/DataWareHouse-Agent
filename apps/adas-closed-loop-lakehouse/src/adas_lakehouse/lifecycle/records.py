"""两张治理表的行对象：明细表快照 + 成本日表行。

原文第四章一句话分工：

    明细表回答「这份数据现在在哪、能不能动」，成本日表回答「今天花了多少、治理省了多少」。

* ``LifecycleRecord``  ←→ ``dwd_closed_loop_storage_lifecycle``（Paimon 主键表）
* ``CostDailyRow``     ←→ ``dws_closed_loop_storage_cost_daily``（StarRocks 离线聚合）

两张表统一归入**闭环域（跨域归集）**——生命周期治理的对象横跨采集、生产、
数据资产、训练、仿真、回传六个域，任何单一业务域都覆盖不了（原文第四章）。
明细表通过 ``data_id`` 与 ``dwd_closed_loop_trace`` 直接关联，形成
「闭环追溯 + 存储状态」孪生视图。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..ids import parse_data_id
from .policy import DataType, EvictStatus, ExpirePolicy
from .tiers import (
    DAYS_PER_MONTH,
    GB_PER_TB,
    SAMPLE_PRICE_YUAN_PER_GB_MONTH,
    STAGE_OF_MEDIA,
    LifecycleStage,
    StorageMedia,
    storage_class_of,
)

__all__ = [
    "LifecycleRecord",
    "CostDailyRow",
    "LIFECYCLE_COLUMNS",
    "COST_DAILY_COLUMNS",
]


def _as_media(value: StorageMedia | str) -> StorageMedia:
    return value if isinstance(value, StorageMedia) else StorageMedia(value)


def _as_stage(value: LifecycleStage | str) -> LifecycleStage:
    return value if isinstance(value, LifecycleStage) else LifecycleStage(value)


@dataclass(slots=True)
class LifecycleRecord:
    """``dwd_closed_loop_storage_lifecycle`` 的一行：一个数据单元的当前存储状态快照。

    原文第四章①原话：「记录全闭环每个数据单元的『当前存储状态快照』，是预热、淘汰、
    降冷、删除等一切治理动作的唯一决策信息源」。

    主键 = ``(data_id, file_path)``——一个 clip 的 data_id 下可能有多份文件
    （video/pointcloud/ckpt...），粒度必须到文件路径。

    字段分两组：

    * 原文第四章①表格里点名的字段（data_id/file_path/storage_media/lifecycle_stage/
      last_access_time/access_count_30d/lineage_ref_count/whitelist_flag/expire_policy/
      preheat_task_id/evict_status）；
    * ⚠️ 原文未明确，本项目补充：``file_size_bytes`` / ``data_type`` / ``source_domain``
      / ``checksum_md5`` / ``create_time`` / ``stage_entered_at`` / ``artifact_id``。
      理由——成本日表要按「数据类型 × 来源域」聚合、保留期表按数据类型配置、
      淘汰前要校验 checksum、温层条件要「创建 30 天内」，这些字段不落表就算不出来。
    """

    # ---- 原文点名字段 ----
    data_id: str
    file_path: str
    storage_media: StorageMedia = StorageMedia.OSS_STANDARD
    lifecycle_stage: LifecycleStage = LifecycleStage.WARM
    last_access_time: datetime | None = None
    access_count_30d: int = 0
    lineage_ref_count: int = 0
    whitelist_flag: bool = False
    expire_policy: ExpirePolicy | str = ExpirePolicy.RAW_365D
    preheat_task_id: str = ""
    evict_status: EvictStatus = EvictStatus.NONE

    # ---- ⚠️ 本项目补充字段 ----
    file_size_bytes: int = 0
    data_type: DataType | None = None
    source_domain: str = ""
    checksum_md5: str = ""
    create_time: datetime | None = None
    stage_entered_at: datetime | None = None
    artifact_id: str = ""
    #: 跨域公共键。注册表 ``dwd_closed_loop_storage_lifecycle`` 有此列，
    #: 成本按项目归集时用得上（看板的「月末复盘」是按项目摊的）。
    project_code: str = ""
    #: 最近一次预热至 NAS 的时间。与 ``preheat_task_id`` 成对——
    #: 有任务 ID 没有时刻，就算不出「预热了多久」，预热命中率也没法按时段归因。
    preheat_time: datetime | None = None
    #: 最近一次降冷 / 归档流转时间。冷 → 归档要算「连续 90 天无访问」，
    #: 而「在当前分层待了多久」由它和 ``stage_entered_at`` 一起回答。
    tier_down_time: datetime | None = None

    def __post_init__(self) -> None:
        if not self.data_id:
            raise ValueError("data_id 不能为空：它是明细表主键的第一段，也是与闭环追溯表的关联键")
        if not self.file_path:
            raise ValueError("file_path 不能为空：主键第二段，一个 data_id 下可有多份文件")
        self.storage_media = _as_media(self.storage_media)
        self.lifecycle_stage = _as_stage(self.lifecycle_stage)
        if isinstance(self.evict_status, str):
            self.evict_status = EvictStatus(self.evict_status)
        if isinstance(self.data_type, str):
            self.data_type = DataType.parse(self.data_type)
        if self.access_count_30d < 0:
            raise ValueError(f"access_count_30d 不能为负: {self.access_count_30d}")
        if self.lineage_ref_count < 0:
            raise ValueError(f"lineage_ref_count 不能为负: {self.lineage_ref_count}")
        if self.file_size_bytes < 0:
            raise ValueError(f"file_size_bytes 不能为负: {self.file_size_bytes}")

    # ---- 派生量 ----

    @property
    def pk(self) -> tuple[str, str]:
        """主键元组。"""
        return (self.data_id, self.file_path)

    @property
    def storage_class(self) -> str | None:
        """当前介质对应的文件域 ``storage_class``（standard / infrequent / archive）。

        这是生命周期子系统与入湖子系统之间那根线：降冷执行完成后，
        存储执行服务要把这个值回写到 ``ods_data_file_meta.storage_class``，
        [a8] 点名的「storage_class 对接存储生命周期管理」才算真的接上。

        本表自己**不**存这一列——它是 ``storage_media`` 的函数，存两份必然漂移；
        注册表 ``dwd_closed_loop_storage_lifecycle`` 也没有这一列。

        :returns: 三档取值；当前在 NAS 时返回 None（NAS 不是 OSS 的存储级别）。
        """
        return storage_class_of(self.storage_media)

    @property
    def size_gb(self) -> float:
        """文件体积（GB，二进制换算）。"""
        return self.file_size_bytes / (1024**3)

    @property
    def size_tb(self) -> float:
        """文件体积（TB）。成本日表按 TB 聚合。"""
        return self.size_gb / GB_PER_TB

    def vehicle_code(self) -> str:
        """从 data_id 解析车码（ID 本身即信息，不用回查采集域）。

        data_id 不合法时返回空串而不抛——生命周期扫描是批量作业，
        单条脏数据不该炸掉整轮调度，脏数据由 ``validate()`` 单独报出。
        """
        try:
            return parse_data_id(self.data_id).vehicle_code
        except ValueError:
            return ""

    def days_since_create(self, now: datetime) -> int:
        """距落湖天数。create_time 缺失时按 0 处理（当作刚落湖，最保守）。"""
        if self.create_time is None:
            return 0
        return max(0, (now - self.create_time).days)

    def days_since_access(self, now: datetime) -> int:
        """连续无访问天数。从未访问过则退回「距落湖天数」。"""
        ref = self.last_access_time or self.create_time
        if ref is None:
            return 0
        return max(0, (now - ref).days)

    def monthly_cost_yuan(self, price: dict[StorageMedia, float] | None = None) -> float:
        """当前介质下的月成本（元）。

        :param price: 单价表（元/GB·月）；默认用原文第四章案例的示例单价
            NAS 1.0 / OSS 标准 0.12 / 低频 0.06 / 归档 0.018。
            生产环境务必传入云厂商实际计价。
        """
        table = price or SAMPLE_PRICE_YUAN_PER_GB_MONTH
        if self.lifecycle_stage is LifecycleStage.DELETED:
            return 0.0
        return self.size_gb * table[self.storage_media]

    def daily_cost_yuan(self, price: dict[StorageMedia, float] | None = None) -> float:
        """当日折算成本（元）。按 30 天/月折算，见 tiers.DAYS_PER_MONTH 的 ⚠️ 说明。"""
        return self.monthly_cost_yuan(price) / DAYS_PER_MONTH

    def validate(self) -> list[str]:
        """一致性自检，返回问题列表；空列表表示这条快照可以拿去做决策。"""
        problems: list[str] = []
        try:
            parse_data_id(self.data_id)
        except ValueError as exc:
            problems.append(f"data_id 不合法: {exc}")

        expected = STAGE_OF_MEDIA.get(self.storage_media)
        if expected is not None and self.lifecycle_stage not in (
            expected,
            LifecycleStage.PENDING_DELETE,
            LifecycleStage.DELETED,
        ):
            problems.append(
                f"介质与分层不匹配: storage_media={self.storage_media.value} 应对应 "
                f"lifecycle_stage={expected.value}，实际 {self.lifecycle_stage.value}"
            )
        if (
            self.lifecycle_stage is LifecycleStage.HOT
            and self.storage_media is not StorageMedia.NAS
        ):
            problems.append("hot 分层必须落 NAS——热层的定义就是『预热到 NAS 的训练副本』")
        if self.storage_media is StorageMedia.NAS and not self.checksum_md5:
            problems.append(
                "NAS 副本缺 checksum_md5：淘汰校验闸（第二道闸）无法通过，副本将永远淘汰不掉"
            )
        if (
            self.create_time is not None
            and self.last_access_time is not None
            and self.last_access_time < self.create_time
        ):
            problems.append("last_access_time 早于 create_time")
        return problems

    # ---- 序列化 ----

    def to_row(self) -> dict[str, Any]:
        """转成可直接写入 Paimon 表的一行（字段名与 DDL 列名一一对应）。"""
        return {
            "data_id": self.data_id,
            "file_path": self.file_path,
            "artifact_id": self.artifact_id,
            "project_code": self.project_code,
            "storage_media": self.storage_media.value,
            "lifecycle_stage": self.lifecycle_stage.value,
            "data_type": self.data_type.code if self.data_type else "",
            "source_domain": self.source_domain,
            "file_size_bytes": self.file_size_bytes,
            "checksum_md5": self.checksum_md5,
            "create_time": self.create_time,
            "stage_entered_at": self.stage_entered_at,
            "last_access_time": self.last_access_time,
            "access_count_30d": self.access_count_30d,
            "lineage_ref_count": self.lineage_ref_count,
            "whitelist_flag": self.whitelist_flag,
            "expire_policy": (
                self.expire_policy.value
                if isinstance(self.expire_policy, ExpirePolicy)
                else self.expire_policy
            ),
            "preheat_task_id": self.preheat_task_id,
            "preheat_time": self.preheat_time,
            "evict_status": self.evict_status.value,
            "tier_down_time": self.tier_down_time,
            "monthly_cost_yuan": round(self.monthly_cost_yuan(), 6),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> LifecycleRecord:
        """从查询结果行还原对象。缺列按默认值处理，多余列忽略。"""
        dt_raw = row.get("data_type") or None
        return cls(
            data_id=str(row["data_id"]),
            file_path=str(row["file_path"]),
            artifact_id=str(row.get("artifact_id") or ""),
            project_code=str(row.get("project_code") or ""),
            storage_media=_as_media(row.get("storage_media") or StorageMedia.OSS_STANDARD),
            lifecycle_stage=_as_stage(row.get("lifecycle_stage") or LifecycleStage.WARM),
            data_type=DataType.parse(dt_raw) if dt_raw else None,
            source_domain=str(row.get("source_domain") or ""),
            file_size_bytes=int(row.get("file_size_bytes") or 0),
            checksum_md5=str(row.get("checksum_md5") or ""),
            create_time=row.get("create_time"),
            stage_entered_at=row.get("stage_entered_at"),
            last_access_time=row.get("last_access_time"),
            access_count_30d=int(row.get("access_count_30d") or 0),
            lineage_ref_count=int(row.get("lineage_ref_count") or 0),
            whitelist_flag=bool(row.get("whitelist_flag") or False),
            expire_policy=row.get("expire_policy") or ExpirePolicy.RAW_365D,
            preheat_task_id=str(row.get("preheat_task_id") or ""),
            preheat_time=row.get("preheat_time"),
            evict_status=EvictStatus(row.get("evict_status") or EvictStatus.NONE),
            tier_down_time=row.get("tier_down_time"),
        )


#: 明细表列顺序（与 flink/sql/lifecycle_tables.sql 的 DDL 保持一致）。
#: 列集合以 ``catalog.registry`` 的 ``dwd_closed_loop_storage_lifecycle`` 为准——
#: 注册表里有而这里没有的列，写入时就是一列 NULL，下游拿不到。
LIFECYCLE_COLUMNS: tuple[str, ...] = (
    "data_id",
    "file_path",
    "artifact_id",
    "project_code",
    "storage_media",
    "lifecycle_stage",
    "data_type",
    "source_domain",
    "file_size_bytes",
    "checksum_md5",
    "create_time",
    "stage_entered_at",
    "last_access_time",
    "access_count_30d",
    "lineage_ref_count",
    "whitelist_flag",
    "expire_policy",
    "preheat_task_id",
    "preheat_time",
    "evict_status",
    "tier_down_time",
    "monthly_cost_yuan",
)


@dataclass(slots=True)
class CostDailyRow:
    """``dws_closed_loop_storage_cost_daily`` 的一行。

    原文第四章②：按「日期 × 介质 × 分层 × 数据类型 × 来源域」聚合容量与成本，
    支撑成本看板与预算告警。

    字段分两组：原文第四章②表格点名的，以及注册表
    ``dws_closed_loop_storage_cost_daily`` 登记、且第六章看板指标口径要用的四项
    （``file_count`` / ``baseline_cost_yuan`` / ``saved_cost_yuan`` / ``cost_mom_rate``）。
    后四项不是可选装饰：``ads_storage_cost_dashboard`` 的物化 SQL 直接
    ``SUM(baseline_cost_yuan)`` / ``SUM(saved_cost_yuan)`` / ``AVG(cost_mom_rate)``
    读本表，本表不写，看板上「成本节省额」就是一列 NULL——
    而那恰恰是原文第六章「用数字证明治理有效」的主指标。
    """

    stat_date: date
    storage_media: StorageMedia
    lifecycle_stage: LifecycleStage
    data_type: str
    source_domain: str

    total_capacity_tb: float = 0.0
    daily_cost_yuan: float = 0.0
    #: 文件数（注册表列）。容量之外还看文件数——同样 1TB，一个大文件和一百万个小文件
    #: 的治理成本完全不同
    file_count: int = 0

    #: 治理动作量全记录（原文：当日预热 / 淘汰 / 降冷 / 删除数据量）
    preheat_volume_tb: float = 0.0
    evict_volume_tb: float = 0.0
    tier_down_volume_tb: float = 0.0
    delete_volume_tb: float = 0.0

    #: 规则调优的反馈信号（原文：NAS 峰值使用率、预热命中率、归档取回次数）
    nas_peak_usage: float = 0.0
    preheat_hit_rate: float = 0.0
    archive_restore_count: int = 0

    #: 无治理基线成本（元）——原文第六章「成本节省额 = 治理释放成本 =
    #: 无治理基线成本 − 实际成本」里的被减数
    baseline_cost_yuan: float = 0.0
    #: 治理释放成本（元）= baseline_cost_yuan − daily_cost_yuan
    saved_cost_yuan: float = 0.0
    #: 成本环比增长率（原文第六章预算告警线一：> 10% 自动告警）
    cost_mom_rate: float = 0.0

    def __post_init__(self) -> None:
        self.storage_media = _as_media(self.storage_media)
        self.lifecycle_stage = _as_stage(self.lifecycle_stage)
        if isinstance(self.stat_date, datetime):
            self.stat_date = self.stat_date.date()
        for name in ("nas_peak_usage", "preheat_hit_rate"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 应为 0~1 的比率，收到 {value}")

    @property
    def pk(self) -> tuple[Any, ...]:
        """五维复合主键（原文：主键维度 = 统计日期 × 介质 × 分层 × 数据类型 × 来源域）。"""
        return (
            self.stat_date,
            self.storage_media.value,
            self.lifecycle_stage.value,
            self.data_type,
            self.source_domain,
        )

    @property
    def monthly_cost_yuan(self) -> float:
        """按当日成本外推的月成本（元）。看板「月存储成本」口径。"""
        return self.daily_cost_yuan * DAYS_PER_MONTH

    @property
    def governed_volume_tb(self) -> float:
        """当日治理动作总量（TB）= 预热 + 淘汰 + 降冷 + 删除。"""
        return (
            self.preheat_volume_tb
            + self.evict_volume_tb
            + self.tier_down_volume_tb
            + self.delete_volume_tb
        )

    def to_row(self) -> dict[str, Any]:
        """转成可写入 Paimon / StarRocks 的一行。"""
        return {
            "stat_date": self.stat_date,
            "storage_media": self.storage_media.value,
            "lifecycle_stage": self.lifecycle_stage.value,
            "data_type": self.data_type,
            "source_domain": self.source_domain,
            "file_count": self.file_count,
            "total_capacity_tb": round(self.total_capacity_tb, 6),
            "daily_cost_yuan": round(self.daily_cost_yuan, 4),
            "baseline_cost_yuan": round(self.baseline_cost_yuan, 4),
            "saved_cost_yuan": round(self.saved_cost_yuan, 4),
            "cost_mom_rate": round(self.cost_mom_rate, 6),
            "preheat_volume_tb": round(self.preheat_volume_tb, 6),
            "evict_volume_tb": round(self.evict_volume_tb, 6),
            "tier_down_volume_tb": round(self.tier_down_volume_tb, 6),
            "delete_volume_tb": round(self.delete_volume_tb, 6),
            "nas_peak_usage": round(self.nas_peak_usage, 4),
            "preheat_hit_rate": round(self.preheat_hit_rate, 4),
            "archive_restore_count": self.archive_restore_count,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CostDailyRow:
        """从查询结果行还原对象。"""
        stat = row["stat_date"]
        if isinstance(stat, str):
            stat = date.fromisoformat(stat)
        return cls(
            stat_date=stat,
            storage_media=_as_media(row["storage_media"]),
            lifecycle_stage=_as_stage(row["lifecycle_stage"]),
            data_type=str(row.get("data_type") or ""),
            source_domain=str(row.get("source_domain") or ""),
            file_count=int(row.get("file_count") or 0),
            total_capacity_tb=float(row.get("total_capacity_tb") or 0.0),
            daily_cost_yuan=float(row.get("daily_cost_yuan") or 0.0),
            baseline_cost_yuan=float(row.get("baseline_cost_yuan") or 0.0),
            saved_cost_yuan=float(row.get("saved_cost_yuan") or 0.0),
            cost_mom_rate=float(row.get("cost_mom_rate") or 0.0),
            preheat_volume_tb=float(row.get("preheat_volume_tb") or 0.0),
            evict_volume_tb=float(row.get("evict_volume_tb") or 0.0),
            tier_down_volume_tb=float(row.get("tier_down_volume_tb") or 0.0),
            delete_volume_tb=float(row.get("delete_volume_tb") or 0.0),
            nas_peak_usage=float(row.get("nas_peak_usage") or 0.0),
            preheat_hit_rate=float(row.get("preheat_hit_rate") or 0.0),
            archive_restore_count=int(row.get("archive_restore_count") or 0),
        )


#: 成本日表列顺序。列集合以 ``catalog.registry`` 的
#: ``dws_closed_loop_storage_cost_daily`` 为准——``ads_storage_cost_dashboard``
#: 的物化 SQL 直接读 baseline_cost_yuan / saved_cost_yuan / cost_mom_rate，
#: 这里少写一列，看板上就少一个指标。
COST_DAILY_COLUMNS: tuple[str, ...] = (
    "stat_date",
    "storage_media",
    "lifecycle_stage",
    "data_type",
    "source_domain",
    "file_count",
    "total_capacity_tb",
    "daily_cost_yuan",
    "baseline_cost_yuan",
    "saved_cost_yuan",
    "cost_mom_rate",
    "preheat_volume_tb",
    "evict_volume_tb",
    "tier_down_volume_tb",
    "delete_volume_tb",
    "nas_peak_usage",
    "preheat_hit_rate",
    "archive_restore_count",
)
