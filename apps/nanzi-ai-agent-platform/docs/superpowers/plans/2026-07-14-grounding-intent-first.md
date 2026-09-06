# Grounding Intent-First Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each checkbox in order. This workspace does not auto-commit or run service restart scripts.

**Goal:** Remove output-keyword-only scrutiny from GENERAL while preserving evidence requirements derived from the user's request intent.

**Architecture:** Keep `resolve_request_decision()` as the source-of-truth for request evidence intent, `policy.py` as the output/evidence decision engine, and `GroundingService` as the audit facade. GENERAL becomes direct streaming unless the user query is deterministically upgraded to an evidence-required source.

**Tech Stack:** Python 3, dataclasses, pytest.

---

### Task 1: Lock the intent-first contract

**Files:**
- Modify: `tests/ai/grounding/test_grounding_policy.py`
- Modify: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [x] Change the policy test so GENERAL alone does not scrutinize model-added dynamic wording.
- [x] Add a runner test proving a GENERAL route hint is upgraded for a dynamic public user query.
- [x] Run the focused tests and verify they fail for the old output-only fallback.

### Task 2: Remove GENERAL output-only scrutiny

**Files:**
- Modify: `app/services/ai/grounding/policy.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`

- [x] Remove `scrutinize_dynamic_output` from `FactRequirement` and policy evaluation.
- [x] Reuse `resolve_request_decision()` to upgrade GENERAL route hints only when the user query has an evidence-required deterministic source.
- [x] Remove GENERAL end-of-stream text accumulation and audit branches.
- [x] Run policy, Main streaming, resume, and service tests.

### Task 3: Documentation and regression

**Files:**
- Modify: `docs/platform-grounding-gate.md`
- Modify: `tests/CHECKLIST.md`

- [x] Document intent-first GENERAL behavior and retained UNKNOWN fallback.
- [x] Run the focused grounding/MCP/KnowledgeAgent suite and ChatBI grounding subset.
- [x] Run `py_compile` and `git diff --check`.
- [x] Do not run `./dev.sh`; leave restart to the user.
