# ChatBI Analysis Workflow Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved P0/P1/P2 evolution from reliable intent boundaries through result-aware analysis, delivery, subscriptions, and alerts.

**Architecture:** Add focused policy and state modules around the existing Router and `DataAgentRunner`; preserve the current SQL gate stack as the only path that may execute data queries. Build P1/P2 on structured result references and operations so later features do not depend on prompt-only SQL rewriting.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Redis memory service, AgentScope runtime, Vue 3/TypeScript, pytest.

---

### Task 1: P0 tri-state ChatBI session affinity

**Files:**
- Modify: `app/services/ai/intent_service.py`
- Modify: `app/services/ai/router_service.py`
- Test: `tests/ai/test_router_service.py`
- Test: `tests/ai/test_turn_classifier.py`

- [ ] Add failing tests proving explicit data follow-ups return `KEEP`, explicit topic switches return `BREAK`, and gray phrases return `UNCERTAIN`.
- [ ] Run the focused tests and confirm the gray phrase still routes directly to Main.
- [ ] Add `DataSessionAffinity` and `resolve_data_agent_session_affinity()`; keep `should_inherit_data_agent_session()` as a boolean compatibility wrapper.
- [ ] Change `RouterService.route_query()` to short-circuit only on `BREAK`; let `UNCERTAIN` reach request-decision and semantic routing.
- [ ] Run router and intent tests; verify existing greeting, web, public-profile and real data-query cases pass.

### Task 2: P0 first-turn unknown classification

**Files:**
- Modify: `app/services/ai/data_query_turn_classifier.py`
- Test: `tests/ai/test_turn_classifier.py`

- [ ] Add failing tests for first-turn “这里主要覆盖哪些业务”, “销售额通常应该怎么分析”, and an unclassifiable request; assert none silently becomes `NEW_DATA_QUERY`.
- [ ] Add a failing test proving “查本月各区域销售额” still takes the zero-LLM fast path.
- [ ] Replace the unconditional no-history default with high-confidence data/metadata/non-data fast paths followed by the existing classifier LLM.
- [ ] On classifier failure without positive data evidence, return `CLARIFICATION_REQUIRED` or `CAPABILITY_HELP` rather than querying Schema.
- [ ] Run the full data-query classifier test slice.

### Task 3: P0 non-data disposition and seamless delegation

**Files:**
- Create: `app/services/ai/runners/chatbi/non_data_policy.py`
- Modify: `app/services/ai/data_query_turn_classifier.py`
- Modify: `app/services/ai/runners/chatbi/turn_handlers.py`
- Modify: `app/services/ai/runners/chatbi/clarification.py`
- Modify: `app/services/ai/runners/data_agent_runner.py`
- Test: `tests/ai/runners/test_chatbi_non_data_policy.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [ ] Write failing policy tests for local capability help, current-result actions, Main delegation, and public-web delegation.
- [ ] Define `NonDataDisposition` and `resolve_non_data_disposition()` using the shared request-source decision.
- [ ] Write failing runner tests that require a structured delegation event and fallback guidance only when delegation is unavailable.
- [ ] Add a runner delegation adapter that preserves question, user, conversation and result reference while enforcing the normal dispatcher permissions.
- [ ] Keep greetings/capability help local and route result actions into the existing context-action/tool path.
- [ ] Run non-data, runner, grounding and dispatcher regression tests.

### Task 4: P0 Schema miss source reclassification

**Files:**
- Create: `app/services/ai/runners/chatbi/source_reclassification.py`
- Modify: `app/services/ai/runners/chatbi/run_state.py`
- Modify: `app/services/ai/runners/chatbi/react_stream.py`
- Modify: `app/services/ai/runners/chatbi/native_turn.py`
- Test: `tests/ai/runners/test_chatbi_source_reclassification.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [ ] Write failing tests for public profile, internal knowledge, platform self-help, general request and true missing internal dataset after two Schema misses.
- [ ] Add a source reclassification result containing source, capability, confidence, reason and delegation target.
- [ ] Invoke reclassification before the fatal Schema response; delegate supported non-data sources and retain existing fatal response for internal-data/unknown cases.
- [ ] Emit a trace step describing reclassification without exposing internal reasoning.
- [ ] Run Schema prefetch, repair, fatal, router and runner regression tests.

### Task 5: P1 result stack and reference resolution

**Files:**
- Create: `app/services/ai/chatbi_result_stack.py`
- Modify: `app/services/ai/memory_service.py`
- Modify: `app/services/ai/runners/chatbi/followup_data.py`
- Modify: `app/services/ai/runners/chatbi/state_serialization.py`
- Test: `tests/ai/test_chatbi_result_stack.py`
- Test: `tests/ai/runners/test_data_agent_runner.py`

- [ ] Write failing tests for push, maximum depth 10, current/previous/explicit reference, ambiguous descriptive reference and user/conversation isolation.
- [ ] Implement versioned `ChatBIResultRef`, `ChatBIAnalysisContext` and Redis stack persistence.
- [ ] Dual-write the current legacy payload and stack during compatibility period; read stack first and legacy value second.
- [ ] Persist metrics, dimensions, filters and time context from semantic intent, SQL plan and query binding.
- [ ] Run memory, follow-up, resume and saved-report focus tests.

### Task 6: P1 split result turn taxonomy

**Files:**
- Modify: `app/services/ai/data_query_turn_classifier.py`
- Modify: `app/services/ai/runners/chatbi/turn_handlers.py`
- Modify: `app/services/ai/runners/chatbi/synthesis.py`
- Modify: `frontend/src/types/chatbiInsight.ts`
- Test: `tests/ai/test_turn_classifier.py`
- Test: `tests/ai/runners/test_chatbi_modules.py`

