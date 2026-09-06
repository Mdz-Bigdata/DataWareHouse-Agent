# Implementation Plan: 四项目全能力融合

## Overview

在不回退 DataWareHouse-Agent 现有智能问数、安全网闸、语义层、自愈记忆和开发者工作台能力的前提下，把以下固定快照的全部可运行能力纳入同一代码库、统一部署入口和统一门户：

- `nanzi-api-data-platform`：`56c7acc2878952b0e246ea72f3ca8d12f392dd7f`
- `nanzi-ai-agent-platform`：`1cf117cefa3dd6ecf0fabb2581398a3307bb64ca`
- `listen-book-data-agent`：`bcc9fa90bf77673156498959ee4bbee82d73308d`

“全部能力”以来源仓库代码、路由、数据库迁移、前端页面、任务脚本和自动化测试为基线，不以 README 宣称代替验收。融合采用单仓库多应用架构：保留每套成熟业务域的内部实现，通过统一身份、网关、配置、导航、可观测性和部署编排形成一个产品；对已重叠的问数/元数据能力先适配、后收敛，避免一次性重写造成逻辑丢失。

## Baseline and Constraints

- 当前工作区已有用户未提交修改，涉及聊天 API、用户记忆、向量服务、前端页面与反馈飞轮；实施必须保留这些修改。
- 当前主应用为 Python FastAPI + React；两个 NanZi 项目为 FastAPI + Vue，听书问数为 FastAPI/LangGraph + React。
- 三个来源系统的 Python、FastAPI、Redis、SQLAlchemy、Qdrant 和前端依赖版本存在冲突，不能合并成单一 Python 进程或单一 `package.json` 后仍保证原功能不变。
- 两个 NanZi 仓库包含 MIT LICENSE；`listen-book-data-agent` 快照没有 LICENSE。复制其源码进入本仓库前必须由用户确认拥有授权或取得许可。
- 生产数据库、Redis、Qdrant、Elasticsearch、Embedding、RAGFlow、OpenClaw、通知通道等外部依赖不能在离线环境中伪造为“已验收”；必须区分本地自动化、容器集成和真实环境验收。

## Capability Matrix

| 能力域 | 当前项目 | API Data Platform | AI Agent Platform | Listen Book Agent | 融合目标 |
|---|---|---|---|---|---|
| 智能问数 | NL→DSL→SQL、安全网闸、自愈、图表 | SQL Lab、对话分析 | ChatBI、SQL plan、Few-shot、报告 | LangGraph、深度分析、洞察卡、推荐 | 保留四套引擎并提供统一入口与按场景路由 |
| 数据源 | MySQL/Doris/CK/Postgres/SQLite 降级 | MySQL/CK/Oracle、连接池治理 | DB 连接管理、数据画像 | MySQL + ES + Qdrant | 统一连接目录与凭证引用，保留各适配器 |
| 元数据 | 自动发现、语义层、JoinPath | 元数据治理、健康评分、目录 | 数据集、RAG 元数据、画像 | 版本发布、术语、规则、验证查询 | 建立共享 ID/映射，不删除来源模型 |
| 数据 API | 无完整产品化 | 资源 API、DSL、模板、发布 | 数据门户消费 | 同步/SSE 查询 API | 纳入统一 `/platform/*` 网关与 API 文档 |
| 智能体 | 开发 Agent 协调器 | AI SQL 辅助 | 多专家、工具、Skill、MCP、RAGFlow/OpenClaw | LangGraph 查询代理 | 统一智能体目录、会话和工具授权适配 |
| 知识/记忆 | 本地/Qdrant 纠错记忆 | 向量元数据 | LTM、摘要、知识库、个人 Skill | 反馈学习、查询集、语义知识 | 统一用户与会话关联，保留独立存储策略 |
| 安全权限 | 行列权限、AST SQL 网闸 | Session/API Key、RBAC、脱敏、审计 | RBAC、配额、SSO、权限挂起 | JWT、RLS、限流、SQL 白名单 | 统一登录令牌交换与审计关联，不降低各自网闸 |
| 数据产品 | 无 | 产品目录、申请审批、资产全景 | 我的数据门户、黄金报表 | 验证查询、基线、发布 | 统一门户导航与跨系统深链接 |
| 任务通知 | 无 | APScheduler、治理任务 | 任务中心、订阅、告警、多通知通道 | 知识重建、评测脚本 | 统一任务状态协议与通知入口 |
| 可观测性 | 基础日志 | 分片审计、统计、池监控 | Trace、Token、决策链 | OTel、Prometheus、trace inspector | 统一 trace ID、健康检查和状态页 |
| 前端 | React 单页问数台 | Vue 管理后台 | Vue 企业门户 | React/Carbon 工作台 | 统一壳层、单点登录、导航和视觉入口，子应用独立构建 |

