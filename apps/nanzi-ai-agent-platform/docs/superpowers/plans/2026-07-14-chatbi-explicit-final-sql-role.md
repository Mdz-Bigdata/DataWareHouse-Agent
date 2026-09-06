# ChatBI Explicit Final SQL Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a successful final business SQL from being reclassified as diagnostic solely because its text resembles a sampling query.

**Architecture:** Carry a one-shot final-query role in `DataRunState` when the repair loop explicitly requests the final SQL. Consume that role in SQL result handling before falling back to SQL-shape classification.

**Tech Stack:** Python, dataclasses, pytest, AgentScope ChatBI runner

---

### Task 1: Add the failing regression test

**Files:**
- Modify: `tests/ai/runners/test_data_agent_runner.py`

- [ ] Add a test that creates diagnostic-pending state, runs `reset_state_for_repair()`, then applies a successful `IS NOT NULL ... LIMIT` detail query.
- [ ] Assert the query is accepted as final, `ready_to_answer` is true, and diagnostic state is cleared.
- [ ] Run the single test and verify it fails because the explicit role does not exist yet.

### Task 2: Carry and consume the explicit role

**Files:**
- Modify: `app/services/ai/runners/chatbi/run_state.py`
- Modify: `app/services/ai/runners/chatbi/repair_policy.py`
- Modify: `app/services/ai/runners/chatbi/tool_result_handlers.py`

- [ ] Add `next_sql_is_final_business_query: bool = False` to `DataRunState`.
- [ ] Set it when `reset_state_for_repair()` sees `diagnostic_sql_pending_final`.
- [ ] In SQL result handling, consume the flag and bypass `is_diagnostic_sql()` for that execution only.
- [ ] Run the new test and verify it passes.

### Task 3: Regression verification

**Files:**
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [ ] Run focused diagnostic-SQL and final-guard tests.
- [ ] Run the complete ChatBI runner test file if focused tests pass.
- [ ] Review the diff for unrelated changes; do not commit or restart services.

