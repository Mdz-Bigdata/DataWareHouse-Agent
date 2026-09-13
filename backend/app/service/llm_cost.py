# -*- coding: utf-8 -*-
"""LLM token 用量与成本计量：一张按「日期 × 模型档位 × 模型 × 数据源」聚合的成本日表。

**为什么有这个模块**：本系统按 token 计费调用外部大模型，但在此之前
``backend/app`` 全库没有任何一处记账——``ask_agent._call_llm`` 的模型路由只
``print`` 一行日志，调用一结束用量就丢了。于是「上个月花了多少钱」「哪个数据源
最烧钱」「complex 档位值不值」这类问题**一个都答不出来**。本模块补上这本账。

设计取自智驾数据闭环湖仓 a14 的成本账思路（成本日表 + 容量×单价折算 + 两条告警线），
但**不 import 它的任何代码**：那是另一个独立应用，两边的计量对象也不同
（那边是存储容量 × 介质单价，这边是 token 数 × 模型单价）。

口径（必须先说清，否则这张表会被误读）：

* **一行 = 一个 (日期, 模型档位, 模型, 厂商, 数据源) 组合的当日合计**，
  记 ``calls`` / ``prompt_tokens`` / ``completion_tokens`` / ``cost_yuan``。
* **日期取记账时刻的本地日历日**，不是 UTC；跨天的调用各自落在各自那天。
* **单价表是示意值**（见 :data:`DEFAULT_PRICE_BOOK` 的警告），生产环境必须用
  厂商实际计价覆盖。单价表里查不到的模型**不会按 0 元静默记账**——那会凭空
  低估花销——而是退到该档位的保守兜底价，并把 ``price_source`` 标成
  ``tier_default``，让报表看得见这笔是估的。
* **厂商没返回 usage 时按字符估算 token**，该行计入 ``estimated_calls``。
  估算值只能当量级看，不能拿去和账单对账。``MOCK_LLM`` 下的调用记 ``vendor="mock"``、
  单价 0、成本恒为 0——本地假调用不产生真实花销，不能混进钱数里。
* 账本默认**只在进程内存里**，有界（``max_days`` / ``max_rows``），重启即清空。
  需要跨重启就设环境变量 ``LLM_COST_LEDGER_PATH``，逐条追加 JSONL 事件流，
  可用 :meth:`LlmCostLedger.replay_events` 重建。默认关闭，不写任何文件。

两条告警线是外部给定的口径，逐字使用，不要在别处重新定义：
:data:`ALERT_RING_GROWTH_RATIO` = 0.10（成本环比增长 > 10%）、
:data:`ALERT_BUDGET_WATERLINE` = 0.80（预算水位 > 80%）。
"""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "ALERT_RING_GROWTH_RATIO",
    "ALERT_BUDGET_WATERLINE",
    "Price",
    "DEFAULT_PRICE_BOOK",
    "TIER_FALLBACK_PRICE",
    "resolve_price",
    "estimate_tokens",
    "DailyCostRow",
    "LlmCostLedger",
    "llm_cost_ledger",
    "active_data_source_id",
    "record_llm_usage",
]

# --------------------------------------------------------------------- 告警线

#: 成本环比增长告警线：环比增长 **> 10%** 告警。原文给定，逐字使用。
ALERT_RING_GROWTH_RATIO: float = 0.10

#: 预算水位告警线：已用 / 预算 **> 80%** 告警。原文给定，逐字使用。
ALERT_BUDGET_WATERLINE: float = 0.80


# --------------------------------------------------------------------- 单价表


@dataclass(frozen=True)
class Price:
    """模型单价（元 / 千 token），prompt 与 completion 分别计价。

    :param source: 这个单价是怎么来的——``price_book`` 命中单价表、
        ``tier_default`` 退到档位兜底价、``mock`` 本地假调用（恒 0）、
        ``override`` 调用方显式传入。报表要据此区分「按真实单价算的」和「估的」。
    """

    prompt_per_1k: float
    completion_per_1k: float
    source: str = "price_book"

    def cost_yuan(self, prompt_tokens: int, completion_tokens: int) -> float:
        """折算成本（元）。token 数为负一律当 0，不产生负成本。"""
        prompt_tokens = max(0, int(prompt_tokens or 0))
        completion_tokens = max(0, int(completion_tokens or 0))
        return (prompt_tokens / 1000.0) * self.prompt_per_1k + (
            completion_tokens / 1000.0
        ) * self.completion_per_1k


