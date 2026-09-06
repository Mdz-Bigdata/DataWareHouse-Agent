# Evidence Grounding Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent general-purpose agents from emitting unsupported external facts unless the current turn contains valid server-recorded tool evidence.

**Architecture:** Add a focused grounding package containing evidence types, a per-turn ledger, fact-requirement resolution, and a response gate. Extend runtime tool metadata and audit state so successful tools can produce evidence, then integrate the gate at the AssistantAgentRunner output boundary while preserving ordinary streaming for clearly non-factual work.

**Tech Stack:** Python 3.11+, dataclasses, AgentScope runtime events, pytest/pytest-asyncio.

---

### Task 1: Evidence domain model and ledger

**Files:**
- Create: `app/services/ai/grounding/models.py`
- Create: `app/services/ai/grounding/ledger.py`
- Test: `tests/ai/grounding/test_evidence_ledger.py`

- [ ] Write failing tests proving only successful, non-empty tool results create receipts and that receipts are scoped by evidence type.
- [ ] Run `venv/bin/python -m pytest tests/ai/grounding/test_evidence_ledger.py -q` and verify failure because the grounding package is absent.
- [ ] Implement immutable evidence types/receipts and a per-turn ledger with `record_success()` and `has_valid_evidence()`.
- [ ] Re-run the focused tests and verify they pass.

### Task 2: Generic fact requirement and response decision

**Files:**
- Create: `app/services/ai/grounding/policy.py`
- Test: `tests/ai/grounding/test_grounding_policy.py`

- [ ] Write failing tests for stable/non-factual pass-through, evidence-required request sources, unknown-source structured fact output, false tool-use claims, and matching-evidence pass-through.
- [ ] Run the focused policy tests and verify failure for missing policy behavior.
- [ ] Implement source-to-evidence contracts and a generic output-risk classifier based on structural factual claims and execution claims, without business-domain field lists.
- [ ] Re-run policy tests and verify they pass.

### Task 3: Runtime tool evidence metadata

**Files:**
- Modify: `app/services/ai/runtime/agentscope/tools.py`
- Modify: `app/services/ai/runtime/agentscope/event_stream.py`
- Test: `tests/ai/runtime/test_agentscope_tool_evidence.py`

- [ ] Write failing tests showing `RuntimeToolSpec` exposes evidence metadata and successful invocation can register a receipt while errors cannot.
- [ ] Run the focused runtime tests and verify the expected failure.
- [ ] Add backward-compatible default evidence metadata and ledger state plumbing.
- [ ] Re-run focused runtime and existing AgentScope foundation tests.

### Task 4: Assistant runner hard gate

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/runners/test_assistant_agent_data_guard.py`

- [ ] Write the screenshot regression: an unknown-routed request produces a numeric ranking table and a false ChatBI claim without tool evidence; assert the candidate answer is withheld.
- [ ] Add regressions for unrelated/failed tool attempts, valid matching evidence, stable knowledge, and explicitly hypothetical examples.
- [ ] Run focused tests and verify the screenshot regression fails under the old guard.
- [ ] Integrate requirement resolution, buffered risky responses, ledger inspection, and server-authored safe fallback.
- [ ] Re-run focused tests and keep existing data-guard behavior green.

### Task 5: Tool capability registration and regression validation

**Files:**
- Modify: relevant registrations under `app/services/ai/tools/registry.py` and delegation/knowledge/file/runtime tool factories discovered during implementation.
- Test: `tests/ai/test_tool_nudge_policy.py`
- Test: `tests/ai/executors/test_chat_executor.py`

- [ ] Add representative evidence metadata for internal data, internal knowledge, public web, runtime state, user files, and conversation memory without coupling policy code to tool names.
- [ ] Add one end-to-end test per evidence type confirming a matching successful receipt permits grounded output.
- [ ] Run targeted runner, tool-nudge, request-decision, and executor suites.
- [ ] Run `git diff --check` and inspect the final diff for unrelated changes.