## Architecture Decisions

1. **采用单仓库多应用，而非源码扁平合并。** 在 `apps/` 中保留四个可独立测试的应用，在 `platform/` 中增加统一网关、身份交换、能力注册和门户。这样可以完整保留来源逻辑及其依赖锁文件。
2. **固定来源快照并保留 provenance。** 每个导入应用记录仓库 URL、commit、许可证和变更说明；后续升级通过差异审计进行，避免无法追溯的复制。
3. **统一入口，不强行统一内部数据库。** 网关提供 `/platform/core`、`/platform/data-api`、`/platform/agents`、`/platform/audio` 命名空间；各业务域先使用自己的 schema/migration，再通过共享用户映射和资源引用联通。
4. **身份采用令牌交换。** 统一门户签发平台会话，各子系统验证平台 JWT 或通过短时内部令牌映射到本地用户/角色；保留 API Key、RLS、RBAC、配额和审计逻辑。
5. **前端采用应用壳 + 子应用构建。** 当前 React 作为核心问数应用保留；统一壳提供登录、导航、健康状态和上下文传递，Vue/React 子应用以同源路径部署，避免 React 18/19、Vue 3 和构建链冲突。
6. **重叠能力先并存、经等价测试后再收敛。** SQL guard、元数据、向量检索和 LLM 配置等重复实现均先保留；只有建立黄金测试和行为对比后才抽取共享实现。
7. **安全默认拒绝。** 外部工具、通知、写操作、生产数据库、跨系统身份交换均默认关闭，显式配置后启用；任何统一入口不得绕过子系统原有权限和 SQL 网闸。

## Dependency Graph

```text
授权确认 + 来源快照清单
        |
        v
多应用目录与独立依赖 --------> 统一配置/密钥引用
        |                              |
        v                              v
子应用原生测试基线 --------> 平台身份与用户映射
        |                              |
        +--------------+---------------+
                       v
                统一网关/能力注册
                       |
             +---------+----------+
             v                    v
        统一门户壳             任务/通知/Trace
             |                    |
             +---------+----------+
                       v
                跨系统端到端验收
```

## Task List

### Phase 0: Authorization and Reproducible Baseline

#### Task 1: Confirm source authorization and snapshot manifest

**Description:** Confirm permission to incorporate and redistribute the unlicensed Gitee repository, then record all source URLs, commits, licenses, import dates, and excluded generated artifacts.

**Acceptance criteria:**
- [ ] User confirms authorization for `listen-book-data-agent` or provides a compatible license.
- [ ] Every imported source has a machine-readable provenance entry.
- [ ] Secrets, local configs, logs, caches, and generated datasets are excluded.

**Verification:** Validate manifest paths and commit hashes against cloned repositories.

**Dependencies:** None

**Files likely touched:** `THIRD_PARTY.yml`, `NOTICE.md`, `.gitignore`

**Estimated scope:** Medium

#### Task 2: Capture current application regression baseline

**Description:** Preserve the current dirty worktree, document existing changes, and run current backend/frontend checks without resetting user work.

**Acceptance criteria:**
- [ ] Existing modified/untracked files are recorded and remain intact.
- [ ] Current backend tests and frontend build results are captured.
- [ ] Existing API routes and UI flows have a baseline inventory.

