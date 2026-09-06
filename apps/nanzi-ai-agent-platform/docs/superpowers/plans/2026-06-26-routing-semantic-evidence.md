# Routing Semantic Evidence Implementation Plan

> **For agentic workers:** Execute inline with TDD. Do not stage or commit any code unless the user explicitly requests it.

**Goal:** Reuse one agent-independent semantic intent result for capability-aware routing and for the selected executor, eliminating contradictory route and intent decisions.

**Architecture:** `RouterService` obtains a generic `IntentResponse` before LLM agent selection. A high-confidence `DATA_QUERY` result constrains only the router's eligible candidates to agents advertising a data-query capability; the result is carried in `RouteResult` and reused by `turn_classifier` so the selected Main agent sees the same semantic evidence. ChatBI retains its internal request-category classifier.

**Tech Stack:** Python, Pydantic, pytest, AgentScope runtime adapters.

---

### Task 1: Make router candidate selection consume semantic evidence

**Files:**
- Modify: `app/services/ai/router_service.py`
- Test: `tests/services/ai/test_router_service.py`

- [ ] **Step 1: Write the failing route constraint test**

```python
@pytest.mark.asyncio
async def test_route_query_constrains_candidates_for_high_confidence_data_intent(mock_agents_metadata):
    service = RouterService()
    evidence = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.91,
        reasoning="请求系统结构化记录",
        entities=["任意业务对象"],
    )
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock, return_value=mock_agents_metadata), \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock, return_value=evidence), \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock, return_value=object()), \
         patch("app.services.ai.router_service.chat_client_from_handle", return_value=_mock_chat_client(json.dumps({
             "thought": "在数据候选中选择 SQL 分析专家", "agent_name": "ChatBI", "confidence": 0.9,
         }))) as factory:
        result = await service.route_query("列出某个系统中的全部记录")

    assert result.agent_id == "agent-chatbi"
    assert result.intent_info == evidence
    prompt = factory.return_value.generate_text.call_args.args[0][0].content[0].text
    assert "ID: ChatBI" in prompt
    assert "ID: general-chat" not in prompt
```

- [ ] **Step 2: Run the test and verify it fails because `RouteResult` has no semantic evidence and routing does not constrain candidates**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/ai/test_router_service.py::test_route_query_constrains_candidates_for_high_confidence_data_intent -q`

- [ ] **Step 3: Add evidence-carrying, capability-aware routing**

```python
class RouteResult(BaseModel):
    agent_id: str
    secondary_agents: List[str] = []
    confidence: float
    reasoning: str
    intent_info: Optional[IntentResponse] = None
    # existing route-hint fields remain unchanged

async def _resolve_intent_evidence(self, user_input: str, history: Optional[List[dict]]) -> Optional[IntentResponse]:
    try:
        return await intent_service.identify_intent(user_input, history=history)
    except Exception as exc:
        logger.warning("Semantic evidence failed before routing: %s", exc)
        return None

def _constrain_candidates_by_intent(self, agents: List[dict], intent_info: Optional[IntentResponse]) -> List[dict]:
    if not intent_info or intent_info.intent != IntentType.DATA_QUERY:
        return agents
    if intent_info.confidence < AMBIGUOUS_INTENT_CONFIDENCE_THRESHOLD:
        return agents
    data_agents = [agent for agent in agents if self._is_data_query_agent(agents, agent.get("name"))]
    return data_agents or agents
```

Call `_resolve_intent_evidence` after existing greeting/web/session-break shortcuts, build the LLM prompt from the constrained list, and pass `intent_info` through `_build_route_result` and fallback results.

- [ ] **Step 4: Run the focused router test and verify it passes**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/ai/test_router_service.py::test_route_query_constrains_candidates_for_high_confidence_data_intent -q`

- [ ] **Step 5: Add and run fallback coverage**

Add cases proving low-confidence `DATA_QUERY` and a high-confidence `DATA_QUERY` with no data-capable candidate retain the complete candidate list and may select Main.

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/ai/test_router_service.py -q`

### Task 2: Reuse routing evidence in session classification and expose it to Main

**Files:**
- Modify: `app/services/ai/turn_classifier.py`
- Modify: `app/services/ai/agent_service.py`
- Modify: `app/services/ai/executors/prompts.py`
- Test: `tests/ai/test_turn_classifier.py`
- Test: `tests/ai/executors/test_chat_executor.py`

- [ ] **Step 1: Write the failing reuse test**

```python
@pytest.mark.asyncio
async def test_resolve_turn_reuses_routing_intent_evidence_without_second_llm_call():
    evidence = IntentResponse(
        intent=IntentType.DATA_QUERY, confidence=0.9,
        reasoning="请求结构化记录", entities=[],
    )
    with patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as identify:
        classification, intent_info, elapsed_ms = await resolve_turn_for_session(
            "列出任意记录", [{"role": "user", "content": "列出任意记录"}],
            can_do_data=False, intent_evidence=evidence,
        )

    identify.assert_not_awaited()
    assert classification.intent == IntentType.DATA_QUERY
    assert classification.turn_type == TurnType.GENERAL
    assert intent_info == evidence
    assert elapsed_ms == 0.0
```

- [ ] **Step 2: Run it and verify it fails because `intent_evidence` is not accepted**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/test_turn_classifier.py::test_resolve_turn_reuses_routing_intent_evidence_without_second_llm_call -q`

- [ ] **Step 3: Add optional evidence reuse**

```python
async def resolve_turn_classification(..., intent_evidence: Optional[IntentResponse] = None):
    # keep existing heuristic classification first
    if classification is None:
        if intent_evidence is not None:
            intent_info = intent_evidence
            intent_elapsed_ms = 0.0
        else:
            intent_info = await intent_service.identify_intent(user_query, history=prior_messages)
        classification = classify_turn_from_intent(...)
```

Thread the optional argument through `resolve_turn_for_session`. In `AgentService`, retrieve `route_details.intent_info`, pass it to every `resolve_turn_for_session` call, include its fields in router trace output, and store `semantic_intent`, `semantic_confidence`, and `semantic_reasoning` in `route_hints`.

Extend `AssistantPrompts.route_hints` to show this as semantic evidence, explicitly stating that Main should obtain real data through its available delegation/tool path rather than inventing an answer.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/ai/test_turn_classifier.py::test_resolve_turn_reuses_routing_intent_evidence_without_second_llm_call tests/ai/executors/test_chat_executor.py -q`

- [ ] **Step 5: Run all affected suites**

Run: `REDIS_ENABLE=false PYTHONPATH=. venv/bin/python -m pytest tests/services/ai/test_router_service.py tests/ai/test_turn_classifier.py tests/ai/executors/test_chat_executor.py tests/ai/test_tool_nudge_policy.py -q`

### Task 3: Verify integration hygiene

**Files:**
- Modify only if a test exposes a concrete compatibility defect in the preceding tasks.

- [ ] **Step 1: Compile production modules**

Run: `PYTHONPATH=. venv/bin/python -m py_compile app/services/ai/router_service.py app/services/ai/turn_classifier.py app/services/ai/agent_service.py app/services/ai/executors/prompts.py`

- [ ] **Step 2: Inspect the final diff and worktree**

Run: `git diff --check && git diff --stat && git status --short`

- [ ] **Step 3: Hand off without staging or committing**

Report the test output and changed files. Do not run `git add` or `git commit` unless the user explicitly asks.
