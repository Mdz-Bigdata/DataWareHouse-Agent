# data-agent-engine integration baseline

## Source baseline

- Repository: `https://github.com/zao-sheng/data-agent-engine`
- Imported commit: `de8157e19562b28f601060568ef5f68a7865b0a6`
- License: MIT
- Design source: `https://mp.weixin.qq.com/s/3Kx2RCXhG40lN2XdlCKQQw`
- Design title: 《基于 DeepSeek Harness（DSH）的 Data Agent 落地方案》
- Retrieved: 2026-09-08

The article is used as a requirements source. This document paraphrases its
capabilities and maps them to implementation; it does not republish the article.

## Complete capability map

| Design requirement | Imported implementation | Unified-project integration |
|---|---|---|
| “生产数 → 取到数 → 分析数” lifecycle | Modeling, deterministic query, metadata and exploration tools | Existing analysis/chart layer plus the new deterministic-engine workbench |
| Four request paths | `intent_classify`, DSH `plan-routing` | Intent API and UI routing for query, metadata, modeling, operation and exploration |
| Six-layer architecture | DSH conversation/preset/Skills, MCP tools, Python core, Ontology, stores | Native MCP remains intact; HTTP adapter and portal become an additional interaction layer |
| Ontology as single source of truth | objects/functions/relations/glossary/config YAML | Exact source retained; YAML/SQLite/Supabase modes remain selectable |
| OAG retrieval | `ontology_search`, `ontology_traverse`, `term_normalize` | Tool API and ontology inspection UI |
| MQL intermediate language | `mql_validate`, `mql_explain` | Editable MQL, validation and explicit confirmation interaction |
| Deterministic translation | `semantic_translate` | Confirm-token-gated translate action; generated SQL is not modified by the adapter |
| Controlled execution | `execute_sql`, read-only executor, row cap | Query-token-gated execution; sample SQLite is the safe default |
| Metric-family disambiguation | `metric_disambiguate` | Workbench action exposes variants, formula, filters, version and dimension mismatch |
| Metadata consultation | `metadata_search` | Table, caliber, lineage and readiness response viewer |
| Exploratory querying | `explore_validate`, `explore_execute` | SQL editor with validate-before-execute and independent exploration token |
| Exploration promotion | `explore_promote` | Promotion draft viewer feeding the modeling flow |
| Modeling and ETL | `ddl_generate`, `etl_generate`, `modeling_plan` | Read-only plan generation in UI; registration remains confirmation and policy gated |
| Ontology governance | `ontology_register`, `ontology_reload` | Native tool retained; HTTP mutation disabled by default and explicitly configurable |
| DSH data-assistant behavior | preset, persona and eight Skills | All preset and Skill files retained byte-for-byte for native DSH installation |
| Triple-token safety | confirmation, query and exploration stores | Tokens remain generated and verified inside the original engine process |
| Row-level security | translator injects `user.region_ids` | Generic tool runner accepts explicit `user.region_ids`; no implicit elevation |
| Audit and runtime logging | JSONL audit/runtime logs with rotation | Dedicated volume and trace header propagation through the unified gateway |
| Local and remote data media | SQLite plus MySQL/Doris/Hive/SparkSQL DSNs | Sample DB generated before tests/start; remote DSNs remain opt-in secrets |
| Ontology stores | YAML, compiled SQLite and Supabase with revisions/RLS | Environment-selectable; source import/export/sync utilities retained |
| Evaluation gate | 189 engine tests, 29 golden cases and 8 DSH tests at baseline | Import hashes and source/tool/Skill completeness are enforced by `platform.sh verify`; native suites are also run in an isolated declared-dependency environment |
| Feedback loop | Article defines adopt/modify/reject attribution | Existing DataWareHouse-Agent feedback flywheel remains available as an adjacent top-level workflow |
| Analysis extension | Article marks analysis as a later Skill | Current project's grounded explanation, visualization and deep-analysis capabilities satisfy the extension point |
| Scheduler integration | Upstream `scheduler_submit` is explicitly a placeholder | HTTP adapter replaces it with an idempotent NanZi task-center bridge when the bundled scheduler is configured |

## Native tools retained

The imported MCP server exposes 20 tools without altering their implementation:

1. `health_check`
2. `ontology_search`
3. `ontology_traverse`
4. `mql_validate`
5. `mql_explain`
6. `semantic_translate`
7. `execute_sql`
8. `metric_disambiguate`
9. `term_normalize`
10. `intent_classify`
11. `metadata_search`
12. `ddl_generate`
13. `etl_generate`
14. `modeling_plan`
15. `ontology_register`
16. `ontology_reload`
17. `explore_validate`
18. `explore_execute`
19. `explore_promote`
20. `scheduler_submit`

## Baseline verification

On a fresh clone, six native tests fail until `backend/seed/sample.db` exists.
After running the deterministic seed generator with seed `42`, the imported
commit passes:

- Engine unit tests: 189/189
- Golden evaluation: 29/29
- DSH setup tests: 8/8

The integration image therefore generates the sample database deterministically
before startup. The database is a runtime artifact and is not committed.
