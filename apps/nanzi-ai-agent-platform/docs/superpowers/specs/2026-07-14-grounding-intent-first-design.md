# Grounding 意图优先降硬编码设计

## 目标

取消 GENERAL 回答仅凭“今天、当前、最新”等输出词触发事实审核的兜底，改为优先依赖用户问题的统一 `RequestDecision` 和工具证据元数据，降低语义正则误报与维护成本。

## 方案选择

不采用继续增加问候白名单，也不新增一次 LLM 分类调用。复用现有 `resolve_request_decision()`：当上游路由提示为 GENERAL 时，如果用户问题的确定性请求决策明确要求公网或运行状态证据，则以该决策升级事实要求；否则保持普通 GENERAL 流式通过。

## 规则边界

- `PUBLIC_WEB`、`RUNTIME_DIAGNOSTIC`、`INTERNAL_STRUCTURED_DATA` 和 `INTERNAL_DOCS` 继续要求对应凭证；
- `UNKNOWN` 继续对最终结构化事实做保守兜底；
- `GENERAL` 不再设置 `scrutinize_dynamic_output`，不再扫描完整回复中的动态词；
- 金额、表格、日期、ID、错误结果和证据内容关联等确定性规则保持不变；
- 问候语句段识别修复保留，用于 UNKNOWN 和 ChatBI 等仍需输出结构检查的路径。

## 流式影响

普通 GENERAL 不收集完整正文、不执行结束审核，继续直接流式。被用户问题意图升级为需要证据的请求仍按原方式缓冲或审核，因此真实天气、汇率和运行状态查询不会绕过门禁。

## 验收

- “你好”“今天有什么可以帮您”无提示；
- GENERAL 路由提示下的“查询汇率/天气”仍升级为 `PUBLIC_WEB` 并要求证据；
- GENERAL 普通回答即使模型自行出现动态措辞，也不由该兜底提示，避免对模型寒暄和举例误报；
- UNKNOWN、MCP、ChatBI、KnowledgeAgent 和恢复路径现有回归保持通过。