#: ⚠️ **示意单价，不是账单**。按 2026 年前后各厂商公开价换算成人民币的量级值，
#: 汇率、阶梯价、缓存命中折扣、长上下文加价一概没算。生产环境请用
#: :meth:`LlmCostLedger.set_price` 或 ``LLM_COST_PRICE_BOOK`` 覆盖成实际计价。
#:
#: 匹配规则：对模型名小写后做**子串**匹配，**从上往下取第一个命中**，
#: 所以更具体的型号必须排在更宽泛的前面（``gpt-4o-mini`` 在 ``gpt-4o`` 之前）。
DEFAULT_PRICE_BOOK: Tuple[Tuple[str, Price], ...] = (
    ("gpt-4o-mini", Price(0.0011, 0.0043)),
    ("gpt-4.1-mini", Price(0.0029, 0.0115)),
    ("gpt-4.1", Price(0.0144, 0.0576)),
    ("gpt-4o", Price(0.0180, 0.0720)),
    ("o4-mini", Price(0.0079, 0.0317)),
    ("gemini-2.5-flash", Price(0.0022, 0.0180)),
    ("gemini-3.5-flash", Price(0.0022, 0.0180)),
    ("flash-lite", Price(0.0005, 0.0022)),
    ("flash", Price(0.0005, 0.0022)),
    ("gemini-3.1-pro", Price(0.0090, 0.0720)),
    ("gemini", Price(0.0090, 0.0720)),
    ("deepseek-reasoner", Price(0.0040, 0.0160)),
    ("deepseek-chat", Price(0.0020, 0.0080)),
    ("deepseek", Price(0.0020, 0.0080)),
    ("qwen-max", Price(0.0024, 0.0096)),
    ("qwen", Price(0.0008, 0.0020)),
    ("glm-4", Price(0.0010, 0.0010)),
    ("moonshot", Price(0.0120, 0.0120)),
)

#: 单价表没命中时按档位兜底。刻意取**偏贵**的一档：宁可高估也不能把未知模型
#: 按 0 元记账——低估花销比高估危险得多。
TIER_FALLBACK_PRICE: Dict[str, Price] = {
    "fast": Price(0.0020, 0.0080, source="tier_default"),
    "complex": Price(0.0200, 0.0800, source="tier_default"),
}

#: 本地假调用（``MOCK_LLM=true``）恒 0 元：它没有向任何厂商发过请求。
MOCK_PRICE = Price(0.0, 0.0, source="mock")

_MOCK_VENDORS = frozenset({"mock", "local", "offline"})


def _coerce_price(value: Any) -> Optional[Price]:
    """把配置里的单价条目转成 :class:`Price`；形状不对返回 ``None`` 而不是抛。"""
    if isinstance(value, Price):
        return value
    if isinstance(value, dict):
        try:
            return Price(
                float(value.get("prompt_per_1k", value.get("prompt", 0.0))),
                float(value.get("completion_per_1k", value.get("completion", 0.0))),
                source=str(value.get("source", "override")),
            )
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return Price(float(value[0]), float(value[1]), source="override")
        except (TypeError, ValueError):
            return None
    return None


def _price_book_from_env() -> Tuple[Tuple[str, Price], ...]:
    """``LLM_COST_PRICE_BOOK``（JSON 对象：模型名片段 -> 单价）覆盖内置单价表。

    配置坏了只打一行日志、退回内置表——计量绝不能把主流程拖垮。
    """
    raw = os.getenv("LLM_COST_PRICE_BOOK", "").strip()
    if not raw:
        return DEFAULT_PRICE_BOOK
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("必须是 JSON 对象")
    except Exception as error:  # noqa: BLE001 - 配置问题不该影响问数
        print(f"[LLM Cost] LLM_COST_PRICE_BOOK 解析失败，改用内置示意单价：{error}")
        return DEFAULT_PRICE_BOOK
    entries: List[Tuple[str, Price]] = []
    for pattern, value in parsed.items():
        price = _coerce_price(value)
        if price is not None:
            entries.append((str(pattern).lower(), price))
    # 长的（更具体的）型号片段先匹配，避免 "gpt-4o" 抢走 "gpt-4o-mini"。
    entries.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(entries) + DEFAULT_PRICE_BOOK


