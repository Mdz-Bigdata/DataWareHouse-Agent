"""标签覆盖度度量：dws_mining_tag_coverage_daily 的口径与低覆盖信号。

原文第四章：
    「标签的「健康度」则由 DWS 层的 dws_mining_tag_coverage_daily（标签覆盖度日指标表）
      按「日期 × 标签类别」持续统计——哪个类别覆盖率长期走低，就是定向采集的需求信号，
      标签体系反过来驱动采集策略。」

原文只给了统计维度（日期 × 标签类别）和用途（低覆盖 → 定向采集），没给指标公式，
也没给「长期走低」的判定线。以下均为本项目补的设计，逐条标注：

⚠️ 原文未明确，本项目设计：
  · 覆盖率分子只算「active 字典标签 + valid_flag=true」的记录——候选/废弃/合并态标签
    不参与正式统计（这一条有原文依据：candidate「不参与正式检索与统计」）；
  · clip 覆盖率 = 当日该类别下至少有 1 个有效标签的 clip 数 / 当日 clip 总数；
    image 覆盖率同理；
  · 低覆盖判定线 LOW_COVERAGE_RATE_THRESHOLD = 0.60；
  · 「长期」判定为连续 LOW_COVERAGE_CONSECUTIVE_DAYS = 7 天。

:class:`CoverageRow` 就是 dws_mining_tag_coverage_daily 的一行，字段名一律与
catalog.registry 里该表的列名对齐（``stat_date`` 而不是 dt，``*_count`` / ``*_ratio``
而不是 ``*_cnt`` / ``*_rate``），免得内存一套名字、落库另一套名字。registry 侧还把
「审了多少」与「审过的里有多少是对的」拆成了两列，本模块按各自口径分别算：

  · ``reviewed_tag_ratio``  = 已出审核结论 / 全部 = (过审 + 驳回) / (过审 + 驳回 + 未审)
  · ``review_pass_ratio``   = 过审 / (过审 + 驳回)——模型标签质量下滑先从这条看出来
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from ..config import settings
from .constants import COVERAGE_DIMENSIONS, COVERAGE_TABLE
from .dictionary import TagCategory, TagDictionary, TagStatus, default_dictionary
from .records import DataTagRecord, ImageTagRecord, ReviewStatus, TagRecord
from .sources import TagSource

__all__ = [
    "LOW_COVERAGE_RATE_THRESHOLD",
    "LOW_COVERAGE_CONSECUTIVE_DAYS",
    "CoverageRow",
    "LowCoverageSignal",
    "compute_daily_coverage",
    "detect_low_coverage",
    "CoverageReader",
]

#: ⚠️ 原文未明确，本项目设计：低覆盖判定线。原文只说「覆盖率长期走低」，没给数值。
#: 本项目取 0.60——一个类别有四成以上的 clip 完全没有标签时，检索与圈选已经不可用。
LOW_COVERAGE_RATE_THRESHOLD: Final[float] = 0.60

#: ⚠️ 原文未明确，本项目设计：「长期」= 连续 7 天低于判定线，滤掉单日抖动（周维度）。
LOW_COVERAGE_CONSECUTIVE_DAYS: Final[int] = 7


def _as_dt(value: date | datetime | str) -> str:
    """统一成 ``yyyy-MM-dd`` 字符串（registry 里 stat_date 是 DATE，字面量即此形态）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"stat_date 需要 date/datetime/str，收到 {type(value).__name__}")