**Verification:** `git diff --check`; backend pytest; `npm run build` in current frontend.

**Dependencies:** None

**Files likely touched:** `artifacts/baseline/current.json`, `tasks/todo.md`

**Estimated scope:** Small

### Phase 1: Multi-Application Foundation

#### Task 3: Establish monorepo application boundaries

**Description:** Move or import each application into an explicit app boundary while retaining independent dependency locks and start commands.

**Acceptance criteria:**
- [ ] Four applications import/start independently.
- [ ] Python and frontend dependency graphs do not overwrite one another.
- [ ] Current root start behavior remains available through compatibility commands.

**Verification:** Compile/import smoke test for each backend; typecheck/build each frontend.

**Dependencies:** Tasks 1-2

**Files likely touched:** `apps/README.md`, `pyproject.toml`, `package.json`, `Makefile`

**Estimated scope:** Medium per application, implemented as four increments

#### Task 4: Add unified environment and secret references

**Description:** Define namespaced environment variables and secret references for all databases, LLMs, Redis, Qdrant, Elasticsearch, RAGFlow, OpenClaw, SSO, and notification providers.

**Acceptance criteria:**
- [ ] No source app reads another app's ambiguous environment variable.
- [ ] Example configuration contains placeholders only.
- [ ] Startup validation identifies missing mandatory dependencies by subsystem.

**Verification:** Configuration unit tests and secret scan.

**Dependencies:** Task 3

**Files likely touched:** `.env.example`, `platform/config.py`, `platform/config.schema.json`, `docs/configuration.md`

**Estimated scope:** Medium

#### Task 5: Compose infrastructure and health contracts

**Description:** Add local Compose profiles for MySQL, Redis, Qdrant, Elasticsearch, embedding service, and the four applications, with non-conflicting ports and health checks.

**Acceptance criteria:**
- [ ] Minimal profile starts core + gateway; optional profiles enable heavy integrations.
- [ ] Each service exposes liveness/readiness with dependency detail.
- [ ] Persistent volumes and reset operations are explicit and safe.

**Verification:** Compose config validation and health-contract tests.

**Dependencies:** Tasks 3-4

**Files likely touched:** `compose.yaml`, `compose.profiles.yaml`, `platform/health.py`, `docs/deployment.md`

**Estimated scope:** Medium

### Checkpoint: Foundation

- [ ] Current application regression baseline still passes.
- [ ] Each imported application passes its native compile/build tests.
- [ ] No secret or generated runtime data is committed.

### Phase 2: Identity, Gateway, and Shared Contracts

#### Task 6: Define platform identity and role mapping contract

**Description:** Define canonical user, tenant, department, role, and permission claims plus mappings to each subsystem's local models.

**Acceptance criteria:**
- [ ] Admin, analyst, ordinary user, API client, and embedded client mappings are explicit.
- [ ] Unknown claims fail closed.
- [ ] Row/column permissions remain enforced after token exchange.

**Verification:** Contract tests for allow/deny matrices.

**Dependencies:** Tasks 3-4

**Files likely touched:** `platform/auth/contracts.py`, `platform/auth/mapping.py`, `platform/auth/models.py`, `tests/platform/test_role_mapping.py`

**Estimated scope:** Medium

#### Task 7: Implement platform authentication and token exchange

**Description:** Add unified login/session validation and short-lived subsystem tokens without exposing local secrets to the browser.

**Acceptance criteria:**
- [ ] One login opens authorized routes in all enabled sub-apps.
- [ ] Logout/revocation propagates.
- [ ] API Key and embed flows remain supported.

**Verification:** Authentication integration tests including expiry, revocation, and privilege escalation attempts.

**Dependencies:** Task 6

**Files likely touched:** `platform/auth/service.py`, `platform/auth/router.py`, `platform/auth/tokens.py`, `tests/platform/test_auth_flow.py`

**Estimated scope:** Medium

#### Task 8: Implement route gateway and capability registry

