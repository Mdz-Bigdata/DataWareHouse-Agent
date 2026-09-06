# Grounding Gate Correlation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each checkbox in order. This workspace does not auto-commit or run service restart scripts.

**Goal:** Prevent unrelated, failed, or empty tool results from backing factual answers while making Main, Knowledge, ChatBI, MCP structured results, and external-resume paths follow the same soft-warning contract.

**Architecture:** Normalize evidence success and content markers in `EvidenceLedger`, require fact-bearing answers to correlate with matching receipts in `grounding.policy`, and adapt each runner boundary to supply or consume the same evidence contract. Preserve existing security and SQL hard gates.

**Tech Stack:** Python 3, AgentScope, MCP Python SDK, pytest.

---

### Task 1: Reject failed envelopes and strengthen receipt correlation

**Files:**
- Modify: `app/services/ai/grounding/models.py`
- Modify: `app/services/ai/grounding/ledger.py`
- Test: `tests/ai/grounding/test_evidence_ledger.py`

- [x] Add failing tests for dict `isError`, dict `state=error`, one weak Chinese marker, two-marker matches, and strong identifier matches.
- [x] Run `venv/bin/python -m pytest -q tests/ai/grounding/test_evidence_ledger.py` and verify the new tests fail for the reproduced reasons.
- [x] Extend receipts with strong marker digests and add a correlation method that accepts one strong marker or two ordinary markers.
- [x] Normalize dict/object failure fields before signing receipts.
- [x] Re-run the ledger tests and verify green.

### Task 2: Require content correlation and constrain empty success

**Files:**
- Modify: `app/services/ai/grounding/policy.py`
- Test: `tests/ai/grounding/test_grounding_policy.py`

- [x] Add failing tests for unrelated exact-type receipts, train-result/weather overlap, empty-result-plus-fabricated-facts, and valid train result correlation.
- [x] Verify RED with `venv/bin/python -m pytest -q tests/ai/grounding/test_grounding_policy.py`.
- [x] Replace unconditional exact-type passage for fact-bearing output with receipt correlation.
- [x] Allow empty receipts only for pure no-result language with no concrete fact values or tables.
- [x] Apply the same rule to UNKNOWN evidence groups and re-run the policy tests.

### Task 3: Preserve MCP structured content

**Files:**
- Modify: `app/services/ai/tools/mcp_client.py`
- Test: `tests/ai/tools/test_mcp_client_grounding.py`

- [x] Add failing SDK and Direct HTTP tests for `structuredContent`-only responses and `isError` responses without text content.
- [x] Verify RED with `venv/bin/python -m pytest -q tests/ai/tools/test_mcp_client_grounding.py`.
- [x] Return a serializable envelope containing both text and structured content; only use empty success when both are absent.
- [x] Re-run MCP grounding and session recovery tests.

### Task 4: Register external-execution resume evidence

**Files:**
- Modify: `app/services/ai/agent_service.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`
- Test: `tests/ai/runtime/test_agentscope_state_pending.py`

- [x] Add failing tests proving a successful submitted result uses the pending tool's evidence metadata and an error result does not.
- [x] Verify RED with the two focused test files.
- [x] Restore the scoped ledger, resolve the pending tool spec by name, and record only successful external result blocks before final grounding evaluation.
- [x] Re-run resume tests, including cross-process snapshot restoration.

### Task 5: Convert KnowledgeAgent final failure to soft warning

**Files:**
- Modify: `app/services/ai/runners/knowledge_agent_runner.py`
- Test: `tests/ai/runners/test_knowledge_hallucination_guard.py`
- Test: `tests/ai/runners/test_knowledge_agent_tools.py`

- [x] Add a failing test asserting the last reviewed answer is retained, one risk warning is appended, and no final blocking/error event is emitted.
- [x] Verify RED with the KnowledgeAgent test files.
- [x] Keep reflection retries but replace the final refusal/block branch with retained content plus the shared warning helper.
- [x] Re-run KnowledgeAgent tests.

### Task 6: Add a non-invasive ChatBI final grounding boundary

**Files:**
- Modify: `app/services/ai/runners/data_agent_runner.py`
- Modify: `app/services/ai/runners/chatbi/native_turn.py`
- Modify: `app/services/ai/runners/chatbi/synthesis.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [x] Add isolated failing tests for a matching SQL-result answer and an answer containing unrelated business numbers.
- [x] Verify only the new tests are RED; record pre-existing failures separately.
- [x] Add a shared DataAgent finalizer that evaluates current/derived `internal_data` receipts and appends a soft warning without changing SQL error termination.
- [x] Apply it to native final content and synthesis/reuse paths.
- [x] Re-run the isolated tests and the existing ChatBI grounding-related subset.

### Task 7: General-classification fallback, docs, and regression

**Files:**
- Modify: `app/services/ai/grounding/policy.py`
- Modify: `app/services/ai/runners/assistant_agent_runner.py`
- Modify: `docs/platform-grounding-gate.md`
- Modify: `docs/md/ai_agent_gating_contract.md`
- Modify: `tests/CHECKLIST.md`
- Test: `tests/ai/runners/test_assistant_agent_grounding_gate.py`

- [x] Add failing tests for a GENERAL route emitting a high-confidence current fact without evidence and for stable general knowledge remaining warning-free.
- [x] Add a conservative output-scrutiny fallback only when structural dynamic fact signals are present.
- [x] Update runtime contracts and checklist with correlation, empty-result, KnowledgeAgent, ChatBI, and MCP structured-content behavior.
- [x] Run the focused grounding suite, `git diff --check`, and `py_compile` for touched backend modules.
- [x] Do not run `./dev.sh`; hand service compilation and restart to the user.