def resolve_price(
    model: str,
    model_tier: str = "fast",
    price_book: Optional[Iterable[Tuple[str, Price]]] = None,
    vendor: str = "",
) -> Price:
    """给 (模型, 档位) 定价。命中不了单价表就退到档位兜底价（标记 ``tier_default``）。"""
    if str(vendor or "").lower() in _MOCK_VENDORS:
        return MOCK_PRICE
    name = str(model or "").lower()
    for pattern, price in price_book if price_book is not None else DEFAULT_PRICE_BOOK:
        if pattern and pattern in name:
            return price
    return TIER_FALLBACK_PRICE.get(
        str(model_tier or "").lower(), TIER_FALLBACK_PRICE["complex"]
    )


# --------------------------------------------------------------------- token 估算

# CJK 统一表意文字 + 常用中日韩标点/假名/谚文的粗略区间。
_CJK_RANGES = (
    (0x3000, 0x303F),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
)


def _is_cjk(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in _CJK_RANGES)


def estimate_tokens(text: Any) -> int:
    """厂商没回 usage 时的兜底 token 估算。

    经验系数：中日韩字符约 1.5 字/token，其余（英文、数字、SQL、JSON）约 4 字符/token。
    **只能当量级看**，和厂商账单对不上是正常的——所以用到它的行会被计入
    ``estimated_calls``，报表必须把估算量和实测量分开看。
    """
    if not isinstance(text, str) or not text:
        return 0
    cjk = sum(1 for char in text if _is_cjk(char))
    other = len(text) - cjk
    return max(1, math.ceil(cjk / 1.5 + other / 4.0))


# --------------------------------------------------------------------- 日期归一


def _normalize_day(value: Any = None) -> str:
    """把入参归一成 ``YYYY-MM-DD``；给不出合法日期就用今天（记账绝不能因此失败）。"""
    if value is None:
        return date.today().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return date.today().isoformat()


def _previous_day(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def _label(value: Any, fallback: str = "unknown", limit: int = 80) -> str:
    """标签列归一：非字符串/空值退到 fallback，并截断，避免异常输入撑爆内存。"""
    if value is None:
        return fallback
    text = str(value).strip().replace("\n", " ")
    return text[:limit] if text else fallback


# --------------------------------------------------------------------- 成本日表行


@dataclass
class DailyCostRow:
    """成本日表的一行：一个 (日期, 档位, 模型, 厂商, 数据源) 组合的当日合计。"""

    day: str
    model_tier: str
    model: str
    vendor: str
    data_source: str
    calls: int = 0
    failed_calls: int = 0
    estimated_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_yuan: float = 0.0
    price_source: str = "price_book"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "day": self.day,
            "model_tier": self.model_tier,
            "model": self.model,
            "vendor": self.vendor,
            "data_source": self.data_source,
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "estimated_calls": self.estimated_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            # 钱按 6 位小数出账：单次 fast 档调用可能只有几厘钱，2 位会被抹成 0。
            "cost_yuan": round(self.cost_yuan, 6),
            "price_source": self.price_source,
        }


# --------------------------------------------------------------------- 账本