**Description:** Add namespaced reverse proxy routing, SSE/WebSocket-safe streaming, capability discovery, and per-subsystem availability reporting.

**Acceptance criteria:**
- [ ] All source API routes are reachable without collisions.
- [ ] SSE query/chat streams preserve event order and cancellation.
- [ ] Disabled/unhealthy capabilities return structured status rather than broken links.

**Verification:** Route inventory diff and proxy integration tests.

**Dependencies:** Tasks 5 and 7

**Files likely touched:** `platform/gateway.py`, `platform/capabilities.py`, `platform/schemas.py`, `tests/platform/test_gateway.py`

**Estimated scope:** Medium

#### Task 9: Standardize trace and audit correlation

**Description:** Propagate trace ID, user ID, tenant, source app, feature name, and request timing through all gateways and background jobs.

**Acceptance criteria:**
- [ ] One cross-system action can be reconstructed by trace ID.
- [ ] Sensitive parameters are masked.
- [ ] Existing daily-sharded audits and OTel/Prometheus metrics continue to work.

**Verification:** Cross-service trace test and log redaction test.

**Dependencies:** Task 8

**Files likely touched:** `platform/observability/middleware.py`, `platform/observability/context.py`, `platform/observability/redaction.py`, `tests/platform/test_trace.py`

**Estimated scope:** Medium

### Phase 3: Data Platform Vertical Slices

#### Task 10: Data source and connection-management slice

**Description:** Surface MySQL, Doris/StarRocks, ClickHouse, PostgreSQL, Oracle, and SQLite fallback capabilities through one portal while retaining native adapters and pool monitoring.

**Acceptance criteria:**
- [ ] Create/test/update data source flows work for every supported engine.
- [ ] Credentials use secret references and are masked in responses/logs.
- [ ] Pool health and fallback state are visible.

**Verification:** Adapter contract tests with mocked drivers plus available integration databases.

**Dependencies:** Tasks 7-9

**Files likely touched:** One adapter/contract/test set per increment (maximum five files)

**Estimated scope:** Medium per database family

#### Task 11: Metadata governance and semantic lifecycle slice

**Description:** Connect physical introspection, semantic datasets, metrics, dimensions, relationships, terms, health checks, YAML import/export, versions, and vector synchronization.

**Acceptance criteria:**
- [ ] Metadata can be discovered, edited, versioned, released, rolled back, and searched.
- [ ] Existing automatic semantic generation and JoinPath behavior remains intact.
- [ ] Source-specific IDs round-trip through the platform mapping.

**Verification:** Golden metadata fixtures and release/rollback tests.

**Dependencies:** Task 10

**Files likely touched:** Implemented as separate discover/edit/release/search increments, each at most five files

**Estimated scope:** Medium per increment

#### Task 12: Resource API, SQL Lab, and publication slice

**Description:** Integrate table/SQL resource definitions, Jinja templates, DSL filters, SQL editor/repair, API Key access, versioning, and one-click publication.

**Acceptance criteria:**
- [ ] Table and SQL resources execute with parameter binding and limits.
- [ ] SQL Lab can generate, validate, execute, repair, save, and publish.
- [ ] Existing AST and static SQL guards both run at their intended boundaries.

**Verification:** Resource API contract suite, SQL injection suite, and publish round-trip.

**Dependencies:** Tasks 10-11

**Files likely touched:** Implemented as resource/query/lab/publish increments, each at most five files

**Estimated scope:** Medium per increment

#### Task 13: Data catalog, approval, and asset panorama slice

**Description:** Integrate product lifecycle, catalog discovery, permission applications, approval/rejection/revocation, change notices, ownership governance, redundancy detection, and panorama metrics.

**Acceptance criteria:**
- [ ] Draft→published→offline lifecycle works.
- [ ] Approval updates effective resource permission and audit records.
- [ ] Catalog and panorama pages expose accurate aggregates and exports.

**Verification:** Lifecycle API/UI integration tests and permission regression tests.

**Dependencies:** Task 12

**Files likely touched:** Implemented as lifecycle/approval/panorama increments, each at most five files

