# 路由语义证据与 Main 委派设计

**日期：** 2026-06-26
**状态：** 待评审

## 背景

当前一次对话会先由智能路由选择 Agent，随后由已选中的 Agent 做会话级意图分类。两层均由模型判断“是否为数据查询”，但没有共享结论。

因此可能出现：外层把结构化记录查询路由至 Main；内层识别为 `DATA_QUERY`，却因 Main 不具备 `data_query` capability 而按通用路径降级。该降级用于防止编造数据，却不能让请求自然到达正确的执行能力，且 trace 呈现为看似矛盾的两次判断。

本设计不为“机房/资产”等具体领域添加关键词路由规则，也不把内层 `DATA_QUERY` 识别强制改派为 ChatBI。

## 目标

1. 在 Agent 选择前产生一次与具体 Agent 无关的请求语义证据。
2. 让路由器消费该证据，按 capability 选择 Agent，避免同一请求被两套模型独立且冲突地解释。
3. Main 被有意选为编排入口时，优先通过 `sub_agent_call` 委派具备 `data_query` capability 的 Agent，而非自行回答或被强制跳转。
4. 保持专家直选、权限过滤、多 Agent、知识库和非数据请求的现有行为。

## 非目标

- 不按机房、资产、设备或任何业务对象写专用路由白名单。
- 不因为内层结果为 `DATA_QUERY` 而绕过用户显式选择的 Agent。
- 不要求 Main 在没有可用数据 Agent 时伪造数据或自动跨权限调用。
- 不改动 ChatBI 内部的新查数、结果复用、元数据查询等轮次分类职责。

## 设计

### 1. 请求语义证据

在自动路由入口，对最后一条用户消息执行现有通用意图分类器，生成结构化证据：

- `intent`：`DATA_QUERY`、`KNOWLEDGE_BASE`、`GENERAL` 或 `UNKNOWN`；
- `confidence`、`reasoning`、`entities`；
- 由已有启发式短路产生的等价分类结果。

该证据只描述请求语义，不指定具体 Agent，也不负责 ChatBI 内部轮次。

为避免额外、冲突的模型调用，后续会话分类复用该证据；只有 ChatBI 进入执行后，才保留其独立的 `DataQueryTurnClassifier`，用于区分新查数、复用结果、上下文动作和元数据请求。

### 2. capability 驱动的路由

路由器接收“语义证据 + 当前用户有权限的 Agent 清单”。处理规则如下：

| 语义证据 | 路由约束 |
| --- | --- |
| 高置信 `DATA_QUERY` | 优先在具有 `data_query` capability 的候选中选择；候选的描述仍用于选择具体领域专家。 |
| 高置信 `KNOWLEDGE_BASE` | 沿用知识库绑定与知识 Agent 的既有优先级。 |
| `GENERAL`、`UNKNOWN` 或低置信 | 沿用当前 LLM 路由和 Main 兜底逻辑。 |
| 没有可访问的数据 Agent | 可选择 Main，但必须保留 `DATA_QUERY` 语义证据。 |

“高置信”的具体阈值应复用现有意图分类的模糊阈值或抽成统一配置，避免路由层另起一套常量。

显式 `agent_id`、`agent_name`、版本选择和 `@` 提及属于专家直选，跳过该约束。

### 3. Main 编排与委派

Main 是允许的编排入口，而非错误状态。当最终选中的 Main 没有 `data_query` capability、但请求语义为高置信 `DATA_QUERY` 时：

1. 把语义证据传入 `AssistantExecutor` / `AssistantAgentRunner`；
2. 若当前用户可委派至少一个数据 Agent，则对 `sub_agent_call` 注入本轮优先提示；
3. Main 仍可根据完整上下文选择合适的数据子 Agent；
4. 若没有可委派的目标，继续现有防幻觉与切换建议逻辑。

这不是强制跳转。Main 可以承担组合任务、先澄清或委派多个子 Agent；但不应在未获取真实数据时直接给出内部结构化数据结论。

### 4. 可观测性

Trace 将拆开显示三个事实，避免“前后改口”的错觉：

1. `请求语义识别`：意图、置信度、理由；
2. `智能路由决策`：候选 capability、选中 Agent、是否受语义证据约束；
3. `任务委派`：如由 Main 编排，展示 `sub_agent_call` 的目标与结果。

原始路由结论不被覆盖；若缺少数据 Agent 或权限不足，trace 应显示该回落原因。

## 兼容性与错误处理

- 不改变直接选择专家的执行路径。
- 路由/意图模型不可用时沿用现有 Main 兜底，不阻断会话。
- 数据候选必须来自已按用户权限过滤后的 Agent 清单。
- 多个数据 Agent 时不以固定名称硬编码 ChatBI；由现有候选描述和路由选择具体目标。
- 保留 ChatBI 内部分类，防止把“选择数据 Agent”误解为“本轮一定执行 SQL”。

## 验证

1. 使用模拟的通用 `DATA_QUERY` 证据，验证路由优先选择任何具备 `data_query` capability 的候选，而非测试特定业务词。
2. 验证 `GENERAL`、低置信和没有数据候选时保留现有 Main 兜底。
3. 验证用户无权限的数据 Agent 不会进入候选集。
4. 验证专家直选不受语义路由约束。
5. 验证 Main 拿到 `DATA_QUERY` 语义且存在可委派数据 Agent 时，对 `sub_agent_call` 产生优先提示；无候选时保持防幻觉回复。
6. 验证 trace 同时展示语义、路由和委派，不再以“当前 Agent 无能力”的降级日志掩盖真实决策。

## 实施范围

- `app/services/ai/agent_service.py`
- `app/services/ai/context_manager.py`
- `app/services/ai/router_service.py`
- `app/services/ai/turn_classifier.py`
- `app/services/ai/runners/assistant_agent_runner.py`
- 相应的 router、turn classifier、assistant runner 测试。