@dataclass(slots=True)
class CoverageRow:
    """dws_mining_tag_coverage_daily 的一行：一个「日期 × 项目 × 标签类别」的健康度快照。

    字段名与 registry 里该表的列名一一对齐——这是一个落库行的内存形态，不是另一套模型。
    ``project_code`` 是该表联合主键的第二段：原文的统计维度是「日期 × 标签类别」，
    湖里按项目再分一层，不给项目就落空串（单项目部署即此形态），主键仍然完整。
    """

    stat_date: str
    tag_category: TagCategory
    project_code: str = ""
    total_data_count: int = 0
    tagged_data_count: int = 0
    total_image_count: int = 0
    tagged_image_count: int = 0
    tag_record_count: int = 0
    distinct_tag_count: int = 0
    active_tag_count: int = 0
    candidate_tag_count: int = 0
    collect_tag_count: int = 0
    rule_tag_count: int = 0
    vlm_tag_count: int = 0
    reviewed_tag_count: int = 0
    unreviewed_tag_count: int = 0
    #: 审核驳回条数。不是表里的列，但 review_pass_ratio 的分母少不了它。
    rejected_tag_count: int = 0
    conflict_invalid_count: int = 0
    avg_confidence: float | None = None

    # ---- 派生指标 ----

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        """分母为 0 时返回 0.0，绝不抛除零错（当日该类别无数据是常态）。"""
        return round(numerator / denominator, 4) if denominator else 0.0

    @property
    def data_coverage_ratio(self) -> float:
        """clip 级覆盖率 = 已打标 clip 数 / 全量 clip 数。"""
        return self._ratio(self.tagged_data_count, self.total_data_count)

    @property
    def image_coverage_ratio(self) -> float:
        return self._ratio(self.tagged_image_count, self.total_image_count)

    @property
    def reviewed_tag_ratio(self) -> float:
        """已审核标签占比：出了审核结论的（过审 + 驳回）/ 全部。答「审了多少」。"""
        judged = self.reviewed_tag_count + self.rejected_tag_count
        return self._ratio(judged, judged + self.unreviewed_tag_count)

    @property
    def review_pass_ratio(self) -> float:
        """过审率 = 过审 /（过审 + 驳回）。答「审过的里有多少是对的」。"""
        return self._ratio(
            self.reviewed_tag_count, self.reviewed_tag_count + self.rejected_tag_count
        )

    @property
    def is_low_coverage(self) -> bool:
        """单日是否低于判定线（「长期」还要看 :func:`detect_low_coverage`）。"""
        return self.data_coverage_ratio < LOW_COVERAGE_RATE_THRESHOLD

    def to_row(self) -> dict[str, Any]:
        """渲染成 DWS 表的一行（列名即 registry 列名）。"""
        return {
            "stat_date": self.stat_date,
            "project_code": self.project_code,
            "tag_category": self.tag_category.value,
            "total_data_count": self.total_data_count,
            "tagged_data_count": self.tagged_data_count,
            "data_coverage_ratio": self.data_coverage_ratio,
            "total_image_count": self.total_image_count,
            "tagged_image_count": self.tagged_image_count,
            "image_coverage_ratio": self.image_coverage_ratio,
            "tag_record_count": self.tag_record_count,
            "distinct_tag_count": self.distinct_tag_count,
            "active_tag_count": self.active_tag_count,
            "candidate_tag_count": self.candidate_tag_count,
            "collect_tag_count": self.collect_tag_count,
            "rule_tag_count": self.rule_tag_count,
            "vlm_tag_count": self.vlm_tag_count,
            "reviewed_tag_count": self.reviewed_tag_count,
            "unreviewed_tag_count": self.unreviewed_tag_count,
            "reviewed_tag_ratio": self.reviewed_tag_ratio,
            "review_pass_ratio": self.review_pass_ratio,
            "conflict_invalid_count": self.conflict_invalid_count,
            "avg_confidence": self.avg_confidence,
            "low_coverage_flag": self.is_low_coverage,
        }


@dataclass(frozen=True, slots=True)
class LowCoverageSignal:
    """低覆盖信号——原文所谓「定向采集的需求信号」。"""

    tag_category: TagCategory
    consecutive_days: int
    latest_rate: float
    worst_rate: float
    window: tuple[str, str]
    threshold: float = LOW_COVERAGE_RATE_THRESHOLD

    @property
    def message(self) -> str:
        start, end = self.window
        return (
            f"{self.tag_category.value}（{self.tag_category.name_cn}）覆盖率连续 "
            f"{self.consecutive_days} 天低于 {self.threshold}"
            f"（{start}~{end}，最新 {self.latest_rate}，最低 {self.worst_rate}）"
            "：建议转成定向采集需求"
        )