**Estimated scope:** Medium per increment

### Checkpoint: Governed Data Access

- [ ] All Data API Platform source routes are accounted for by route inventory diff.
- [ ] SQL safety, RBAC, masking, audit, and rate limits pass negative tests.
- [ ] A user can register a source, publish a resource, request access, and query it.

### Phase 4: Agent Platform Vertical Slices

#### Task 14: Agent/model/prompt management slice

**Description:** Integrate model vendors, model routing, agent definitions/versions, prompts, scenario templates, slash commands, expert selection, and custom Skill management.

**Acceptance criteria:**
- [ ] Admin can configure/test models and version agents/prompts.
- [ ] Users can select or mention experts and manage allowed personal Skills.
- [ ] Model credentials never reach clients.

**Verification:** CRUD/permission tests and mocked LLM execution tests.

**Dependencies:** Tasks 7-9

**Files likely touched:** Implemented by feature increment, each at most five files

**Estimated scope:** Medium per increment

#### Task 15: Tool, MCP, RAGFlow, OpenClaw, and knowledge slice

**Description:** Integrate tool registry/preflight, MCP servers, RAGFlow agents/datasets/documents/retrieval, OpenClaw auth context, and knowledge execution with citations.

**Acceptance criteria:**
- [ ] Tools are discoverable and permission-gated before execution.
- [ ] Knowledge upload/parse/retrieve/cite lifecycle works when configured.
- [ ] External integration outages degrade without bypassing security.

**Verification:** Mock-server contract tests and optional real-provider acceptance profiles.

**Dependencies:** Task 14

**Files likely touched:** Implemented per integration adapter, each at most five files

**Estimated scope:** Medium per integration

#### Task 16: Conversation, memory, and embed slice

**Description:** Integrate streaming chat, attachments/vision, conversation history, LTM, daily/session summaries, memory search/governance, and embedded Chat SDK.

**Acceptance criteria:**
- [ ] Conversations stream, persist, resume, export, and enforce ownership.
- [ ] Memory can be inspected/rebuilt/deleted according to role.
- [ ] Embedded chat preserves tenant and permission context.

**Verification:** SSE, ownership, attachment, memory, and embed tests.

**Dependencies:** Tasks 14-15

**Files likely touched:** Implemented as chat/memory/embed increments, each at most five files

**Estimated scope:** Medium per increment

#### Task 17: ChatBI, saved reports, subscriptions, and notifications slice

**Description:** Connect ChatBI planning and execution with datasets, examples, briefs, golden reports, exports, subscriptions, alerts, inbox, and notification channels.

**Acceptance criteria:**
- [ ] ChatBI returns SQL plans, trace cards, chart configs, and downloadable results.
- [ ] Reports can be saved, shared according to permission, subscribed, paused, resumed, and run now.
- [ ] DingTalk/WeCom/email test and delivery paths are auditable and opt-in.

**Verification:** End-to-end report lifecycle with fake clocks and notification fakes.

**Dependencies:** Tasks 12 and 16

**Files likely touched:** Implemented as ChatBI/report/subscription/notification increments, each at most five files

**Estimated scope:** Medium per increment

#### Task 18: Scheduler, quotas, SSO, and operations slice

**Description:** Integrate scheduled agent tasks, Redis-backed job storage, quota policies/usage, SSO and third-party user sync, branding, system config, and operational dashboards.

**Acceptance criteria:**
- [ ] Jobs run once under multi-worker deployment and retain execution identity.
- [ ] Quotas are enforced by user/role/system precedence.
- [ ] SSO/sync is idempotent and does not escalate permissions.

**Verification:** Scheduler lock, quota concurrency, and user-sync tests.

**Dependencies:** Tasks 7, 9, and 17

**Files likely touched:** Implemented per operational concern, each at most five files

**Estimated scope:** Medium per increment

### Checkpoint: Enterprise Agent Platform

- [ ] All AI Agent Platform source routes are accounted for by route inventory diff.
- [ ] Main assistant, expert mode, tools, knowledge, memory, ChatBI, reports, and tasks work end-to-end.
- [ ] Permission, quota, audit, and external-integration degradation tests pass.

