"""Deterministic pre-activation gate for versioned semantic builds."""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.retrieval import (
    allowed_columns,
    lexical_rank,
    merge_retrieval_results,
    recall_terms,
)
from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.relationship_info import RelationshipInfo
from app.entities.table_info import TableInfo
from app.services.conversation_context_service import (
    ConversationTurnContext,
    resolve_standalone_question,
)
from app.services.embedding_batch_service import embed_documents_batched
from app.services.recall_test_service import extract_terms
from app.services.sql_guard import (
    SQLSafetyError,
    extract_filter_only_columns,
    extract_sensitive_columns,
    validate_and_normalize_sql,
)


class GoldenSuiteGateError(RuntimeError):
    """Raised when a candidate semantic build does not meet release gates."""


class SemanticGoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: Literal["simple_aggregate", "join", "nested"]
    question: str = Field(min_length=1)
    expected_metric_ids: list[str] = Field(default_factory=list)
    expected_column_ids: list[str] = Field(default_factory=list)
    reference_sql: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_expected_semantics(self) -> SemanticGoldenCase:
        if not self.expected_metric_ids and not self.expected_column_ids:
            raise ValueError("semantic case must declare at least one expected semantic ID")
        return self


class MultiTurnGoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    ancestor_question: str = Field(min_length=1)
    follow_up: str = Field(min_length=1)
    expected_contains: list[str] = Field(min_length=1)


class DialectGoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    dialect: Literal["mysql", "postgres", "clickhouse", "doris"]
    sql: str = Field(min_length=1)


class AttackGoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    dialect: Literal["mysql", "postgres", "clickhouse", "doris"] = "mysql"


class GoldenSuiteThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_semantic_accuracy: float = Field(default=0.95, ge=0, le=1)
    safety_accuracy: float = Field(default=1.0, ge=0, le=1)
    max_p95_regression_ratio: float = Field(default=1.2, ge=1)


class GoldenSuiteDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    thresholds: GoldenSuiteThresholds = Field(default_factory=GoldenSuiteThresholds)
    semantic_cases: list[SemanticGoldenCase] = Field(min_length=1)
    multi_turn_cases: list[MultiTurnGoldenCase] = Field(min_length=1)
    dialect_cases: list[DialectGoldenCase] = Field(min_length=1)
    attack_cases: list[AttackGoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_ids_and_coverage(self) -> GoldenSuiteDefinition:
        cases = [
            *self.semantic_cases,
            *self.multi_turn_cases,
            *self.dialect_cases,
            *self.attack_cases,
        ]
        ids = [case.id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Golden Suite case IDs must be unique")
        categories = {case.category for case in self.semantic_cases}
        if categories != {"simple_aggregate", "join", "nested"}:
            raise ValueError("Golden Suite must cover simple_aggregate, join and nested")
        dialects = {case.dialect for case in self.dialect_cases}
        if dialects != {"mysql", "postgres", "clickhouse", "doris"}:
            raise ValueError("Golden Suite must cover mysql, postgres, clickhouse and doris")
        return self


@dataclass(frozen=True)
class GoldenSuiteSubject:
    build_id: str
    tables: list[TableInfo]
    columns: list[ColumnInfo]
    metrics: list[MetricInfo]
    relationships: list[RelationshipInfo]
    column_repository: object
    metric_repository: object


def load_golden_suite(path: Path) -> GoldenSuiteDefinition:
    if not path.is_file():
        raise FileNotFoundError(f"Golden Suite 配置不存在：{path}")
    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    return GoldenSuiteDefinition.model_validate(raw)


class GoldenSuiteService:
    """Compare a candidate index with the active one before any alias switch."""

    LATENCY_MEASUREMENT_ROUNDS = 3

    def __init__(self, embedding_client: object):
        self.embedding_client = embedding_client

    async def evaluate(
        self,
        *,
        suite: GoldenSuiteDefinition,
        candidate: GoldenSuiteSubject,
        baseline: GoldenSuiteSubject | None,
    ) -> dict:
        retrieval_inputs = await self._build_retrieval_inputs(suite.semantic_cases)

        # Warm every retrieval vector on both collection paths before measuring.
        # A candidate collection has never served traffic, while the active alias is
        # normally hot; comparing their first reads would reject healthy builds.
        await self._warm(candidate, retrieval_inputs)
        if baseline is not None:
            await self._warm(baseline, retrieval_inputs)

        candidate_report = await self._evaluate_subject(
            suite=suite,
            subject=candidate,
            retrieval_inputs=retrieval_inputs,
        )
        baseline_report = (
            await self._evaluate_subject(
                suite=suite,
                subject=baseline,
                retrieval_inputs=retrieval_inputs,
            )
            if baseline is not None
            else None
        )
        safety_results = self._evaluate_attacks(suite, candidate)
        dialect_results = self._evaluate_dialects(suite, candidate)
        safety_accuracy = _accuracy(safety_results)

        thresholds = suite.thresholds
        failures: list[str] = []
        if candidate_report["semantic_accuracy"] < thresholds.minimum_semantic_accuracy:
            failures.append(
                "候选语义正确率低于最低门槛 "
                f"{thresholds.minimum_semantic_accuracy:.1%}"
            )
        if (
            baseline_report is not None
            and candidate_report["semantic_accuracy"]
            < baseline_report["semantic_accuracy"]
        ):
            failures.append("候选语义正确率低于当前活跃版本")
        if safety_accuracy < thresholds.safety_accuracy:
            failures.append("安全攻击用例未达到 100% 通过")
        if not all(result["passed"] for result in dialect_results):
            failures.append("方言兼容用例未全部通过")
        if baseline_report is not None:
            allowed_p95 = (
                baseline_report["p95_latency_ms"]
                * thresholds.max_p95_regression_ratio
            )
            if candidate_report["p95_latency_ms"] > allowed_p95:
                failures.append(
                    "候选 P95 延迟超过活跃版本允许上限 "
                    f"{thresholds.max_p95_regression_ratio:.2f}x"
                )

        return {
            "suite_version": suite.version,
            "candidate_build_id": candidate.build_id,
            "baseline_build_id": baseline.build_id if baseline is not None else None,
            "passed": not failures,
            "failures": failures,
            "thresholds": thresholds.model_dump(mode="json"),
            "candidate": candidate_report,
            "baseline": baseline_report,
            "safety_accuracy": safety_accuracy,
            "safety_cases": safety_results,
            "dialect_cases": dialect_results,
        }

    async def _build_retrieval_inputs(
        self,
        cases: list[SemanticGoldenCase],
    ) -> list[tuple[list[str], list[list[float]]]]:
        inputs: list[tuple[list[str], list[list[float]]]] = []
        for case in cases:
            terms = recall_terms(
                extract_terms(case.question),
                None,
                case.question,
            )
            inputs.append(
                (
                    terms,
                    await embed_documents_batched(self.embedding_client, terms),
                )
            )
        return inputs

    @staticmethod
    async def _warm(
        subject: GoldenSuiteSubject,
        retrieval_inputs: list[tuple[list[str], list[list[float]]]],
    ) -> None:
        for _, embeddings in retrieval_inputs:
            for embedding in embeddings:
                await asyncio.gather(
                    subject.column_repository.search(embedding, limit=24),
                    subject.metric_repository.search(embedding, limit=12),
                )

    async def _evaluate_subject(
        self,
        *,
        suite: GoldenSuiteDefinition,
        subject: GoldenSuiteSubject,
        retrieval_inputs: list[tuple[list[str], list[list[float]]]],
    ) -> dict:
        semantic_results = []
        for case, (terms, embeddings) in zip(
            suite.semantic_cases,
            retrieval_inputs,
            strict=True,
        ):
            semantic_results.append(
                await self._evaluate_semantic_case(
                    case,
                    subject,
                    terms,
                    embeddings,
                )
            )
        multi_turn_results = self._evaluate_multi_turn(suite)
        all_results = [*semantic_results, *multi_turn_results]
        return {
            "build_id": subject.build_id,
            "semantic_accuracy": _accuracy(all_results),
            "p95_latency_ms": _p95(
                [result["latency_ms"] for result in semantic_results]
            ),
            "semantic_cases": semantic_results,
            "multi_turn_cases": multi_turn_results,
        }

    @staticmethod
    async def _evaluate_semantic_case(
        case: SemanticGoldenCase,
        subject: GoldenSuiteSubject,
        terms: list[str],
        embeddings: list[list[float]],
    ) -> dict:
        started_at = time.perf_counter()
        try:
            latency_samples_ms: list[float] = []
            vector_column_groups = []
            vector_metric_groups = []
            for _ in range(GoldenSuiteService.LATENCY_MEASUREMENT_ROUNDS):
                retrieval_started_at = time.perf_counter()
                vector_column_groups, vector_metric_groups = await asyncio.gather(
                    asyncio.gather(
                        *(
                            subject.column_repository.search(embedding, limit=24)
                            for embedding in embeddings
                        )
                    ),
                    asyncio.gather(
                        *(
                            subject.metric_repository.search(embedding, limit=12)
                            for embedding in embeddings
                        )
                    ),
                )
                latency_samples_ms.append(_elapsed_ms(retrieval_started_at))
            lexical_columns = lexical_rank(
                (item for item in subject.columns if not item.sensitive),
                terms,
                lambda item: " ".join(
                    [item.name, item.description, *item.alias]
                ),
                limit=24,
            )
            lexical_metrics = lexical_rank(
                subject.metrics,
                terms,
                lambda item: " ".join(
                    [item.name, item.description, item.formula, *item.alias]
                ),
                limit=12,
            )
            columns = merge_retrieval_results(
                lexical_columns,
                allowed_columns(
                    item for group in vector_column_groups for item in group
                ),
                limit=24,
            )
            metrics = merge_retrieval_results(
                lexical_metrics,
                (item for group in vector_metric_groups for item in group),
                limit=12,
            )
            retrieval_latency_ms = round(statistics.median(latency_samples_ms), 3)
            table_infos = _table_states(subject.tables, subject.columns)
            validate_and_normalize_sql(
                case.reference_sql,
                table_infos,
                1000,
                sensitive_columns=extract_sensitive_columns(table_infos),
                filter_only_columns=extract_filter_only_columns(table_infos),
                relationships=[asdict(item) for item in subject.relationships],
                dialect="mysql",
                allowed_functions=["*"],
            )
            recalled_columns = {item.id for item in columns}
            recalled_metrics = {item.id for item in metrics}
            metric_supported_columns = {
                column_id
                for metric in metrics
                for column_id in [
                    *metric.relevant_columns,
                    *metric.dimensions,
                    metric.time_column,
                    metric.currency_column,
                ]
                if column_id
            }
            expected_columns = set(case.expected_column_ids)
            expected_metrics = set(case.expected_metric_ids)
            matching_build = all(
                item.build_id == subject.build_id for item in [*columns, *metrics]
            )
            missing_columns = sorted(
                expected_columns - recalled_columns - metric_supported_columns
            )
            missing_metrics = sorted(expected_metrics - recalled_metrics)
            passed = not missing_columns and not missing_metrics and matching_build
            return {
                "id": case.id,
                "category": case.category,
                "passed": passed,
                "latency_ms": retrieval_latency_ms,
                "latency_samples_ms": latency_samples_ms,
                "missing_column_ids": missing_columns,
                "missing_metric_ids": missing_metrics,
                "build_isolation_passed": matching_build,
                "recalled_column_ids": sorted(recalled_columns),
                "recalled_metric_ids": sorted(recalled_metrics),
                "metric_supported_column_ids": sorted(metric_supported_columns),
            }
        except Exception as exc:
            return {
                "id": case.id,
                "category": case.category,
                "passed": False,
                "latency_ms": _elapsed_ms(started_at),
                "error": _safe_error(exc),
            }

    @staticmethod
    def _evaluate_multi_turn(suite: GoldenSuiteDefinition) -> list[dict]:
        results: list[dict] = []
        for case in suite.multi_turn_cases:
            turn = ConversationTurnContext(
                trace_id=f"golden-{case.id}",
                standalone_question=case.ancestor_question,
                query_plan={},
                verified_sql_template=None,
                answer_summary=None,
            )
            resolution = resolve_standalone_question(case.follow_up, (turn,))
            passed = resolution.confidence == "high" and all(
                value in resolution.standalone_question
                for value in case.expected_contains
            )
            results.append(
                {
                    "id": case.id,
                    "category": "multi_turn",
                    "passed": passed,
                    "standalone_question": resolution.standalone_question,
                    "confidence": resolution.confidence,
                }
            )
        return results

    @staticmethod
    def _evaluate_dialects(
        suite: GoldenSuiteDefinition,
        subject: GoldenSuiteSubject,
    ) -> list[dict]:
        table_infos = _table_states(subject.tables, subject.columns)
        relationships = [asdict(item) for item in subject.relationships]
        results: list[dict] = []
        for case in suite.dialect_cases:
            try:
                validate_and_normalize_sql(
                    case.sql,
                    table_infos,
                    1000,
                    sensitive_columns=extract_sensitive_columns(table_infos),
                    filter_only_columns=extract_filter_only_columns(table_infos),
                    relationships=relationships,
                    dialect=case.dialect,
                    allowed_functions=["*"],
                )
                results.append(
                    {"id": case.id, "dialect": case.dialect, "passed": True}
                )
            except Exception as exc:
                results.append(
                    {
                        "id": case.id,
                        "dialect": case.dialect,
                        "passed": False,
                        "error": _safe_error(exc),
                    }
                )
        return results

    @staticmethod
    def _evaluate_attacks(
        suite: GoldenSuiteDefinition,
        subject: GoldenSuiteSubject,
    ) -> list[dict]:
        table_infos = _table_states(subject.tables, subject.columns)
        relationships = [asdict(item) for item in subject.relationships]
        results: list[dict] = []
        for case in suite.attack_cases:
            try:
                validate_and_normalize_sql(
                    case.sql,
                    table_infos,
                    1000,
                    sensitive_columns=extract_sensitive_columns(table_infos),
                    filter_only_columns=extract_filter_only_columns(table_infos),
                    relationships=relationships,
                    dialect=case.dialect,
                    allowed_functions=["*"],
                )
            except SQLSafetyError as exc:
                results.append(
                    {
                        "id": case.id,
                        "passed": True,
                        "rejection": str(exc)[:300],
                    }
                )
            except Exception as exc:
                results.append(
                    {"id": case.id, "passed": False, "error": _safe_error(exc)}
                )
            else:
                results.append(
                    {
                        "id": case.id,
                        "passed": False,
                        "error": "攻击 SQL 未被拒绝",
                    }
                )
        return results


def require_golden_suite_pass(report: dict) -> None:
    if report.get("passed"):
        return
    failures = "；".join(str(item) for item in report.get("failures", []))
    raise GoldenSuiteGateError(f"Golden Suite 准入失败：{failures}")


def _table_states(
    tables: list[TableInfo],
    columns: list[ColumnInfo],
) -> list[dict]:
    columns_by_table: dict[str, list[dict]] = {}
    for column in columns:
        columns_by_table.setdefault(column.table_id, []).append(asdict(column))
    return [
        {**asdict(table), "columns": columns_by_table.get(table.id, [])}
        for table in tables
    ]


def _accuracy(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(1 for result in results if result["passed"]) / len(results)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 3)


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]
