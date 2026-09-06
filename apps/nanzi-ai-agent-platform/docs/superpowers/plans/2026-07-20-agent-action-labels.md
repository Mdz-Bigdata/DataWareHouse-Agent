# Agent Action Labels Implementation Plan

> **For agentic workers:** Apply this plan inline with test-first verification; do not stage or commit without explicit user approval.

**Goal:** Rename agent action entrances so their purpose is clear and consistent across card, list, and drawer views.

**Architecture:** This is a presentation-only change. Keep existing handlers and behavior unchanged; update visible labels and accessibility titles in the existing agent management components.

**Tech Stack:** Vue 3, TypeScript, pytest contract tests

---

### Task 1: Rename action entrances consistently

**Files:**
- Modify: `frontend/src/views/AgentManagement.vue`
- Modify: `frontend/src/components/agent/AgentVersionsDrawer.vue`
- Test: `tests/frontend/test_agent_type_form_contract.py`

- [ ] Add a failing contract test requiring `编辑智能体` and `配置与发布`, and rejecting the former action labels.
- [ ] Run `venv/bin/python -m pytest tests/frontend/test_agent_type_form_contract.py -q` and confirm it fails on the missing labels.
- [ ] Rename `配置元数据` to `编辑智能体` and `版本管理` to `配置与发布` in visible text and titles.
- [ ] Run the focused pytest contract and Vue SFC parser.
- [ ] Run `git diff --check` and leave changes unstaged.