def compute_daily_coverage(
    stat_date: date | datetime | str,
    *,
    data_tags: Iterable[TagRecord] = (),
    image_tags: Iterable[TagRecord] = (),
    total_data_count: int = 0,
    total_image_count: int = 0,
    project_code: str = "",
    dictionary: TagDictionary | None = None,
    candidate_counts: dict[TagCategory, int] | None = None,
) -> list[CoverageRow]:
    """按「日期 × 标签类别」算一天的覆盖度（原文指定的统计维度）。

    分子口径：只统计 ``valid_flag=true`` 且字典状态为 active 的标签记录
    （候选态「不参与正式检索与统计」是原文明写的）。

    :param stat_date: 统计日期（落库列 stat_date）
    :param data_tags: 当日 clip 级标签记录
    :param image_tags: 当日 image 级标签记录
    :param total_data_count: 当日 clip 总数（覆盖率分母）
    :param total_image_count: 当日图片总数（覆盖率分母）
    :param project_code: 所属项目（该表联合主键的第二段）
    :param candidate_counts: 各类别的候选池积压，可选
    :return: 每个类别一行；五大类别 + CAPTION 都会出现，便于下游按固定维度对齐
    :raises ValueError: 分母为负
    """
    if total_data_count < 0 or total_image_count < 0:
        raise ValueError("clip/image 总数不能为负")
    book = dictionary if dictionary is not None else default_dictionary()
    day = _as_dt(stat_date)

    rows: dict[TagCategory, CoverageRow] = {
        cat: CoverageRow(
            stat_date=day,
            tag_category=cat,
            project_code=project_code,
            total_data_count=total_data_count,
            total_image_count=total_image_count,
            candidate_tag_count=(candidate_counts or {}).get(cat, 0),
            active_tag_count=len(book.by_category(cat, active_only=True)),
        )
        for cat in TagCategory
    }

    clip_hits: dict[TagCategory, set[str]] = {cat: set() for cat in TagCategory}
    image_hits: dict[TagCategory, set[str]] = {cat: set() for cat in TagCategory}
    distinct: dict[TagCategory, set[str]] = {cat: set() for cat in TagCategory}
    conf_sum: dict[TagCategory, list[float]] = {cat: [] for cat in TagCategory}

    def _accumulate(rec: TagRecord, is_image: bool) -> None:
        row = rows[rec.tag_category]
        if not rec.valid_flag:
            row.conflict_invalid_count += 1
            return
        entry = book.get(rec.tag_id)
        if entry is not None and entry.status is not TagStatus.ACTIVE:
            return  # 候选/废弃/合并态不参与正式统计
        row.tag_record_count += 1
        distinct[rec.tag_category].add(rec.tag_id)
        if rec.tag_source is TagSource.COLLECT:
            row.collect_tag_count += 1
        elif rec.tag_source is TagSource.RULE:
            row.rule_tag_count += 1
        else:
            row.vlm_tag_count += 1
        if rec.review_status is ReviewStatus.PENDING:
            row.unreviewed_tag_count += 1
        elif rec.review_status is ReviewStatus.REJECTED:
            row.rejected_tag_count += 1
        elif rec.review_status.passed:
            row.reviewed_tag_count += 1
        if rec.confidence is not None:
            conf_sum[rec.tag_category].append(rec.confidence)
        if is_image and isinstance(rec, ImageTagRecord):
            image_hits[rec.tag_category].add(rec.image_id)
            clip_hits[rec.tag_category].add(rec.data_id)
        elif isinstance(rec, DataTagRecord):
            clip_hits[rec.tag_category].add(rec.data_id)

    for rec in data_tags:
        _accumulate(rec, is_image=False)
    for rec in image_tags:
        _accumulate(rec, is_image=True)

    for cat, row in rows.items():
        row.tagged_data_count = len(clip_hits[cat])
        row.tagged_image_count = len(image_hits[cat])
        row.distinct_tag_count = len(distinct[cat])
        confs = conf_sum[cat]
        row.avg_confidence = round(sum(confs) / len(confs), 4) if confs else None
    return list(rows.values())