class LlmCostLedger:
    """进程内有界的 LLM 成本日表，支持按天/模型/档位/数据源回查与两条告警线。

    线程安全：一次问数可能并发发起多次模型调用，聚合写入必须串行。
    """

    def __init__(
        self,
        max_days: int = 90,
        max_rows: int = 5000,
        price_book: Optional[Iterable[Tuple[str, Price]]] = None,
        persist_path: Optional[str] = None,
    ):
        self.max_days = max(1, int(max_days))
        self.max_rows = max(1, int(max_rows))
        self._price_book: List[Tuple[str, Price]] = list(
            price_book if price_book is not None else _price_book_from_env()
        )
        # 默认不落盘；只有显式配置 LLM_COST_LEDGER_PATH 才追加 JSONL 事件流。
        self._persist_path = persist_path or os.getenv("LLM_COST_LEDGER_PATH", "").strip() or None
        self._rows: Dict[Tuple[str, str, str, str, str], DailyCostRow] = {}
        self._unknown_models: set = set()
        # 重放期间置位：挂起逐条淘汰（O(行数)，十万条事件会退化成分钟级），
        # 并且**不再回写事件流**——重放的事件本来就来自那个文件，再追加一遍会让
        # 文件每重启一次翻一倍，下次重放就重复记账。
        self._replaying = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 单价配置
    def set_price(self, pattern: str, price: Any) -> None:
        """用实际计价覆盖某个模型（名字片段）的单价；新规则优先于内置表。"""
        resolved = _coerce_price(price)
        if resolved is None:
            raise ValueError(f"单价格式不支持: {price!r}")
        with self._lock:
            self._price_book.insert(0, (str(pattern).lower(), resolved))

    def price_for(self, model: str, model_tier: str = "fast", vendor: str = "") -> Price:
        with self._lock:
            return resolve_price(model, model_tier, self._price_book, vendor)

    # ------------------------------------------------------------ 记账
    def record(
        self,
        *,
        model: str,
        model_tier: str = "fast",
        vendor: str = "unknown",
        data_source: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        prompt_text: Optional[str] = None,
        completion_text: Optional[str] = None,
        ok: bool = True,
        day: Any = None,
    ) -> DailyCostRow:
        """记一次模型调用的用量与折算成本，返回它落进的那一行（已累加）。

        ``prompt_tokens`` / ``completion_tokens`` 为 ``None`` 时按
        ``prompt_text`` / ``completion_text`` 估算，该次调用计入 ``estimated_calls``。

        ``ok=False``（调用报错）仍然记账：多数厂商对失败请求照样收 prompt token 的钱，
        不记就会漏账；这类调用另计 ``failed_calls``。
        """
        estimated = False
        if prompt_tokens is None:
            prompt_tokens = estimate_tokens(prompt_text)
            estimated = True
        if completion_tokens is None:
            completion_tokens = estimate_tokens(completion_text)
            estimated = True
        prompt_tokens = self._safe_int(prompt_tokens)
        completion_tokens = self._safe_int(completion_tokens)

        day_key = _normalize_day(day)
        tier = _label(model_tier, "unknown", 40).lower()
        model_name = _label(model, "unknown")
        vendor_name = _label(vendor, "unknown", 40).lower()
        source = _label(
            data_source if data_source is not None else active_data_source_id(), "unknown"
        )

        price = self.price_for(model_name, tier, vendor_name)
        if price.source == "tier_default":
            self._warn_unknown_model(model_name, tier)
        cost = price.cost_yuan(prompt_tokens, completion_tokens)

        key = (day_key, tier, model_name, vendor_name, source)
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                row = DailyCostRow(
                    day=day_key,
                    model_tier=tier,
                    model=model_name,
                    vendor=vendor_name,
                    data_source=source,
                    price_source=price.source,
                )
                self._rows[key] = row
            row.calls += 1
            if price.source == "tier_default":
                # 「不确定」有黏性：这一行只要掺进过一笔按兜底价估的钱，
                # 整行就不能再自称是按真实单价算出来的。
                row.price_source = "tier_default"
            if not ok:
                row.failed_calls += 1
            if estimated:
                row.estimated_calls += 1
            row.prompt_tokens += prompt_tokens
            row.completion_tokens += completion_tokens
            row.cost_yuan += cost
            self._evict_if_needed()
        self._append_event(
            {
                "day": day_key,
                "model_tier": tier,
                "model": model_name,
                "vendor": vendor_name,
                "data_source": source,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_yuan": round(cost, 6),
                "ok": bool(ok),
                "estimated": estimated,
            }
        )
        return row

    @staticmethod
    def _safe_int(value: Any) -> int:
        """token 数归一：非数字、负数、NaN 一律当 0，绝不让脏值污染账本。"""
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    def _warn_unknown_model(self, model: str, tier: str) -> None:
        """单价表没这个模型——每个模型只提醒一次，别把日志刷爆。"""
        with self._lock:
            if model in self._unknown_models:
                return
            self._unknown_models.add(model)
        print(
            f"[LLM Cost] 单价表没有模型 '{model}'，暂按 '{tier}' 档位兜底价记账"
            f"（偏保守，会高估）。请用 llm_cost_ledger.set_price() 或 LLM_COST_PRICE_BOOK 配置实际单价。"
        )

    def _evict_if_needed(self) -> None:
        """有界淘汰：先按天丢最老的，仍超行数上限再丢最老那几天的行。调用方已持锁。"""
        if self._replaying:
            return
        days = sorted({key[0] for key in self._rows})
        while len(days) > self.max_days:
            oldest = days.pop(0)
            for key in [k for k in self._rows if k[0] == oldest]:
                del self._rows[key]
        while len(self._rows) > self.max_rows and days:
            oldest = days.pop(0)
            for key in [k for k in self._rows if k[0] == oldest]:
                del self._rows[key]

    def _append_event(self, event: Dict[str, Any]) -> None:
        """可选的 JSONL 事件流落盘。写失败只打日志——账本不该因为磁盘问题炸掉主流程。"""
        if not self._persist_path or self._replaying:
            return
        try:
            with open(self._persist_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as error:  # noqa: BLE001
            print(f"[LLM Cost] 成本事件落盘失败（已跳过，不影响问数）：{error}")

    def replay_events(self, path: Optional[str] = None) -> int:
        """从 JSONL 事件流重建账本（跨重启恢复），返回成功重放的条数。坏行跳过。"""
        target = path or self._persist_path
        if not target or not os.path.exists(target):
            return 0
        self._replaying = True
        try:
            replayed = self._replay_lines(target)
        finally:
            self._replaying = False
            with self._lock:
                self._evict_if_needed()
        return replayed

    def _replay_lines(self, target: str) -> int:
        replayed = 0
        with open(target, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:  # noqa: BLE001 - 坏行跳过，不让一行毁掉整本账
                    continue
                if not isinstance(event, dict):
                    continue
                self.record(
                    model=event.get("model", "unknown"),
                    model_tier=event.get("model_tier", "unknown"),
                    vendor=event.get("vendor", "unknown"),
                    data_source=event.get("data_source", "unknown"),
                    prompt_tokens=event.get("prompt_tokens", 0),
                    completion_tokens=event.get("completion_tokens", 0),
                    ok=bool(event.get("ok", True)),
                    day=event.get("day"),
                )
                replayed += 1
        return replayed

    # ------------------------------------------------------------ 查询
    def rows(
        self,
        day: Any = None,
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
        data_source: Optional[str] = None,
        vendor: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """成本日表查询。任一条件传 ``None`` 表示不过滤；``day=None`` 表示不限日期。

        ``model`` 做**大小写不敏感的子串**匹配（"gpt-4o" 能捞到 "gpt-4o-mini"，
        要精确到型号就传全名）；其余条件精确匹配。
        """
        day_key = _normalize_day(day) if day is not None else None
        model_q = str(model).lower() if model else None
        tier_q = str(model_tier).lower() if model_tier else None
        source_q = str(data_source) if data_source else None
        vendor_q = str(vendor).lower() if vendor else None
        with self._lock:
            selected = [
                row
                for row in self._rows.values()
                if (day_key is None or row.day == day_key)
                and (model_q is None or model_q in row.model.lower())
                and (tier_q is None or row.model_tier == tier_q)
                and (source_q is None or row.data_source == source_q)
                and (vendor_q is None or row.vendor == vendor_q)
            ]
        selected.sort(key=lambda row: (row.day, -row.cost_yuan, row.model))
        return [row.to_dict() for row in selected]

    def summary(self, **filters: Any) -> Dict[str, Any]:
        """把 :meth:`rows` 的结果合成一个总计（调用数、token 数、成本、估算占比）。"""
        matched = self.rows(**filters)
        total_cost = sum(row["cost_yuan"] for row in matched)
        return {
            "rows": len(matched),
            "calls": sum(row["calls"] for row in matched),
            "failed_calls": sum(row["failed_calls"] for row in matched),
            "estimated_calls": sum(row["estimated_calls"] for row in matched),
            "prompt_tokens": sum(row["prompt_tokens"] for row in matched),
            "completion_tokens": sum(row["completion_tokens"] for row in matched),
            "total_tokens": sum(row["total_tokens"] for row in matched),
            "cost_yuan": round(total_cost, 6),
        }

    def cost_on(
        self,
        day: Any = None,
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
        data_source: Optional[str] = None,
        vendor: Optional[str] = None,
    ) -> float:
        """「某天 / 某模型花了多少钱」——直接给一个数（元）。

        注意 ``day=None`` 表示**不限日期**（账本内全部合计），不是「今天」；
        要今天就显式传 ``date.today()``。告警线相关方法的 ``day=None`` 才默认今天。
        """
        return self.summary(
            day=day, model=model, model_tier=model_tier,
            data_source=data_source, vendor=vendor,
        )["cost_yuan"]

    def days(self) -> List[str]:
        """账本里有记录的日期，升序。"""
        with self._lock:
            return sorted({key[0] for key in self._rows})

    def daily_totals(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """按天汇总的成本曲线，升序；``limit`` 只取最近 N 天。"""
        totals: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for row in self._rows.values():
                bucket = totals.setdefault(
                    row.day,
                    {"day": row.day, "calls": 0, "total_tokens": 0, "cost_yuan": 0.0},
                )
                bucket["calls"] += row.calls
                bucket["total_tokens"] += row.total_tokens
                bucket["cost_yuan"] += row.cost_yuan
        ordered = [totals[day] for day in sorted(totals)]
        for bucket in ordered:
            bucket["cost_yuan"] = round(bucket["cost_yuan"], 6)
        if limit is not None:
            ordered = ordered[-max(1, int(limit)):]
        return ordered

    def month_total(self, month: str, **filters: Any) -> float:
        """某个自然月（``YYYY-MM``）的合计成本（元）。"""
        prefix = str(month)[:7]
        with self._lock:
            days = sorted({key[0] for key in self._rows if key[0].startswith(prefix)})
        return round(sum(self.cost_on(day=day, **filters) for day in days), 6)

    # ------------------------------------------------------------ 告警线
    def ring_growth(
        self, day: Any = None, previous_day: Any = None, **filters: Any
    ) -> Dict[str, Any]:
        """成本环比：``day`` 相对上一自然日（或显式指定的 ``previous_day``）的增长率。

        基期为 0 时增长率给 ``None``（0 涨到任何数都是无穷大，报 inf 只会误导），
        并且**不触发**告警——第一天有花销不等于成本失控。
        """
        day_key = _normalize_day(day)
        base_key = _normalize_day(previous_day) if previous_day is not None else _previous_day(day_key)
        current = self.cost_on(day=day_key, **filters)
        previous = self.cost_on(day=base_key, **filters)
        if previous > 0:
            # 先取整到 6 位小数再比阈值：¥0.22 / ¥0.20 在浮点里是 0.10000000000000009，
            # 直接比就会把「正好 10%」判成越线，告警天天误报、很快就没人看了。
            # 取整后的值同时就是对外报出的 growth_ratio——报表显示 10.0% 却告警「超过 10%」
            # 是自相矛盾的，判定和展示必须用同一个数。
            ratio: Optional[float] = round((current - previous) / previous, 6)
            breached = ratio > ALERT_RING_GROWTH_RATIO
        else:
            ratio, breached = None, False
        return {
            "day": day_key,
            "previous_day": base_key,
            "cost_yuan": round(current, 6),
            "previous_cost_yuan": round(previous, 6),
            "growth_ratio": ratio,
            "threshold": ALERT_RING_GROWTH_RATIO,
            "breached": breached,
        }

    def budget_waterline(
        self, budget_yuan: float, day: Any = None, scope: str = "month", **filters: Any
    ) -> Dict[str, Any]:
        """预算水位：已用 / 预算。``scope='month'`` 看当月累计，``'day'`` 看当天。

        :raises ValueError: 预算 <= 0。除以 0 得不到水位，与其返回一个假的
            「没超」不如直接报错——静默放过才是真正的风险。
        """
        budget = float(budget_yuan)
        if budget <= 0:
            raise ValueError(f"预算必须为正数，当前为 {budget_yuan!r}")
        day_key = _normalize_day(day)
        if scope == "day":
            used = self.cost_on(day=day_key, **filters)
        elif scope == "month":
            used = self.month_total(day_key[:7], **filters)
        else:
            raise ValueError(f"scope 只支持 'day' / 'month'，收到 {scope!r}")
        # 同 ring_growth：先取整再比阈值，避免浮点误差把「正好 80%」判成越线。
        ratio = round(used / budget, 6)
        return {
            "day": day_key,
            "scope": scope,
            "used_yuan": round(used, 6),
            "budget_yuan": round(budget, 6),
            "ratio": ratio,
            "threshold": ALERT_BUDGET_WATERLINE,
            "breached": ratio > ALERT_BUDGET_WATERLINE,
        }

    def budget_alerts(
        self,
        day: Any = None,
        daily_budget_yuan: Optional[float] = None,
        monthly_budget_yuan: Optional[float] = None,
        previous_day: Any = None,
        **filters: Any
    ) -> List[Dict[str, Any]]:
        """两条告警线一起过一遍，只返回**真正越线**的那些。

        * ``cost_ring_growth``：成本环比增长 > 10%（恒定检查，不需要预算）。
        * ``budget_waterline``：预算水位 > 80%（传了对应预算才检查）。
        """
        alerts: List[Dict[str, Any]] = []
        growth = self.ring_growth(day=day, previous_day=previous_day, **filters)
        if growth["breached"]:
            percent = growth["growth_ratio"] * 100
            alerts.append({
                "code": "cost_ring_growth",
                "level": "warn",
                "message": (
                    f"{growth['day']} LLM 成本 ¥{growth['cost_yuan']:.4f}，"
                    f"较 {growth['previous_day']} 的 ¥{growth['previous_cost_yuan']:.4f} "
                    f"环比增长 {percent:.1f}%，超过 {ALERT_RING_GROWTH_RATIO:.0%} 告警线。"
                ),
                **growth,
            })
        for scope, budget in (("day", daily_budget_yuan), ("month", monthly_budget_yuan)):
            if budget is None:
                continue
            waterline = self.budget_waterline(budget, day=day, scope=scope, **filters)
            if waterline["breached"]:
                scope_label = "当日" if scope == "day" else "当月"
                alerts.append({
                    "code": "budget_waterline",
                    "level": "warn",
                    "message": (
                        f"{scope_label}预算水位 {waterline['ratio']:.1%}"
                        f"（已用 ¥{waterline['used_yuan']:.4f} / 预算 ¥{waterline['budget_yuan']:.2f}），"
                        f"超过 {ALERT_BUDGET_WATERLINE:.0%} 告警线。"
                    ),
                    **waterline,
                })
        return alerts

    # ------------------------------------------------------------ 维护
    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._unknown_models.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)


#: 全进程共用的成本日表。
llm_cost_ledger = LlmCostLedger()


# --------------------------------------------------------------------- 接入辅助


def active_data_source_id() -> str:
    """当前激活的数据源标识，用于成本按数据源拆分。

    本系统支持 7 种引擎、可随时切源，成本不按数据源拆就没法回答
    「哪个源最烧钱」。取不到一律返回 ``"unknown"``——记账**绝不允许**因为
    数据源探测失败而抛异常。
    """
    try:
        from app.service.data_source_manager import data_source_manager

        active = data_source_manager.active_id
        if active:
            return _label(active)
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.service.db_service import db_service

        if getattr(db_service, "real_engine", None) is None:
            return "demo"
        return _label(getattr(db_service, "active_db_type", None))
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def record_llm_usage(**kwargs: Any) -> Optional[DailyCostRow]:
    """给调用点用的**永不抛异常**的记账入口。

    计量失败只记日志、返回 ``None``，绝不影响问数主流程——一次记账失误不值得
    让用户的查询挂掉。调用点自身仍应再包一层 try（纵深防御）。
    """
    try:
        return llm_cost_ledger.record(**kwargs)
    except Exception as error:  # noqa: BLE001
        print(f"[LLM Cost] 记账失败（已忽略，不影响问数）：{error}")
        return None
