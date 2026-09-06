"""Deterministic, inspectable analysis-plan extraction for Chinese questions."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, timedelta

_METRIC_WORDS = (
    "播放完成率",
    "完播率",
    "点击率",
    "转化率",
    "退款金额",
    "退款率",
    "订单金额",
    "订单数",
    "收藏数",
    "评论数",
    "评分",
    "播放量",
    "播放次数",
    "会员数",
    "搜索量",
    "收入",
    "金额",
    "数量",
    "总数",
)

_METRIC_ALIASES = {
    "播放次数": ("播放总次数", "总播放次数", "播放次数", "播放量"),
    "平均播放时长": ("平均播放时长", "平均收听时长", "播放平均时长"),
}

_CONTENT_GENRES = ("玄幻", "言情")

_DIMENSION_WORDS = (
    "专辑",
    "声音",
    "章节",
    "作者",
    "主播",
    "分类",
    "地区",
    "会员",
    "订单",
    "渠道",
    "设备",
    "关键词",
    "榜单",
)


@dataclass(frozen=True)
class TimeRange:
    start: str | None = None
    end: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class AnalysisPlan:
    """A compact plan shared by retrieval and SQL generation nodes."""

    intent: str
    metric_hints: list[str]
    dimensions: list[str]
    filters: list[str]
    time_range: TimeRange
    time_grain: str | None
    top_n: int | None
    sort_direction: str | None
    comparison: str | None
    filter_requirements: list[dict]
    metric_requirements: list[dict]

    def to_state(self) -> dict:
        value = asdict(self)
        value["time_range"] = asdict(self.time_range)
        return value


def build_analysis_plan(query: str, reference_date: date | None = None) -> AnalysisPlan:
    """Extract stable intent, period and ranking hints without requiring an LLM."""

    today = reference_date or date.today()
    time_range = _parse_time_range(query, today)
    comparison = _parse_comparison(query)
    top_n = _parse_top_n(query)
    time_grain = _parse_time_grain(query)

    if top_n is not None or any(word in query.lower() for word in ("排行", "排名", "top")):
        intent = "ranking"
    elif comparison:
        intent = "compare"
    elif time_grain or "趋势" in query or "变化" in query:
        intent = "trend"
    elif any(word in query for word in ("明细", "列表", "哪些", "详情")):
        intent = "detail"
    else:
        intent = "aggregate"

    metric_hints = _extract_metric_hints(query)
    dimensions = [word for word in _DIMENSION_WORDS if word in query]
    if any(genre in query for genre in _CONTENT_GENRES) and "分类" not in dimensions:
        dimensions.append("分类")
    filter_requirements = _build_filter_requirements(query)
    metric_requirements = _build_metric_requirements(metric_hints, comparison, query)
    filters = list(
        dict.fromkeys(
            [
                requirement["label"]
                for requirement in filter_requirements
                if requirement.get("location") == "where"
            ]
            + _extract_filters(query)
        )
    )
    sort_direction = (
        "asc"
        if any(word in query for word in ("最低", "最少", "倒序最小"))
        else "desc"
        if intent == "ranking"
        else None
    )
    return AnalysisPlan(
        intent=intent,
        metric_hints=metric_hints,
        dimensions=dimensions,
        filters=filters,
        time_range=time_range,
        time_grain=time_grain,
        top_n=top_n,
        sort_direction=sort_direction,
        comparison=comparison,
        filter_requirements=filter_requirements,
        metric_requirements=metric_requirements,
    )


def _extract_metric_hints(query: str) -> list[str]:
    """同时保留用户原词和规范指标名，避免“播放总次数”被拆词漏召回。"""

    hints: list[str] = []
    for canonical, aliases in _METRIC_ALIASES.items():
        for alias in aliases:
            if alias in query:
                hints.extend([alias, canonical])
                break
    hints.extend(word for word in _METRIC_WORDS if word in query)
    return list(dict.fromkeys(hints))


def _build_filter_requirements(query: str) -> list[dict]:
    """把高价值业务筛选词固化为可被 SQL guard 验证的约束。"""

    requirements: list[dict] = []
    if "完播记录" in query:
        requirements.append(
            {
                "label": "播放状态为完播（completed）",
                "columns": ["play_session.play_status"],
                "values": ["completed"],
                "value_match": "exact",
                "location": "where",
                "filter_only": False,
            }
        )
    if "专辑" in query and any(word in query for word in ("已发布", "最近发布")):
        requirements.append(
            {
                "label": "专辑状态为已发布（published）",
                "columns": ["audio_album.album_status"],
                "values": ["published"],
                "value_match": "exact",
                "location": "where",
                "filter_only": False,
            }
        )
    region_match = re.search(r"([\u4e00-\u9fff]{2,6}?)(?:地区|区域)", query)
    if region_match:
        region = region_match.group(1)
        requirements.append(
            {
                "label": f"地区包含{region}",
                "columns": ["user_profile.province", "user_profile.city"],
                "values": [region],
                "value_match": "contains",
                "location": "where",
                # 画像地区只允许参与聚合查询的 WHERE，不允许直接输出。
                "filter_only": True,
            }
        )

    if any(word in query for word in ("男性", "男用户", "男会员", "男性用户")):
        requirements.append(
            {
                "label": "性别为男性",
                "columns": ["user_profile.gender"],
                "values": ["male"],
                "value_match": "exact",
                "location": "where",
                "filter_only": False,
            }
        )
    elif any(word in query for word in ("女性", "女用户", "女会员", "女性用户")):
        requirements.append(
            {
                "label": "性别为女性",
                "columns": ["user_profile.gender"],
                "values": ["female"],
                "value_match": "exact",
                "location": "where",
                "filter_only": False,
            }
        )

    member_level: str | None = None
    member_label: str | None = None
    if "黄金会员" in query or "VIP会员" in query or "vip会员" in query.lower():
        member_level, member_label = "vip", "会员等级为黄金会员（vip）"
    elif "普通会员" in query:
        member_level, member_label = "normal", "会员等级为普通会员（normal）"
    if member_level and member_label:
        requirements.extend(
            [
                {
                    "label": member_label,
                    "columns": ["member_account.member_level"],
                    "values": [member_level],
                    "value_match": "exact",
                    "location": "where",
                    "filter_only": False,
                },
                {
                    "label": "会员状态有效（active）",
                    "columns": ["member_account.member_status"],
                    "values": ["active"],
                    "value_match": "exact",
                    "location": "where",
                    "filter_only": False,
                },
                {
                    "label": "会员已生效",
                    "columns": ["member_account.valid_from"],
                    "values": [],
                    "operators": ["lte"],
                    "location": "where",
                    "filter_only": False,
                },
                {
                    "label": "会员未过期",
                    "columns": ["member_account.valid_to"],
                    "values": [],
                    "operators": ["gte"],
                    "location": "where",
                    "filter_only": False,
                },
            ]
        )

    for genre in _CONTENT_GENRES:
        if genre in query:
            requirements.append(
                {
                    "label": f"内容分类包含{genre}",
                    "columns": ["dim_audio_category.category_name"],
                    "values": [genre],
                    "value_match": "contains",
                    # 分类条件可位于 CASE WHEN 中，不强制进入全局 WHERE。
                    "location": "any",
                    "filter_only": False,
                }
            )
    return requirements


def _build_metric_requirements(
    metric_hints: list[str], comparison: str | None, query: str
) -> list[dict]:
    requirements: list[dict] = []
    # “榜单播放量”是 ranking_item.play_count 的快照指标，不等同于播放会话数。
    # 若强行套用通用播放次数约束，校验器会错误拒绝正确的榜单聚合 SQL。
    if "播放次数" in metric_hints and "榜单播放量" not in query:
        requirements.append(
            {
                "label": "播放次数",
                "column": "play_session.id",
                "aggregate": "COUNT",
                "minimum_occurrences": 1,
                "allow_distinct": False,
                "allow_star": True,
            }
        )
    if "平均播放时长" in metric_hints:
        genre_count = sum(genre in query for genre in _CONTENT_GENRES)
        requirements.append(
            {
                "label": "平均播放时长",
                "column": "play_session.played_seconds",
                "aggregate": "AVG",
                "minimum_occurrences": 2 if genre_count >= 2 else 1,
                "operation": "subtract" if comparison == "difference" else None,
                "allow_distinct": True,
            }
        )
    return requirements


def _parse_time_range(query: str, today: date) -> TimeRange:
    if "今天" in query:
        return _range(today, today, "今天")
    if "昨天" in query:
        yesterday = today - timedelta(days=1)
        return _range(yesterday, yesterday, "昨天")
    if "本周" in query:
        return _range(today - timedelta(days=today.weekday()), today, "本周")
    if "上周" in query:
        end = today - timedelta(days=today.weekday() + 1)
        return _range(end - timedelta(days=6), end, "上周")
    if "本月" in query:
        return _range(today.replace(day=1), today, "本月")
    if "上月" in query:
        first_this_month = today.replace(day=1)
        end = first_this_month - timedelta(days=1)
        return _range(end.replace(day=1), end, "上月")
    if "本季度" in query:
        quarter_start_month = (today.month - 1) // 3 * 3 + 1
        return _range(today.replace(month=quarter_start_month, day=1), today, "本季度")
    if "今年" in query or "本年" in query:
        return _range(today.replace(month=1, day=1), today, "今年")

    match = re.search(r"(?:最近|近)(\d+)(?:个)?(?:天|日)", query)
    if match:
        days = max(1, int(match.group(1)))
        return _range(today - timedelta(days=days - 1), today, f"最近{days}天")
    match = re.search(r"(?:最近|近)(\d+)(?:个)?月", query)
    if match:
        months = max(1, int(match.group(1)))
        start = _shift_month(today.replace(day=1), -(months - 1))
        return _range(start, today, f"最近{months}个月")
    return TimeRange()


def _parse_time_grain(query: str) -> str | None:
    if any(word in query for word in ("按小时", "每小时", "小时趋势")):
        return "hour"
    if any(word in query for word in ("按天", "每天", "日趋势", "每日")):
        return "day"
    if any(word in query for word in ("按周", "每周", "周趋势")):
        return "week"
    if any(word in query for word in ("按月", "每月", "月趋势", "月度")):
        return "month"
    if "趋势" in query or "变化" in query:
        return "day"
    return None


def _parse_comparison(query: str) -> str | None:
    if "同比" in query:
        return "year_over_year"
    if "环比" in query:
        return "period_over_period"
    if "对比" in query or "比较" in query:
        return "comparison"
    if "差多少" in query or "差值" in query or "相差" in query:
        return "difference"
    return None


def _parse_top_n(query: str) -> int | None:
    match = re.search(r"(?:top\s*|前)(\d+)", query, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_filters(query: str) -> list[str]:
    """Keep only explicit, human-readable filter fragments for the SQL prompt."""

    matches = re.findall(r"(?:在|按|针对|仅)([^，。？?、]{2,20})", query)
    return list(dict.fromkeys(item.strip() for item in matches if item.strip()))[:5]


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _range(start: date, end: date, label: str) -> TimeRange:
    return TimeRange(start=start.isoformat(), end=end.isoformat(), label=label)
