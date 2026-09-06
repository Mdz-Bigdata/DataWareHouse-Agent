"""Portable EXPLAIN estimates and configurable query cost budgets."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


class ExplainBudgetError(ValueError):
    """Raised when an EXPLAIN estimate exceeds a configured budget."""


@dataclass(frozen=True)
class ExplainEstimate:
    estimated_cost: float | None
    estimated_rows: int | None
    source: str

    def to_state(self) -> dict[str, float | int | str | None]:
        return {
            "estimated_cost": self.estimated_cost,
            "estimated_rows": self.estimated_rows,
            "source": self.source,
        }


def summarize_explain(rows: list[dict[str, Any]], dialect: str) -> ExplainEstimate:
    """Extract no raw plan text—only bounded numeric estimates for observability."""

    normalized = [{str(key).lower(): value for key, value in row.items()} for row in rows]
    scans: list[tuple[float, float | None]] = []
    for row in normalized:
        row_estimate = next(
            (
                value
                for key in ("rows", "cardinality", "estimated_rows")
                if (value := _number(row.get(key))) is not None
            ),
            None,
        )
        if row_estimate is not None:
            scans.append((row_estimate, _number(row.get("filtered"))))
    if scans:
        running = 1.0
        total_cost = 0.0
        for row_estimate, filtered in scans:
            selectivity = max(0.0, min(1.0, (filtered if filtered is not None else 100.0) / 100))
            running *= max(1.0, row_estimate * selectivity)
            total_cost += running
        return ExplainEstimate(
            estimated_cost=_finite(total_cost),
            estimated_rows=int(sum(row_estimate for row_estimate, _ in scans)),
            source=f"{dialect}:rows",
        )

    plan_text = "\n".join(str(value) for row in normalized for value in row.values())
    costs = [float(value) for value in re.findall(r"cost=\d+(?:\.\d+)?\.\.(\d+(?:\.\d+)?)", plan_text)]
    text_rows = [int(value) for value in re.findall(r"\brows=(\d+)\b", plan_text)]
    if costs or text_rows:
        return ExplainEstimate(
            estimated_cost=_finite(max(costs)) if costs else float(max(text_rows)),
            estimated_rows=max(text_rows) if text_rows else None,
            source=f"{dialect}:plan_text",
        )
    return ExplainEstimate(estimated_cost=None, estimated_rows=None, source=f"{dialect}:unknown")


def enforce_explain_budget(
    estimate: ExplainEstimate,
    *,
    max_cost: float,
    max_rows: int,
) -> None:
    if max_cost <= 0 or max_rows <= 0:
        raise ValueError("EXPLAIN 成本预算必须大于 0")
    if estimate.estimated_cost is None or estimate.estimated_rows is None:
        raise ExplainBudgetError("EXPLAIN 未返回可验证的成本估算，已拒绝执行")
    if estimate.estimated_cost > max_cost:
        raise ExplainBudgetError(
            f"EXPLAIN 估算成本 {estimate.estimated_cost:.2f} 超过预算 {max_cost:.2f}"
        )
    if estimate.estimated_rows > max_rows:
        raise ExplainBudgetError(
            f"EXPLAIN 估算扫描行数 {estimate.estimated_rows} 超过预算 {max_rows}"
        )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _finite(value: float) -> float:
    return min(value, float(2**63 - 1)) if math.isfinite(value) else float(2**63 - 1)