def detect_low_coverage(
    history: Sequence[CoverageRow],
    *,
    threshold: float = LOW_COVERAGE_RATE_THRESHOLD,
    consecutive_days: int = LOW_COVERAGE_CONSECUTIVE_DAYS,
) -> list[LowCoverageSignal]:
    """从历史日指标里识别「覆盖率长期走低」的类别 → 定向采集需求信号。

    这是原文「标签体系反过来驱动采集策略」的落点：信号产出后，
    由采集侧按类别下发定向采集任务（对接采集域 ods_collect_task）。

    :param history: 多天多类别的日指标行，顺序无所谓，内部按 stat_date 排序
    :param threshold: 判定线
    :param consecutive_days: 连续多少天算「长期」
    :return: 每个触发的类别一条信号
    :raises ValueError: 参数非法
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold 需在 [0,1]，收到 {threshold}")
    if consecutive_days < 1:
        raise ValueError(f"consecutive_days 至少为 1，收到 {consecutive_days}")

    by_cat: dict[TagCategory, list[CoverageRow]] = {}
    for row in history:
        by_cat.setdefault(row.tag_category, []).append(row)

    signals: list[LowCoverageSignal] = []
    for cat, rows in by_cat.items():
        if cat is TagCategory.CAPTION:
            continue  # caption 是自由文本，没有「覆盖率」口径
        rows = sorted(rows, key=lambda r: r.stat_date)
        streak: list[CoverageRow] = []
        for row in rows:
            if row.data_coverage_ratio < threshold:
                streak.append(row)
            else:
                streak = []
        if len(streak) >= consecutive_days:
            signals.append(
                LowCoverageSignal(
                    tag_category=cat,
                    consecutive_days=len(streak),
                    latest_rate=streak[-1].data_coverage_ratio,
                    worst_rate=min(r.data_coverage_ratio for r in streak),
                    window=(streak[0].stat_date, streak[-1].stat_date),
                    threshold=threshold,
                )
            )
    signals.sort(key=lambda s: (s.worst_rate, s.tag_category.value))
    return signals


@dataclass(slots=True)
class CoverageReader:
    """从 StarRocks 读覆盖度历史（External Catalog 直查 Paimon）。

    连接信息取自 :func:`adas_lakehouse.config.settings`；``pymysql`` 未安装时
    **不影响 import 本模块**——只有真正查询时才报错，SQL 渲染始终可用。
    """

    table: str = COVERAGE_TABLE
    _last_sql: str = field(default="", init=False, repr=False)

    def render_sql(self, days: int = LOW_COVERAGE_CONSECUTIVE_DAYS) -> str:
        """渲染最近 N 天的查询 SQL（维度固定为原文的「日期 × 标签类别」）。"""
        if days < 1:
            raise ValueError(f"days 至少为 1，收到 {days}")
        cfg = settings().starrocks
        dims = ", ".join(f"`{d}`" for d in COVERAGE_DIMENSIONS)
        date_dim = COVERAGE_DIMENSIONS[0]
        sql = (
            f"SELECT {dims}, `tagged_data_count`, `total_data_count`, `data_coverage_ratio`,\n"
            f"       `tagged_image_count`, `total_image_count`, `image_coverage_ratio`\n"
            f"FROM `{cfg.external_catalog}`.`{settings().paimon.database}`.`{self.table}`\n"
            f"WHERE `{date_dim}` >= DATE_SUB(CURDATE(), INTERVAL {days} DAY)\n"
            f"ORDER BY `{date_dim}`, `tag_category`"
        )
        self._last_sql = sql
        return sql

    def fetch(self, days: int = LOW_COVERAGE_CONSECUTIVE_DAYS) -> list[CoverageRow]:
        """真正查库。

        :raises RuntimeError: 缺少 pymysql 依赖或连接失败
        """
        sql = self.render_sql(days)
        try:
            import pymysql  # 延迟 import：客户端库缺失不应让本模块 import 失败
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise RuntimeError(
                "查询 StarRocks 需要 pymysql（pip install pymysql）；"
                "只想拿 SQL 请用 CoverageReader.render_sql()"
            ) from exc
        cfg = settings().starrocks
        try:  # pragma: no cover - 需要真实 StarRocks
            conn = pymysql.connect(
                host=cfg.fe_host,
                port=cfg.query_port,
                user=cfg.user,
                password=cfg.password,
                charset="utf8mb4",
            )
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"连接 StarRocks 失败：{cfg.fe_host}:{cfg.query_port}") from exc
        try:  # pragma: no cover
            with conn.cursor() as cur:
                cur.execute(sql)
                return [
                    CoverageRow(
                        stat_date=str(r[0]),
                        tag_category=TagCategory(str(r[1])),
                        tagged_data_count=int(r[2] or 0),
                        total_data_count=int(r[3] or 0),
                        tagged_image_count=int(r[5] or 0),
                        total_image_count=int(r[6] or 0),
                    )
                    for r in cur.fetchall()
                ]
        finally:  # pragma: no cover
            conn.close()
