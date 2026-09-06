# GroundingService Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each checkbox in order. This workspace does not auto-commit or run service restart scripts.

**Goal:** Centralize grounding decisions and warning construction without changing streaming, reflection, or SQL-gate behavior.

**Architecture:** Keep `policy.py` as pure decision logic and `EvidenceLedger` as evidence storage. Add a small service that converts a policy decision into a reusable audit result, then make each runner delegate to it while retaining runner-specific orchestration.

**Tech Stack:** Python 3, dataclasses, AgentScope runner events, pytest.

---

### Task 1: Define the service contract

**Files:**
- Create: `app/services/ai/grounding/service.py`
- Create: `tests/ai/grounding/test_grounding_service.py`

- [x] Write failing tests for pass and warning audit results and standardized warning payloads.
- [x] Run the new test file and verify import failure.
- [x] Implement `GroundingAuditResult`, `GroundingService.audit()`, and `warning_chunk()`.
- [x] Re-run the service and policy tests.

### Task 2: Delegate Main and KnowledgeAgent

**Files:**
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/runners/test_knowledge_hallucination_guard.py`

- [x] Add delegation assertions that fail while runners call policy/message helpers directly.
- [x] Replace Main decision/message assembly with service audit results.
- [x] Replace KnowledgeAgent terminal message assembly with the service helper.
- [x] Verify streaming order and existing soft-warning tests remain green.

### Task 3: Delegate ChatBI

**Files:**
- Modify: `app/services/ai/runners/data_agent_runner.py`
- Modify: `app/services/ai/runners/chatbi/native_turn.py`
- Modify: `app/services/ai/runners/chatbi/synthesis.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [x] Change ChatBI adapter tests to assert a `GroundingAuditResult` contract.
- [x] Keep temporary scoped evidence construction in DataAgent and delegate the decision to `GroundingService.audit()`.
- [x] Make native and synthesis paths consume the audit result without changing stream order.
- [x] Run isolated ChatBI grounding/reuse tests and record unrelated full-suite failures separately.

### Task 4: Documentation and regression

**Files:**
- Modify: `docs/platform-grounding-gate.md`
- Modify: `tests/CHECKLIST.md`

- [x] Document the service boundary and runner-owned streaming behavior.
- [x] Run focused grounding, MCP, resume, KnowledgeAgent, and ChatBI tests.
- [x] Run `py_compile` and `git diff --check`.
- [x] Do not run `./dev.sh`; leave compile/restart to the user.
