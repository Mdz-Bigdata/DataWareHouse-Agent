# AgentScope Coroutine Serialization Fix Implementation Plan

> **For agentic workers:** Implement inline with test-driven development; no service restart.

**Goal:** Verify native tool metadata delegation and resolve coroutine leaks in pending snapshots safely.

**Architecture:** Keep the existing wrapper and snapshot flow. Confirm native flags are already delegated, then recursively resolve awaitables from the Python-mode state at the asynchronous confirmation boundary.

**Tech Stack:** Python, AgentScope 2.0.1, Pydantic, pytest

---

### Task 1: Preserve native tool metadata

**Files:**
- Modify: `tests/ai/runtime/test_agentscope_tooling.py`
- Modify: `app/services/ai/runtime/agentscope/tools.py`

- [ ] Add a failing test asserting wrapped `Write` keeps `is_state_injected` and `is_concurrency_safe`.
- [ ] Run the focused test and confirm it fails on `is_state_injected`.
- [ ] Confirm the existing delegation passes without production changes.

### Task 2: Diagnose leaked awaitables before snapshot serialization

**Files:**
- Modify: `tests/ai/runtime/test_agentscope_state_pending.py`
- Modify: `app/services/ai/runtime/agentscope/confirmations.py`

- [ ] Add a failing test requiring a nested coroutine to be resolved in agent state.
- [ ] Run the focused test and confirm current JSON serialization fails.
- [ ] Add a recursive awaitable resolver with path-rich failure reporting.
- [ ] Run both focused suites and confirm they pass without coroutine warnings.
