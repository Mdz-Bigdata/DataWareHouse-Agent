# ChatBI 域模块（`chatbi/`）

`DataAgentRunner`（`../data_agent_runner.py`）是薄编排层：负责 turn 路由、会话上下文与向后兼容门面；查数守卫与 ReAct 逻辑按域拆在本目录。

## 编排入口（仍在 Runner）

| 步骤 | Runner 方法 | 委托模块 |
|------|-------------|----------|
| 执行入口 | `execute()` → `_execute_raw()` | — |
| 早退 turn | `_execute_raw` | `turn_handlers` |
| 独立问题改写 / Schema 预取 | `_execute_raw` | `schema_prefetch` |
| Few-shot 注入 | `_inject_few_shot_examples` | `few_shot` |
| System prompt | `_build_system_content` | `system_prompt` |
| Native ReAct + 修复轮 | `_run_native_agent_turn` | `native_turn` |
| 挂起恢复 | `resume_agentscope_*` | `resume_stream` |

## 模块索引

| 模块 | 职责 |
|------|------|
| `run_state.py` | `DataRunState`（`_DataRunState` 别名）运行态；含 `schema_output`、`table_bindings`、`sql_query_binding` |
| `constants.py` | Gate 前缀、修复预算等常量 |
| `turn_handlers.py` | FORMAT / REUSE / CLARIFICATION 早退；联邦查询升级 |
| `schema_prefetch.py` | 查询改写、意图 keywords 规划、自动 `get_dataset_schema` |
| `few_shot.py` | 经验库检索与 prompt 注入 |
| `system_prompt.py` | ChatBI system prompt 组装 |
| `agent_builder.py` | 工具解析、AgentScope `Agent` 构建 |
| `native_turn.py` | ReAct 主循环 + repair 轮次 + AgentState 持久化 |
| `react_stream.py` | AgentScope 事件流映射、final guard、内容撤回 |
| `tool_gate_wrapper.py` | Schema / SQL 重复等工具层 Gate；构建并注入 `SqlQueryBinding` |
| `tool_result_handlers.py` | Schema/SQL 工具结果解析与应用；写入 `table_bindings` |
| `sql_gates.py` | SQL 静态风险、schema 预检辅助函数、Gate 检测 |
| `sql_repair_hints.py` | SQL 修复 hint 文案 |
| `sql_result_parser.py` | 结果解析、空结果/异常检测 |
| `repair_policy.py` | 修复种类、预算、`repair_message`、tool_choice |
| `schema_retry.py` | 受控 schema 重试 keywords |
| `schema_fatal.py` | Schema 致命错误判定与响应 |
| `empty_filter.py` | 空结果 filter 诊断与 auto-retry |
| `federated_upgrade.py` | 跨数据集联邦查询升级启发式 |
| `../chatbi_sql_query_binding.py` | **SqlQueryBinding**：Schema/SQL/元数据统一绑定；预检、execute 校验、联邦 plan 表→dataset 约束 |
| `forced_tool_choice.py` | 首轮/修复轮强制 tool_choice 包装 |
| `tool_loop.py` | 工具循环检测与签名 |
| `state_serialization.py` | 挂起态与 `DataRunState` 互转 |
| `clarification.py` | 非查数 / 缺上下文澄清 |
| `synthesis.py` | 复用结果 / 格式修正 / 缓存 SQL 合成 |
| `followup_data.py` | 上一轮结构化结果读写 |
| `metadata_guide.py` | 从授权 Schema 生成业务主题、指标、维度和澄清候选 |
| `drilldown_context.py` | 合并上一结果分析条件并约束下钻 SQL 重新过门禁 |
| `non_data_policy.py` / `handoff.py` | 非查数本地回答、结果动作和无感委派 |
| `source_reclassification.py` | Schema 连续未命中后的请求来源重判 |
| `insight_meta.py` | 结果证据、继续分析、简报和监控动作契约 |
| `resume_stream.py` | AgentScope 挂起流恢复 |

## 连续分析与交付（2026-07）

- `chatbi_result_stack.py` 按用户与会话维护最多 10 个版本化结果引用，支持当前、上一结果和显式引用。
- ChatBI turn 拆为数据追问、结果分析、结果呈现和结果动作；数据追问继承未被本轮覆盖的指标、筛选与时间条件。
- 显式多步骤请求由 `chatbi_task_plan.py` 生成依赖计划；当前仅对 query/analyze/present 串行执行。brief/monitor/action 在 operation-aware 调度闭环前不自动拆句，避免被误当作 SQL 查询。
- 元数据问答同时发出 `chatbi_metadata_guide`，两个聊天界面共享渲染；快捷问题只来源于授权 Schema。
- “生成业务简报”调用 `/api/portal/chatbi-briefs` 生成可追溯 Markdown 与 Word；“创建监控”调用 `/api/portal/chatbi-monitors` 转为黄金报表订阅。
- 订阅支持阈值、变化率、连续命中与无数据告警，并在投递记录中保存触发证据。

## 向后兼容

- `data_agent_runner.py` 保留大量 `_xxx()` 一行委托，供集成测试 monkeypatch 与子模块 `runner._xxx` 调用。
- 公开 re-export：`_DataRunState`、`UpgradeToFederatedQuery`、`_should_upgrade_to_federated_query` 等仍在 Runner 模块级导出。

## 测试

- 集成：`tests/ai/runners/test_data_agent_runner.py`
- 域单元：`tests/ai/runners/test_chatbi_modules.py`
- SqlQueryBinding：`tests/ai/test_sql_query_binding.py`

## SqlQueryBinding 数据流（2026-06）

`get_dataset_schema` → `state.table_bindings` + `schema_output` → execute 前 `build_sql_query_binding` → `resolve_sql_schema_preflight_with_binding`（Gate 预检）→ ContextVar → `execute_sql_query_core`（表归属 / 权限 / 字段 / data_perm 复用同一份 binding）→ 跨库失败时 `build_federated_upgrade_binding` 注入联邦 plan prompt 并修正 subquery `dataset_name`。

## 相关文档

- [CHAT_BI_DESIGN.md](../../../../architech/design/CHAT_BI_DESIGN.md)
- [CHATBI_GUARDS_REVIEW.md](../../../../architech/design/CHATBI_GUARDS_REVIEW.md)
- [AGENTSCOPE_RUNTIME.md](../../../../architech/design/AGENTSCOPE_RUNTIME.md)
- [ai_agent_gating_contract.md](../../../../docs/md/ai_agent_gating_contract.md)