- [ ] Add failing tests distinguishing data follow-up query, result analysis, result presentation and result action.
- [ ] Add new enum values with compatibility mappings from reuse/format/context action.
- [ ] Dispatch analysis to cached-data synthesis, presentation to deterministic re-rendering, action to tools, and follow-up query to fresh SQL with inherited context.
- [ ] Add trace labels and frontend types for the four behaviors.
- [ ] Run classifier, runner and frontend contract tests.

### Task 7: P1 metadata navigation and grounded clarification candidates

**Files:**
- Create: `app/services/ai/runners/chatbi/metadata_guide.py`
- Modify: `app/services/ai/runners/chatbi/turn_handlers.py`
- Modify: `app/services/ai/runners/chatbi/clarification.py`
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Create: `frontend/src/components/chatbi/ChatBIMetadataGuide.vue`
- Test: `tests/ai/runners/test_chatbi_metadata_guide.py`
- Test: `tests/frontend/test_chatbi_metadata_guide_contract.py`

- [ ] Write failing backend tests that build topics, metrics, dimensions, freshness and executable suggestions only from authorized Schema.
- [ ] Emit additive `chatbi_metadata_guide` SSE plus Markdown fallback.
- [ ] Write failing clarification tests ensuring dataset/metric/field candidates carry physical names and originate from actual Schema.
- [ ] Render the guide and candidate chips in both EmbedChat and AgentDebug shared message flow.
- [ ] Run metadata, clarification, SSE reconcile and frontend contract tests.

### Task 8: P2 mixed task plan and conditional drill-down

**Files:**
- Create: `app/services/ai/chatbi_task_plan.py`
- Create: `app/services/ai/chatbi_analysis_operations.py`
- Modify: `app/services/ai/runners/data_agent_runner.py`
- Modify: `app/services/ai/runners/chatbi/insight_meta.py`
- Modify: `frontend/src/components/chatbi/ChatBIContinueAnalysis.vue`
- Test: `tests/ai/test_chatbi_task_plan.py`
- Test: `tests/ai/test_chatbi_analysis_operations.py`
- Test: `tests/frontend/test_chatbi_drilldown_contract.py`

- [ ] Write failing tests for dependency validation, partial failure recovery and query→analyze→export ordering.
- [ ] Implement the restricted task plan and operation DSL from the design.
- [ ] Merge drill operations into a parent analysis context and force all resulting SQL through the existing Schema/permission/SQL gates.
- [ ] Replace prompt-only follow-up action payloads with versioned structured operations while retaining `query` fallback.
- [ ] Render inherited-condition chips and back-to-parent behavior.
- [ ] Run task plan, gate, result stack and frontend tests.

### Task 9: P2 evidence-backed business brief

**Files:**
- Create: `app/services/chatbi_brief_service.py`
- Create: `app/api/portal/endpoints/chatbi_briefs.py`
- Create: `app/models/chatbi_analysis.py`
- Create: `db-prod/V98-create-chatbi-analysis-and-briefs.sql`
- Create: `frontend/src/components/chatbi/ChatBIBriefBuilder.vue`
- Test: `tests/services/test_chatbi_brief_service.py`
- Test: `tests/api/portal/test_chatbi_briefs.py`
- Test: `tests/frontend/test_chatbi_brief_contract.py`

- [ ] Write failing model/service tests for node selection, deterministic facts, evidence references and rejection of unsupported claims.
- [ ] Persist analysis sessions/nodes and brief definitions with user ownership and RBAC checks.
- [ ] Generate online Markdown/HTML and Word through the existing generated-file publication service.
- [ ] Add brief selection, preview, section visibility and download UI.
- [ ] Run migrations contract, service, API, document publication and frontend tests.

### Task 10: P2 query-to-report subscription and conditional alerts

**Files:**
- Modify: `app/models/saved_report.py`
- Modify: `app/services/saved_report_subscription_service.py`
- Modify: `app/services/ai/scheduler_service.py`
- Modify: `app/api/portal/endpoints/saved_reports.py`
- Modify: `frontend/src/components/chatbi/DatasetCapabilityMenu.vue`
- Create: `db-prod/V99-add-saved-report-alert-conditions.sql`
- Test: `tests/services/test_saved_report_alert_conditions.py`
- Test: `tests/api/portal/test_saved_report_subscriptions.py`
- Test: `tests/frontend/test_saved_report_subscription_ui_contract.py`

- [ ] Write failing tests for threshold, rate-of-change, consecutive-hit, no-data and unconditional compatibility.
- [ ] Add versioned alert condition and state snapshots to subscriptions and deliveries.
- [ ] Evaluate conditions after a successful run; deliver only on a hit and persist trigger evidence and comparison baseline.
- [ ] Add “monitor this result” from the current analysis context and a simple condition editor.
- [ ] Run scheduler, saved-report, delivery, notification and frontend tests.

### Task 11: Completion audit

**Files:**
- Modify: `tests/CHECKLIST.md`
- Modify: `app/services/ai/runners/chatbi/README.md`
- Modify: `README.md`

- [ ] Map every P0/P1/P2 acceptance criterion in the design to an authoritative test and implementation file.
- [ ] Run targeted Python suites with `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest ...`.
- [ ] Run frontend contract tests and the frontend type/build command without starting services.
- [ ] Run `venv/bin/python -m py_compile` for touched Python modules and `git diff --check`.
- [ ] Confirm no `./dev.sh`, service restart, staging, commit, push or PR was performed unless separately requested.

