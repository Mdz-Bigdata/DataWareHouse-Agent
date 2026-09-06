# 跨会话记忆与记忆管理中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Redis Stack 会话摘要索引、`memory_search` 工具、记忆管理中心（配置/数据/检索测试）及菜单权限体系。

**Architecture:** 会话明细仍用 `conversation:{uid}:{cid}:history` LIST；摘要用 `memory:summary:{uid}:{cid}` + RediSearch KNN；配置存 `system_configs.category=memory`；Portal `/api/portal/memory` 按 `menu:memory_management` + `element:memory:*` 鉴权；对话内工具不校验菜单。

**Tech Stack:** FastAPI, Redis Stack (RediSearch), Vue 3, `ConfigService`, `PermissionService`, LangChain `@tool`

**Spec:** [`docs/superpowers/specs/2026-05-27-memory-search-redis-stack-design.md`](../specs/2026-05-27-memory-search-redis-stack-design.md)

---

## File map

| 文件 | 职责 |
|------|------|
| `db-prod/V59-memory-management-module.sql` | 配置表 + 菜单/元素权限（单脚本） |
| `app/services/memory_config_service.py` | 记忆配置读写（非 ConfigService） |
| `app/services/ai/embedding_client.py` | Embedding API 调用 |
| `app/services/ai/memory_index_service.py` | FT.CREATE / upsert / KNN / delete |
| `app/services/ai/conversation_summarizer.py` | LLM 生成 title/summary |
| `app/services/ai/memory_service.py` | 扩展 summary + history 删除 |
| `app/api/portal/endpoints/memory.py` | 管理中心 API |
| `app/services/ai/tools/memory_search_tool.py` | 对话工具 |
| `app/core/context.py` | `AgentContext.conversation_id` |
| `app/services/ai/agent_service.py` | 异步摘要任务；不自动注入 summary |
| `app/services/ai/tools/registry.py` | 注册 memory_search |
| `frontend/src/constants/permissions.ts` | MENU_TREE 节点 |
| `frontend/src/views/MemoryManagement.vue` | 三 Tab UI |
| `frontend/src/router/index.ts` + `Dashboard.vue` | 路由与侧栏 |
| `tests/test_memory_*.py` | 单元/集成测试 |

---

### Task 1: 数据库迁移与权限种子

**Files:**
- Create: `db-prod/V59-memory-management-module.sql`
- Modify: `app/services/permission_service.py`（展示名 `memory_management`）

- [ ] **Step 1:** 执行 `V59-memory-management-module.sql`（`memory_service_configs` + 权限种子）
- [ ] **Step 2:** `permission_service` 增加 `'memory_management': '记忆管理中心'`

---

### Task 2: EmbeddingClient

**Files:**
- Create: `app/services/ai/embedding_client.py`
- Create: `tests/test_embedding_client.py`

- [ ] **Step 1:** 写失败测试：mock httpx，给定 text 返回固定维度向量
- [ ] **Step 2:** 实现：读 `memory_embedding_*`，fallback `llm_base_url` / `llm_api_key`；OpenAI 兼容 POST `/v1/embeddings`
- [ ] **Step 3:** 运行测试通过

---

### Task 3: MemoryIndexService（Redis Stack）

**Files:**
- Create: `app/services/ai/memory_index_service.py`
- Create: `tests/test_memory_index_service.py`（`pytest.mark` 无基础设施或 fakeredis/redislite 若可用）

- [ ] **Step 1:** `ensure_index(dimensions)` — `FT.CREATE` 含 VECTOR COSINE、TAG user_id/conversation_id、PREFIX `memory:summary:`
- [ ] **Step 2:** `upsert_summary(user_id, conversation_id, fields)` — HSET + 索引字段
- [ ] **Step 3:** `search_summaries(user_id, query_embedding?, limit, keyword?)` — KNN + `@user_id`
- [ ] **Step 4:** `delete_summary` / `delete_all_for_user` / `index_status` / `rebuild_index`
- [ ] **Step 5:** 测试覆盖 upsert + search（可 mock redis）

---

### Task 4: MemoryService 扩展 + ConversationSummarizer

**Files:**
- Modify: `app/services/ai/memory_service.py`
- Create: `app/services/ai/conversation_summarizer.py`
- Create: `tests/test_conversation_summarizer.py`

