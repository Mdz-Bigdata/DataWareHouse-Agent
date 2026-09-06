# Scenario Agent Type Alignment Implementation Plan

> **For agentic workers:** Apply this plan inline with test-first verification; do not stage or commit without explicit user approval.

**Goal:** Ensure every built-in scenario delivers and repairs an agent whose primary type, locked capability, tools, and release gate agree.

**Architecture:** Declare `agent_type` in each scenario manifest and make the installer the propagation point into `AIAgentBase`. The change applies only to newly created scenario agents; existing agents and historical delivery data are not migrated or rewritten.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, MySQL JSON, pytest

---

### Task 1: Lock the manifest contract

- [ ] Add a failing unit test covering all eight template-to-type mappings and their locked primary capabilities.
- [ ] Run the test and confirm current manifests lack `agent_type`.
- [ ] Add `agent_type` and normalized capabilities to every built-in manifest.

### Task 2: Align new installs

- [ ] Extend the installation test to assert a ChatBI template persists `CHATBI/data_query`.
- [ ] Pass manifest `agent_type` during creation without changing existing instances.

### Task 3: Verify

- [ ] Run focused scenario, agent type, readiness, and installer tests; run Python compilation and `git diff --check`.
