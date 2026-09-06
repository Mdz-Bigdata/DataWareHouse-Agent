## 1. 数据库升级与 Trace 基础建设

- [x] 1.1 创建 DDL 迁移脚本 V81-add_span_columns_to_traces.sql，增加 span_id、parent_span_id、meta_info 字段
- [x] 1.2 在 app/models/audit.py 中更新 AgentExecutionTrace ORM 模型
- [x] 1.3 编写 app/services/ai/runtime/agentscope/trace_context.py 协程安全 TraceSpan 上下文管理器
- [x] 1.4 在各 Agent 核心 Runner 和工具执行层嵌入 TraceSpan 监控逻辑
- [x] 1.5 新增只读树形 Trace API：GET /api/portal/traces/{trace_id}/spans
- [x] 1.6 在前端 AgentDebug.vue 与 EmbedChat.vue 中渲染树形时延甘特图并支持点击查看 Span 详情

## 2. 交互式数据透视画布与图表微调

- [x] 2.1 编写前端行列透视表格组件 PivotTable.vue，实现纯 JS 多维聚合算法
- [x] 2.2 在 ChatCanvas.vue 中引入并集成 PivotTable.vue 面板，提供行列字段拖拽 UI
- [x] 2.3 在前端和 turn_classifier.py 层面构建 ECharts 样式微调指令拦截与短路重绘逻辑

## 3. 跨数据集 DuckDB 联邦查询引擎

- [x] 3.1 对齐现有的 metadata 模块，验证支持以多数据集 Schema YAML 合并方式供给大模型，明确表和数据集的映射关系
- [x] 3.2 升级 data_query_turn_classifier.py，支持跨源联合查询的意图识别并标志为 FEDERATED_DATA_QUERY
- [x] 3.3 编写 FederatedQueryExecutor 核心逻辑，解析联邦计划中的子查询 `dataset_id` 或 `dataset_name`
- [x] 3.4 在 FederatedQueryExecutor 底层中，根据子查询的数据集 ID 获取对应数据源 Adapter 连接，完成权限门禁隔离校验（防 IDOR）
- [x] 3.5 实现基于 DuckDB 临时表的数据源子查询结果注册与内存 Join 执行逻辑

## 4. 测试与验证

- [x] 4.1 编写 TraceSpan 嵌套计时与 parent 继承测试用例 tests/test_trace_span.py
- [x] 4.2 编写联邦查询引擎与权限拦截测试用例 tests/test_federated_executor.py
- [x] 4.3 更新 tests/CHECKLIST.md 自动化测试清单，把链路监控 Trace、数据透视画布和跨数据集 DuckDB 联邦查询测试项录入