- [ ] **Step 1:** `MemoryService` 增加 `delete_history`、`clear_user_memory`（SCAN + DEL）
- [ ] **Step 2:** `ConversationSummarizer.summarize(history_messages) -> {title, summary}` 用系统 LLM
- [ ] **Step 3:** `merge_session_summary(uid, cid)` 编排：读配置开关 → 读 history → summarize → embed → index upsert
- [ ] **Step 4:** 防抖：Redis key `memory:debounce:{uid}:{cid}` 或内存 TTL 读配置 turns/seconds

---

### Task 5: AgentService 触发摘要

**Files:**
- Modify: `app/services/ai/agent_service.py`
- Modify: `app/services/ai/context_manager.py`
- Modify: `app/core/context.py`

- [ ] **Step 1:** `AgentContext` 增加 `conversation_id: Optional[str]`
- [ ] **Step 2:** `setup_context` 传入 `conversation_id`
- [ ] **Step 3:** `chat_completion_stream` 的 `finally` 中 `create_task(merge_session_summary(...))`（成功且有 conv_id）
- [ ] **Step 4:** 确认 **不**注入 history_summary 到 system_prompt

---

### Task 6: memory_search 工具

**Files:**
- Create: `app/services/ai/tools/memory_search_tool.py`
- Modify: `app/services/ai/tools/registry.py`
- Create: `tests/test_memory_search_tool.py`

- [ ] **Step 1:** 实现 `memory_search(scope, query?, conversation_id?, limit?)` — 读 `get_current_agent_context().user_id`
- [ ] **Step 2:** 注册到 `_registry` 与 `get_system_implicit_tools()`
- [ ] **Step 3:** 测试：mock index/history，验证不可跨 user_id

---

### Task 7: Portal API memory.py

**Files:**
- Create: `app/api/portal/endpoints/memory.py`
- Modify: `app/api/portal/api.py`

- [ ] **Step 1:** 路由挂载 `prefix=/memory`
- [ ] **Step 2:** 实现 configs GET/PUT（仅 `category=memory`）
- [ ] **Step 3:** summaries 列表/详情/删除；`user_id` 校验 + `view_all_users`
- [ ] **Step 4:** `search-test`、`test-embedding`、`index/status`、`index/rebuild`
- [ ] **Step 5:** 各端点 `Depends(require_permission(...))` 按 spec §6.1.3

---

### Task 8: 前端权限与路由

**Files:**
- Modify: `frontend/src/constants/permissions.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/views/Dashboard.vue`

- [ ] **Step 1:** `MENU_TREE` 增加 `menu:memory_management` 及 children
- [ ] **Step 2:** 路由 `/dashboard/memory`，`meta.perm`
- [ ] **Step 3:** 侧栏「智能体开发平台」下增加「记忆管理中心」

---

### Task 9: MemoryManagement.vue

**Files:**
- Create: `frontend/src/views/MemoryManagement.vue`

- [ ] **Step 1:** Tab「服务配置」— 拉取/保存 memory configs，`hasPermission('element:memory:config_save')`
- [ ] **Step 2:** Tab「记忆数据」— 表格筛选 user_id、删除按钮权限
- [ ] **Step 3:** Tab「记忆检索测试」— 调用 `search-test`，展示 KNN 结果与 history 预览
- [ ] **Step 4:** 索引状态/重建、测试 Embedding 按钮权限

---

### Task 10: 文档与回归

**Files:**
- Modify: `architech/design/chat/CHAT_FLOW.md`
- Modify: `tests/CHECKLIST.md`（可选）

- [ ] **Step 1:** 更新 ChatFlow §4.1 实现状态
- [ ] **Step 2:** 运行 `tests/run_tests.sh` 相关用例
- [ ] **Step 3:** 手工：角色无菜单不可见；有菜单无 delete 不可删；Embed 调 memory_search 无需菜单

---

## 二期（本计划不实施）

- `POST /api/v1/conversation/{id}/finalize` + Embed 切换会话调用
- 列表用户名 JOIN 优化（若一期未做）

---

## 建议提交顺序

1. Task 1–4（后端存储与索引）  
2. Task 5–7（编排 + API + 工具）  
3. Task 8–9（前端）  
4. Task 10（文档与测试）  

每个 Task 完成后可独立提交，中文 commit message。
