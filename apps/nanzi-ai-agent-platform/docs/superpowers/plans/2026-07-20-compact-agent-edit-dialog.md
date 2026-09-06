# Compact Agent Edit Dialog Implementation Plan

> **For agentic workers:** Apply this plan inline with test-first verification; do not stage or commit without explicit user approval.

**Goal:** Implement the approved compact two-column agent edit dialog while locking engine selection and retaining editable engine-specific parameters.

**Architecture:** Extend the shared modal with optional header-extra and footer slots, then restructure only the editing branch in `AgentManagement.vue`. Preserve existing form state and save handlers.

**Tech Stack:** Vue 3, TypeScript, Tailwind CSS, pytest contract tests

---

### Task 1: Add layout contracts

- [ ] Add failing assertions for wide modal size, header System Agent, locked engine summary, editable RAGFlow/OpenClaw parameters, collapsible safety settings, and modal slots.
- [ ] Run the focused contract and verify failure.

### Task 2: Implement compact edit layout

- [ ] Add optional `header-extra` and `footer` slots to `Modal.vue`.
- [ ] Move System Agent into the edit modal header.
- [ ] Convert basic fields to a compact grid and render engine type read-only.
- [ ] Preserve engine-specific parameter fields and collapse OpenClaw advanced safety rules.
- [ ] Move edit actions into the modal footer.

### Task 3: Verify

- [ ] Run focused frontend contract tests.
- [ ] Parse both Vue SFCs and run relevant `vue-tsc` filtering.
- [ ] Run `git diff --check`.
