from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_tool_log_display_exposes_golden_report_helpers():
    source = _source("frontend/src/utils/toolLogDisplay.ts")

    assert "export function resolveSavableSqlFromMessage" in source
    assert "export function canSaveGoldenReportFromMessage" in source
    assert "export function sqlToolLogResultHasDataRows" in source
    assert "export function resolveSavableSqlFromLog" in source


def test_embed_chat_message_level_golden_report_button():
    source = _source("frontend/src/views/EmbedChat.vue")

    assert "canSaveGoldenReportFromMessage(msg)" in source
    assert "handleSaveReportFromMessage(msg)" in source
    assert "添加黄金报表" in source
    assert "resolveSavableSqlFromLog(log)" in source
    assert "parseRequirementAnalysisFromMessage(agentMessage)" in source


def test_agent_debug_message_level_golden_report_button():
    source = _source("frontend/src/views/AgentDebug.vue")

    assert "canSaveGoldenReportFromMessage(msg)" in source
    assert "handleSaveReportFromMessage(msg)" in source
    assert "resolveSavableSqlFromLog(log)" in source
    assert "parseRequirementAnalysisFromMessage(agentMessage)" in source


def test_saved_report_defaults_extracts_requirement_analysis():
    source = _source("frontend/src/utils/savedReportDefaults.ts")

    assert "export function parseRequirementAnalysisFromMessage" in source
    assert "业务目标" in source
    assert "export function deriveSavedReportDescription" in source
    assert "export function deriveSavedReportTitle" in source
    assert "export function deriveSavedReportTagsInput" in source
