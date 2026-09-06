"""Deterministic business-date ranges shared by querying and attribution."""
import re
from datetime import date, timedelta


def question_periods(question: str, today: date | None = None):
    """Return inclusive current/base periods; the default is 30 completed days."""
    try:
        return _question_periods(question, today)
    except OverflowError:
        raise ValueError("日期超出支持范围，请选择能包含完整分析期和对比期的日期。") from None


def _question_periods(question: str, today: date | None):
    today = today or date.today()
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", question)
    if dates:
        if len(dates) > 2:
            raise ValueError("请每次指定一个分析日期区间；系统会对比前一个等长区间。")
        try:
            start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        except ValueError:
            raise ValueError("分析日期无效，请按 YYYY-MM-DD 填写有效日期。") from None
    elif any(word in question for word in ("昨天", "昨日", "yesterday")):
        start = end = today - timedelta(days=1)
    elif "前天" in question:
        start = end = today - timedelta(days=2)
    elif any(word in question for word in ("今天", "今日")):
        start = end = today
    elif any(word in question for word in ("上个月", "上月")):
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif any(word in question for word in ("这个月", "本月")):
        start, end = today.replace(day=1), today
    elif "上周" in question:
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
    else:
        match = re.search(r"(?:过去|最近|近|前)?\s*(\d+)\s*天", question)
        days = int(match.group(1)) if match else (7 if "一周" in question else 30)
        if days < 1 or days > 365:
            raise ValueError("分析日期区间需按先后顺序填写，且不能超过 365 天。")
        start, end = today - timedelta(days=days), today - timedelta(days=1)
    days = (end - start).days + 1
    if days < 1 or days > 365:
        raise ValueError("分析日期区间需按先后顺序填写，且不能超过 365 天。")
    baseline_end = start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=days - 1)
    if "同比" in question or "去年同期" in question:
        def prior_year(value):
            try:
                return value.replace(year=value.year - 1)
            except ValueError:  # February 29 compares with February 28.
                return value.replace(year=value.year - 1, day=28)
        baseline_start, baseline_end = prior_year(start), prior_year(end)
    return ({"start": start.isoformat(), "end": end.isoformat()},
            {"start": baseline_start.isoformat(), "end": baseline_end.isoformat()})