### Phase 5: Listen-Book LangGraph Vertical Slices

#### Task 19: Import domain schema, bootstrap, and metadata build slice

**Description:** Integrate the 54-table audio domain, smoke/full bootstrap tooling, metadata catalog validation, and versioned knowledge build/rollback.

**Acceptance criteria:**
- [ ] Bootstrap requires explicit reset for destructive initialization.
- [ ] Audio metadata builds and validates with expected table/metric counts.
- [ ] Failed builds leave the previous release active.

**Verification:** Smoke bootstrap and metadata rollback tests.

**Dependencies:** Tasks 1, 5, and 11

**Files likely touched:** Implemented as schema/bootstrap/build increments, each at most five files

**Estimated scope:** Medium per increment

#### Task 20: LangGraph query and safety slice

**Description:** Integrate graph state/nodes, retrieval, schema selection, analysis/query plans, deterministic SQL, dialect handling, row-level policy, SQL guard, correction, and degraded execution.

**Acceptance criteria:**
- [ ] Sync and SSE query flows preserve all graph events and cancellation.
- [ ] SQL white-list, sensitive columns, subquery/star/locking restrictions, and RLS pass negative tests.
- [ ] Deterministic and LLM paths both work and expose plans.

**Verification:** Native golden, security, DSL, planning, and agent E2E tests.

**Dependencies:** Task 19

**Files likely touched:** Imported in node/service testable increments, each at most five files

**Estimated scope:** Medium per increment

#### Task 21: Analysis, insight, feedback, and governance slice

**Description:** Integrate grounded answers, charts, insight cards, deep analysis, recommendations, verified queries, query sets, baselines, feedback learning, accuracy benchmarks, recall tests, traces, and semantic administration.

**Acceptance criteria:**
- [ ] Results include grounded explanations and supported chart/insight payloads.
- [ ] Feedback affects retrievable learned cases with governance controls.
- [ ] Benchmark and trace inspector APIs/UI report reproducible outcomes.

**Verification:** Native insight, feedback, benchmark, recommendation, and governance suites.

**Dependencies:** Task 20

**Files likely touched:** Implemented by analysis/feedback/governance increments, each at most five files

**Estimated scope:** Medium per increment

### Phase 6: Unified Product Experience

#### Task 22: Unified portal shell and navigation

**Description:** Add a single branded entry with login, role-aware navigation, subsystem status, breadcrumbs, and deep links to core问数、数据服务、智能体、听书分析和管理功能。

**Acceptance criteria:**
- [ ] Existing current React flows remain reachable and unchanged in behavior.
- [ ] Every enabled source frontend page is reachable from role-appropriate navigation.
- [ ] Auth/session and return URLs work across all sub-app paths.

**Verification:** Navigation matrix and Playwright smoke tests.

**Dependencies:** Tasks 7-8 and all relevant backend slices

**Files likely touched:** `platform-ui/src/App.tsx`, `platform-ui/src/routes.ts`, `platform-ui/src/navigation.ts`, `platform-ui/src/api.ts`, one test file

**Estimated scope:** Medium

#### Task 23: Cross-system workflow interactions

**Description:** Add explicit handoffs: metadata→ChatBI, SQL Lab→resource→catalog, ChatBI→saved report→subscription, trace→debug, and audio insight→verified query/feedback.

**Acceptance criteria:**
- [ ] Handoffs carry stable resource IDs and authorized context.
- [ ] Users see actionable errors when a target capability is unavailable.
- [ ] No handoff bypasses approval or permissions.

**Verification:** One Playwright journey per cross-system handoff.

**Dependencies:** Tasks 13, 17, 21, and 22

**Files likely touched:** One handoff adapter/UI/test set per increment, at most five files

**Estimated scope:** Medium per workflow

### Phase 7: Verification and Delivery

#### Task 24: Route, page, migration, and capability completeness audit

