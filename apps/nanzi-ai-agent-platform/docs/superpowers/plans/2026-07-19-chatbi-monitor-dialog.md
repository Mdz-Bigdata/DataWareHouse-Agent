# ChatBI Monitor Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser-native monitor confirmation with one shared, validated ChatBI monitor configuration dialog.

**Architecture:** A focused Vue component owns schedule form state, validation, API submission, loading, and inline errors. EmbedChat and AgentDebug only pass the selected result identity, control visibility, and display the completion toast.

**Tech Stack:** Vue 3 script setup, TypeScript, Tailwind CSS, Axios, Python source-contract pytest.

---

### Task 1: Lock the shared-dialog contract

**Files:**
- Modify: `tests/frontend/test_chatbi_delivery_actions_contract.py`

- [ ] Add assertions that both views import and render `ChatBIMonitorDialog`, contain no `window.confirm`, and no longer post directly to `/api/portal/chatbi-monitors`.
- [ ] Add assertions that the shared component defines daily/weekly/monthly options, `time_value`, `weekday`, `monthday`, `notify_on_success`, loading state, inline error state, and the monitor endpoint.
- [ ] Run `REDIS_ENABLE=false venv/bin/python -m pytest tests/frontend/test_chatbi_delivery_actions_contract.py -q` and verify it fails because the component does not exist and the native confirmation remains.

### Task 2: Implement the focused monitor dialog

**Files:**
- Create: `frontend/src/components/chatbi/ChatBIMonitorDialog.vue`

- [ ] Implement props `open`, `conversationId`, and optional `resultId`; emit `close` and `created`.
- [ ] Initialize `schedule_type='daily'`, `time_value='09:00'`, weekday Monday, monthday 1, and successful notification enabled whenever the dialog opens.
- [ ] Validate time, weekly weekday, and monthly day 1–28 before posting.
- [ ] Post the selected result and only the schedule-specific fields to `/api/portal/chatbi-monitors`.
- [ ] Disable close and submit while posting; retain the dialog and render the backend detail on failure.
- [ ] Render an accessible light/dark Tailwind dialog with `role="dialog"` and `aria-modal="true"`.

### Task 3: Wire both ChatBI surfaces

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/views/AgentDebug.vue`

- [ ] Import the shared dialog and add local state for visibility and the selected result ID.
- [ ] Change the monitor action handler to open the dialog instead of calling `window.confirm` and Axios.
- [ ] Render the shared dialog once at view level and pass conversation/result identity.
- [ ] On `created`, close it and show either “指标监控已创建” or “该结果的监控已存在” according to the API response.
- [ ] Keep brief generation unchanged.

### Task 4: Verify regression scope

**Files:**
- Verify: `tests/frontend/test_chatbi_delivery_actions_contract.py`
- Verify: `tests/api/test_chatbi_monitor_validation.py`

- [ ] Run the frontend contract and confirm green.
- [ ] Run the backend idempotency/validation contract and confirm green.
- [ ] Run `git diff --check`.
- [ ] Do not run `./dev.sh`; hand browser verification to the user as required by repository instructions.
