"""Run the guarded, end-to-end query accuracy benchmark.

The command is deliberately opt-in because the query endpoint sends schema and
question context to the configured LLM provider. Example:

    $env:RUN_QUERY_ACCURACY_EVAL='1'
    $env:LISTENBOOK_EVAL_TOKEN='<local JWT>'
    uv run python -m app.scripts.evaluate_query_accuracy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import median
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import text

from app.clients.mysql_client_manager import dw_mysql_client_manager, meta_mysql_client_manager
from app.core.security import create_access_token
from app.repositories.mysql.query_trace_repository import QueryTraceRepository
from app.services.query_accuracy_benchmark import (
    CORE_ACCURACY_CASES,
    DSL_COMPARISON_CASES,
    QueryAccuracyCase,
    build_all_metric_accuracy_cases,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 180


def _require_explicit_opt_in() -> None:
    if os.getenv("RUN_QUERY_ACCURACY_EVAL") != "1":
        raise RuntimeError("评测会调用已配置的 LLM。请在确认后设置 RUN_QUERY_ACCURACY_EVAL=1。")


def _select_cases(cases: tuple[QueryAccuracyCase, ...]) -> tuple[QueryAccuracyCase, ...]:
    """Optionally run a comma-separated subset for focused regression checks."""

    requested = {
        item.strip()
        for item in os.getenv("LISTENBOOK_ACCURACY_CASE_IDS", "").split(",")
        if item.strip()
    }
    if not requested:
        return cases
    selected = tuple(case for case in cases if case.case_id in requested)
    missing = requested - {case.case_id for case in selected}
    if missing:
        raise RuntimeError(f"未知评测用例: {', '.join(sorted(missing))}")
    return selected


def _post_query(question: str, token: str, api_url: str | None = None) -> dict:
    api_url = (api_url or os.getenv("LISTENBOOK_API_URL", DEFAULT_API_URL)).rstrip("/")
    timeout = int(os.getenv("LISTENBOOK_ACCURACY_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
    payload = json.dumps({"query": question}).encode("utf-8")
    request = Request(
        f"{api_url}/api/query/sync",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicit local URL
        return json.loads(response.read().decode("utf-8"))


async def _reference_values(
    cases: tuple[QueryAccuracyCase, ...],
) -> dict[str, tuple[list[list[object]], int]]:
    dw_mysql_client_manager.init_client()
    try:
        if dw_mysql_client_manager.session_factory is None:
            raise RuntimeError("数据仓库会话未初始化")
        values: dict[str, tuple[list[list[object]], int]] = {}
        async with dw_mysql_client_manager.session_factory() as session:
            for case in cases:
                started_at = time.perf_counter()
                result = await session.execute(text(case.reference_sql))
                values[case.case_id] = (
                    [list(row) for row in result.fetchall()],
                    max(1, round((time.perf_counter() - started_at) * 1000)),
                )
        return values
    finally:
        await dw_mysql_client_manager.close()


async def _local_admin_token() -> str:
    """使用本地元数据中的管理员生成短期评测 JWT，不打印令牌或密码。"""

    configured = os.getenv("LISTENBOOK_EVAL_TOKEN")
    if configured:
        return configured

    meta_mysql_client_manager.init_client()
    try:
        if meta_mysql_client_manager.session_factory is None:
            raise RuntimeError("元数据库会话未初始化")
        async with meta_mysql_client_manager.session_factory() as session:
            admin = (
                (
                    await session.execute(
                        text("SELECT id, username, role FROM users WHERE role = 'admin' LIMIT 1")
                    )
                )
                .mappings()
                .first()
            )
        if admin is None:
            raise RuntimeError("未找到管理员用户，无法执行 API 评测")
        return create_access_token(
            user_id=str(admin["id"]),
            username=str(admin["username"]),
            role=str(admin["role"]),
        )
    finally:
        await meta_mysql_client_manager.close()


async def _record_reference_sql(trace_id: str | None, sql: str, duration_ms: int) -> None:
    """Expose the evaluator truth query beside the generated SQL in Query Analysis."""

    if not trace_id:
        return
    meta_mysql_client_manager.init_client()
    try:
        if meta_mysql_client_manager.session_factory is None:
            raise RuntimeError("元数据库会话未初始化")
        async with meta_mysql_client_manager.session_factory() as session:
            await QueryTraceRepository(session).record_reference_sql(
                trace_id=trace_id,
                sql=sql,
                duration_ms=duration_ms,
            )
    finally:
        await meta_mysql_client_manager.close()


def _result_values(rows: list[object]) -> list[list[object]] | None:
    normalized: list[list[object]] = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        normalized.append(list(row.values()))
    return normalized


def _values_match(actual: list[list[object]] | None, expected: list[list[object]]) -> bool:
    if actual is None or len(actual) != len(expected):
        return False
    return all(
        _row_contains_expected_values(actual_row, expected_row)
        for actual_row, expected_row in zip(actual, expected, strict=True)
    )


def _row_contains_expected_values(actual_row: list[object], expected_row: list[object]) -> bool:
    """Allow extra display columns while requiring every requested value in order."""

    expected_index = 0
    for actual_value in actual_row:
        if expected_index < len(expected_row) and _value_matches(
            actual_value, expected_row[expected_index]
        ):
            expected_index += 1
    return expected_index == len(expected_row)


def _value_matches(actual: object, expected: object) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= 1e-9
    except (TypeError, ValueError):
        actual_text = str(actual)
        expected_text = str(expected)
        if _looks_like_datetime(actual_text) and _looks_like_datetime(expected_text):
            return actual_text.replace("T", " ") == expected_text.replace("T", " ")
        return actual_text == expected_text


def _looks_like_datetime(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value))


def _evaluate_case(
    case: QueryAccuracyCase,
    expected: list[list[object]],
    reference_duration_ms: int,
    token: str,
    api_url: str | None = None,
) -> dict:
    if _reference_is_time_dependent(case.reference_sql):
        expected, reference_duration_ms = asyncio.run(_reference_values((case,)))[case.case_id]
    started_at = time.perf_counter()
    try:
        response = _post_query(case.question, token, api_url)
        asyncio.run(
            _record_reference_sql(
                response.get("request_id"), case.reference_sql, reference_duration_ms
            )
        )
        actual = _result_values(response.get("rows") or [])
        passed = response.get("status") == "completed" and _values_match(actual, expected)
        return {
            **asdict(case),
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "status": response.get("status"),
            "error": response.get("error"),
            "sql": response.get("sql"),
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "generation_mode": response.get("generation_mode"),
            "generation_source": response.get("generation_source"),
            "query_dsl": response.get("query_dsl"),
            "dsl_fallback_reason": response.get("dsl_fallback_reason"),
            "dsl_attempts": response.get("dsl_attempts", 0),
            "sql_correction_attempts": response.get("sql_correction_attempts", 0),
            "llm_calls": response.get("llm_calls", 0),
        }
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {
            **asdict(case),
            "expected": expected,
            "actual": None,
            "passed": False,
            "status": "failed",
            "error": str(exc),
            "sql": None,
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "generation_mode": None,
            "generation_source": None,
            "query_dsl": None,
            "dsl_fallback_reason": None,
            "dsl_attempts": 0,
            "sql_correction_attempts": 0,
            "llm_calls": 0,
        }


def _reference_is_time_dependent(sql: str) -> bool:
    normalized = sql.upper()
    return any(
        expression in normalized
        for expression in ("CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME", "NOW()")
    )


def _summarize(cases: list[dict]) -> dict:
    total = len(cases)
    passed = sum(case["passed"] for case in cases)
    grouped: dict[str, list[dict]] = {}
    for case in cases:
        grouped.setdefault(str(case.get("intent") or "aggregate"), []).append(case)

    by_intent: dict[str, dict] = {}
    for intent, items in sorted(grouped.items()):
        intent_total = len(items)
        native = [
            item
            for item in items
            if str(item.get("generation_source") or "").startswith("dsl_")
            and item.get("generation_source") != "legacy_fallback"
        ]
        by_intent[intent] = {
            "total": intent_total,
            "passed": sum(item["passed"] for item in items),
            "accuracy": sum(item["passed"] for item in items) / intent_total,
            "native_dsl": len(native),
            "native_dsl_coverage": len(native) / intent_total,
            "fallbacks": sum(item.get("generation_source") == "legacy_fallback" for item in items),
        }
    intent_accuracies = [item["accuracy"] for item in by_intent.values()]
    durations = [int(case["duration_ms"]) for case in cases]
    llm_calls = [int(case.get("llm_calls") or 0) for case in cases]
    native = [
        case
        for case in cases
        if str(case.get("generation_source") or "").startswith("dsl_")
        and case.get("generation_source") != "legacy_fallback"
    ]
    return {
        "total": total,
        "passed": passed,
        "accuracy": passed / total if total else 0,
        "macro_accuracy": (
            sum(intent_accuracies) / len(intent_accuracies) if intent_accuracies else 0
        ),
        "median_duration_ms": median(durations) if durations else 0,
        "average_llm_calls": sum(llm_calls) / total if total else 0,
        "native_dsl_coverage": len(native) / total if total else 0,
        "fallbacks": sum(case.get("generation_source") == "legacy_fallback" for case in cases),
        "by_intent": by_intent,
    }


def _comparison_gates(legacy: dict, dsl: dict, dsl_cases: list[dict]) -> dict[str, bool]:
    dsl_by_intent = dsl["by_intent"]
    baseline_case_ids = {
        case.case_id for case in (*CORE_ACCURACY_CASES, *build_all_metric_accuracy_cases())
    }
    baseline_passed = all(
        case["passed"] for case in dsl_cases if case["case_id"] in baseline_case_ids
    )
    latency_ratio = (
        dsl["median_duration_ms"] / legacy["median_duration_ms"]
        if legacy["median_duration_ms"]
        else 1
    )
    llm_ratio = (
        dsl["average_llm_calls"] / legacy["average_llm_calls"] if legacy["average_llm_calls"] else 1
    )
    return {
        "baseline_62_passed": baseline_passed,
        "dsl_macro_accuracy_gte_95": dsl["macro_accuracy"] >= 0.95,
        "dsl_not_worse_than_legacy": dsl["macro_accuracy"] >= legacy["macro_accuracy"],
        "native_dsl_coverage_gte_90": dsl["native_dsl_coverage"] >= 0.90,
        "native_dsl_coverage_each_intent_gte_80": all(
            item["native_dsl_coverage"] >= 0.80 for item in dsl_by_intent.values()
        ),
        "median_latency_within_20_percent": latency_ratio <= 1.20,
        "average_llm_calls_within_20_percent": llm_ratio <= 1.20,
    }


def _comparison_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("artifacts") / f"dsl_comparison_{stamp}.json"


def _single_report_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("artifacts") / f"query_accuracy_report_{stamp}.json"


def main() -> None:
    _require_explicit_opt_in()
    parser = argparse.ArgumentParser(description="Evaluate one query API or a legacy/DSL pair.")
    parser.add_argument("--legacy-api-url", default=os.getenv("LISTENBOOK_LEGACY_API_URL"))
    parser.add_argument("--dsl-api-url", default=os.getenv("LISTENBOOK_DSL_API_URL"))
    args = parser.parse_args()
    if bool(args.legacy_api_url) != bool(args.dsl_api_url):
        raise RuntimeError("配对评测必须同时提供 --legacy-api-url 和 --dsl-api-url")
    metric_cases = build_all_metric_accuracy_cases()
    cases_to_run = _select_cases((*CORE_ACCURACY_CASES, *metric_cases, *DSL_COMPARISON_CASES))
    expected_values = asyncio.run(_reference_values(cases_to_run))
    token = asyncio.run(_local_admin_token())
    if args.legacy_api_url and args.dsl_api_url:
        legacy_cases = [
            _evaluate_case(
                case,
                expected_values[case.case_id][0],
                expected_values[case.case_id][1],
                token,
                args.legacy_api_url,
            )
            for case in cases_to_run
        ]
        dsl_cases = [
            _evaluate_case(
                case,
                expected_values[case.case_id][0],
                expected_values[case.case_id][1],
                token,
                args.dsl_api_url,
            )
            for case in cases_to_run
        ]
        legacy = _summarize(legacy_cases)
        dsl = _summarize(dsl_cases)
        gates = _comparison_gates(legacy, dsl, dsl_cases)
        report = {
            "mode": "legacy_vs_dsl",
            "target": 0.95,
            "legacy": {"api_url": args.legacy_api_url, "summary": legacy, "cases": legacy_cases},
            "dsl": {"api_url": args.dsl_api_url, "summary": dsl, "cases": dsl_cases},
            "gates": gates,
        }
        path = _comparison_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps({"legacy": legacy, "dsl": dsl, "gates": gates}, ensure_ascii=False))
        print(f"DSL 对比报告：{path}")
        if not all(gates.values()):
            raise SystemExit(1)
        return

    cases = [
        _evaluate_case(
            case,
            expected_values[case.case_id][0],
            expected_values[case.case_id][1],
            token,
        )
        for case in cases_to_run
    ]
    summary = _summarize(cases)
    report = {"target": 0.95, "summary": summary, "cases": cases}
    path = _single_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"详细报告：{path}")
    if summary["accuracy"] < report["target"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