**Description:** Generate inventories from source and fused repository and require every item to be mapped as implemented, intentionally namespaced, or demonstrably superseded with equivalent tests.

**Acceptance criteria:**
- [ ] 100% backend route mapping.
- [ ] 100% frontend route/page mapping.
- [ ] 100% migration, job, CLI/tool, prompt, and documented capability mapping.

**Verification:** Automated completeness report fails on unmapped items.

**Dependencies:** Tasks 10-23

**Files likely touched:** `tools/audit_capabilities.py`, `tests/completeness/manifest.yml`, `tests/completeness/test_manifest.py`, `docs/capability-map.md`

**Estimated scope:** Medium

#### Task 25: Full quality, security, and resilience gate

**Description:** Run native suites, platform contract/integration/E2E tests, dependency audit, secret scan, permission matrix, SQL injection corpus, degradation, restart, and migration rollback checks.

**Acceptance criteria:**
- [ ] All native and platform tests pass with documented environment profiles.
- [ ] No critical/high security findings remain.
- [ ] Existing DataWareHouse-Agent golden suite has no regression.

**Verification:** CI matrix and signed test report with exact commands/versions.

**Dependencies:** Task 24

**Files likely touched:** `.github/workflows/ci.yml`, `tests/README.md`, `artifacts/verification/report.json`, `docs/security.md`

**Estimated scope:** Medium

#### Task 26: Operations and handover documentation

**Description:** Provide development, deployment, upgrade, backup/restore, observability, troubleshooting, data migration, and source-sync runbooks.

**Acceptance criteria:**
- [ ] A new operator can start the minimal and full profiles from clean state.
- [ ] Backup/restore and rollback are rehearsed.
- [ ] Known external dependencies and acceptance gaps are explicit.

**Verification:** Clean-machine dry run of documented commands.

**Dependencies:** Task 25

**Files likely touched:** `README.md`, `docs/deployment.md`, `docs/operations.md`, `docs/upgrading-sources.md`, `docs/troubleshooting.md`

**Estimated scope:** Medium

## Final Checkpoint: Complete

- [ ] Current project's original features and user worktree changes are preserved.
- [ ] Three source snapshots have 100% capability inventory coverage.
- [ ] All automated suites, builds, security checks, and representative E2E journeys pass.
- [ ] External-service-dependent items are validated in a configured acceptance environment.
- [ ] Licensing/provenance, deployment, rollback, and operations documentation is complete.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Gitee source has no LICENSE | High | Require owner authorization or compatible license before copying code |
| Conflicting Python/frontend versions | High | Independent app locks and processes; integrate through contracts/gateway |
| Three incompatible auth/RBAC models | High | Canonical claims + explicit mapping + deny-by-default contract tests |
| Duplicate table/model names and migrations | High | Separate schemas/databases first; map IDs; consolidate only after parity tests |
| Hidden functionality not documented in README | High | Source-derived inventories for routes/pages/jobs/scripts/prompts/migrations |
| External systems unavailable in local CI | Medium | Fake contract tests plus opt-in real integration acceptance profiles |
| Existing uncommitted user work overwritten | High | Record dirty baseline, additive changes, never reset/restore user files |
| UI framework/version collision | High | Same-origin micro-frontends/sub-app builds behind a unified shell |
| Security weakened by gateway shortcuts | High | Preserve subsystem guards and add gateway-level negative tests |
| Scope too large for one atomic change | High | Deliver vertical slices with checkpoints; each increment buildable and reviewable |

## Open Questions / Required Decision

1. 请确认你拥有将 `https://gitee.com/laixiaogang/listen-book-data-agent` 源码复制、修改并随本项目再分发的权利，或请为该仓库补充明确许可证。
2. 计划默认采用“单仓库、多应用、统一门户/登录/部署”的融合形态，这是能最大程度保证零逻辑缺失的方案；若要求最终必须变成单一 FastAPI 进程和单一前端 bundle，需要接受显著更高的重写与回归风险。
3. 真实外部服务验收需要后续提供可用的测试环境与凭证；凭证不写入仓库。
