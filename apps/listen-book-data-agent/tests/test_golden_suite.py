from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.metadata.schema_catalog import (
    build_domain_metadata,
    load_meta_config,
    parse_mysql_ddl,
)
from app.services.golden_suite_service import (
    GoldenSuiteGateError,
    GoldenSuiteService,
    GoldenSuiteSubject,
    load_golden_suite,
    require_golden_suite_pass,
)
from app.services.meta_knowledge_service import MetaKnowledgeService

ROOT = Path(__file__).parents[1]
SUITE_PATH = ROOT / "conf" / "domains" / "audio" / "golden_suite.yaml"


class FakeEmbedding:
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1)] for index, _ in enumerate(texts)]


class FakeRepository:
    def __init__(self, items: list, *, delay: float = 0):
        self.items = items
        self.delay = delay

    async def search(
        self,
        embedding: list[float],
        score_threshold: float = 0.6,
        limit: int = 10,
    ) -> list:
        del embedding, score_threshold, limit
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.items


def _subject(
    build_id: str,
    *,
    repository_delay: float = 0,
    omit_metric: str | None = None,
) -> GoldenSuiteSubject:
    config_paths = [
        ROOT / "conf" / "domains" / "audio" / "semantics.yaml",
        ROOT / "conf" / "domains" / "audio" / "relationships.yaml",
        ROOT / "conf" / "domains" / "audio" / "metrics.yaml",
    ]
    config = load_meta_config(*config_paths)
    metadata = build_domain_metadata(
        parse_mysql_ddl(ROOT / "tools" / "audio_data" / "sql" / "audio.sql"),
        config,
    )
    tables = [replace(item, build_id=build_id) for item in metadata.tables]
    columns = [replace(item, build_id=build_id) for item in metadata.columns]
    relationships = [
        replace(item, build_id=build_id) for item in metadata.relationships
    ]
    metrics = MetaKnowledgeService._metric_infos(config, build_id)
    searchable_columns = [item for item in columns if not item.sensitive]
    searchable_metrics = [item for item in metrics if item.id != omit_metric]
    return GoldenSuiteSubject(
        build_id=build_id,
        tables=tables,
        columns=columns,
        metrics=searchable_metrics,
        relationships=relationships,
        column_repository=FakeRepository(
            searchable_columns,
            delay=repository_delay,
        ),
        metric_repository=FakeRepository(
            searchable_metrics,
            delay=repository_delay,
        ),
    )


@pytest.mark.asyncio
async def test_golden_suite_passes_complete_candidate():
    suite = load_golden_suite(SUITE_PATH)
    report = await GoldenSuiteService(FakeEmbedding()).evaluate(
        suite=suite,
        candidate=_subject("candidate"),
        baseline=None,
    )

    assert report["passed"] is True
    assert report["candidate"]["semantic_accuracy"] == 1
    assert report["safety_accuracy"] == 1
    assert all(
        len(item["latency_samples_ms"])
        == GoldenSuiteService.LATENCY_MEASUREMENT_ROUNDS
        for item in report["candidate"]["semantic_cases"]
    )
    assert {item["category"] for item in report["candidate"]["semantic_cases"]} == {
        "simple_aggregate",
        "join",
        "nested",
    }
    assert {item["dialect"] for item in report["dialect_cases"]} == {
        "mysql",
        "postgres",
        "clickhouse",
        "doris",
    }
    require_golden_suite_pass(report)


@pytest.mark.asyncio
async def test_golden_suite_rejects_semantic_regression():
    suite = load_golden_suite(SUITE_PATH)
    report = await GoldenSuiteService(FakeEmbedding()).evaluate(
        suite=suite,
        candidate=_subject("candidate", omit_metric="play_count"),
        baseline=_subject("active"),
    )

    assert report["passed"] is False
    assert report["candidate"]["semantic_accuracy"] < report["baseline"][
        "semantic_accuracy"
    ]
    with pytest.raises(GoldenSuiteGateError, match="语义正确率"):
        require_golden_suite_pass(report)


@pytest.mark.asyncio
async def test_golden_suite_rejects_p95_latency_regression():
    suite = load_golden_suite(SUITE_PATH)
    report = await GoldenSuiteService(FakeEmbedding()).evaluate(
        suite=suite,
        candidate=_subject("candidate", repository_delay=0.02),
        baseline=_subject("active", repository_delay=0.001),
    )

    assert report["passed"] is False
    assert report["candidate"]["semantic_accuracy"] == 1
    assert any("P95" in failure for failure in report["failures"])


def test_golden_suite_manifest_has_unique_complete_coverage():
    suite = load_golden_suite(SUITE_PATH)
    ids = [
        *(case.id for case in suite.semantic_cases),
        *(case.id for case in suite.multi_turn_cases),
        *(case.id for case in suite.dialect_cases),
        *(case.id for case in suite.attack_cases),
    ]

    assert len(ids) == len(set(ids))
    assert suite.thresholds.safety_accuracy == 1
    assert suite.thresholds.max_p95_regression_ratio == 1.2
