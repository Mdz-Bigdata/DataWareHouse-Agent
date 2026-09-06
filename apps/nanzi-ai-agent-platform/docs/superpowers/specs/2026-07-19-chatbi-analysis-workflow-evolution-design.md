# ChatBI 分诊、连续分析与业务交付设计

## 目标

把 ChatBI 从“单轮 Text-to-SQL 执行器”升级为能够正确分诊、连续分析、生成业务交付物并持续监控结果的数据分析智能体。改造分为 P0 流程边界、P1 连续分析、P2 任务交付三个层级，后一级复用前一级的数据契约，不以 Prompt 拼接替代结构化状态。

## 设计原则

1. Router 只决定候选智能体，ChatBI 内部分类型器决定进入后的轮次语义；灰区不得在 Router 启发式层直接判死。
2. 非查数不是统一拒绝：能力帮助本地回答，结果动作留在 ChatBI 执行，通用/公网任务无感委派 Main。
3. 首轮只有高置信内部结构化数据请求才能跳过分类；未知请求必须分类，不能默认查库。
4. Schema 连续未命中代表数据来源可能判断错误，应重判来源后委派或给出可执行出口。
5. 连续分析继承指标、维度、过滤器和时间等语义状态，不修改上一轮 SQL 字符串。
6. 简报、订阅和告警只消费已执行、可追溯的分析节点，不让模型重新编造事实。

## P0：流程边界

### 非查数处置

新增 `NonDataDisposition`：

- `LOCAL_HELP`：寒暄、身份、能力、BI 概念和当前数据解释，由 ChatBI 本地回答。
- `RESULT_ACTION`：导出、保存、发送、生成文件、创建订阅等与当前结果相关的动作，保留在 ChatBI 工具执行链。
- `DELEGATE_MAIN`：通用写作、翻译、平台自助、运行诊断等交给 Main。
- `DELEGATE_WEB`：公网资料、动态事实和行业数据交给具备联网能力的 Main。

委派通过结构化 runner 事件表达，保留原始问题、对话上下文和可选结果引用。若运行环境没有可用委派器，降级为带 `/switch_to_auto` 的现有引导。

### 外层粘性

`should_inherit_data_agent_session()` 改为三态决策：`KEEP`、`BREAK`、`UNCERTAIN`。只有明确寒暄、公网、平台自助或无关任务为 `BREAK`；明确查数与结果追问为 `KEEP`；其余交给统一语义 Router，不再直接 fallback Main。

### 首轮分类

保留高置信 `NEW_DATA_QUERY`、`METADATA_QUERY`、`NON_DATA_REQUEST` 快通道。首轮灰区调用轻量轮次分类；分类失败时返回能力引导或澄清，不触发 Schema/SQL。

### Schema 来源重判

连续两次无相关 Schema 时调用确定性的来源信号与轻量分类，产出 `INTERNAL_DATA`、`INTERNAL_KNOWLEDGE`、`PLATFORM_SELF_HELP`、`PUBLIC_WEB`、`GENERAL` 或 `UNKNOWN`。非数据来源通过委派事件继续完成；`UNKNOWN` 保留现有 Schema 未命中说明并给出数据门户入口。

## P1：连续分析

### 结果栈

用 `ChatBIResultRef` 替代单一 `last_data_result`：

- `result_id`、`parent_result_id`、`conversation_id`、`trace_id`
- `question`、`dataset_name`、`data_source`
- `metrics`、`dimensions`、`filters`、`time_range`、`time_grain`
- `sql`、`rows`、`result_summary`、`created_at`、`expires_at`

Redis 保存最近 10 个节点和当前节点，旧 `last_data_result` 读写保持兼容。引用解析支持“当前结果”“上一个结果”“区域那张表”和显式 `result_id`；不唯一时返回真实候选。

### 轮次类型

把结果相关行为拆为：

- `DATA_FOLLOWUP_QUERY`：改变筛选、维度、时间或指标，需要重新查数。
- `RESULT_ANALYSIS`：只基于已有结果总结、解释、排名或贡献分析。
- `RESULT_PRESENTATION`：图表类型、颜色、格式和布局调整。
- `RESULT_ACTION`：保存、导出、订阅、生成文件或发送。

旧枚举保留兼容映射。

### 元数据导航

`METADATA_QUERY` 返回结构化 `chatbi_metadata_guide`：业务主题、可用指标、维度、时间字段、更新时间、可关联数据集和真实 quick queries。前端渲染导航卡，文本客户端继续收到 Markdown。

### 真实候选澄清

澄清候选只能来自 Schema 预取结果、数据集目录、指标定义或结果栈。响应携带 `candidate_type`、`physical_name`、`display_name`、`dataset_name` 和可执行 query；没有真实候选时不伪造按钮。

## P2：任务交付

### 混合意图与串行任务

新增 `ChatBITaskPlan`，步骤类型为 `query`、`analyze`、`present`、`export`、`delegate`、`save_report`、`subscribe`。每步声明 `depends_on`，只有前置步骤成功才运行；失败时保留已完成产物并给出恢复点。

### 条件继承下钻

下钻操作使用受限 DSL：`replace_dimension`、`add_dimension`、`add_filter`、`remove_filter`、`set_time_grain`、`shift_time_range`、`set_analysis_method`、`drill_to_detail`、`return_to_parent`。服务端把操作合并进父节点语义上下文，再走完整 Schema、权限和 SQL Gate。

### 业务简报

分析节点可加入简报。服务端生成带证据引用的结构化简报：执行摘要、核心指标、趋势、结构贡献、异常事项、数据说明。第一版输出在线 Markdown/HTML 和 Word；PPT 复用模板化生成器后续扩展。每条结论必须引用 `result_id` 和确定性事实。

### 监控、订阅与告警

从分析节点创建黄金报表与订阅，并支持条件：阈值、变化率、连续次数和无数据。调度执行后先评估条件，只有命中才投递；投递保存触发事实、比较基线和关联运行。无条件订阅保持现有定时投递行为。

## 兼容与安全

- 旧客户端忽略新增 SSE 事件仍可工作。
- 旧 `last_data_result`、`REUSE_PREVIOUS_RESULT`、`FORMAT_CORRECTION` 和无条件订阅继续兼容。
- 所有重新查数和下钻都经过现有 Schema、权限、SQL 静态风险、沙箱和执行门禁。
- 委派不得绕过用户可见智能体权限与工具权限。
- 结果栈按用户和会话隔离，并设置 TTL；简报和订阅落库后继续执行现有 RBAC。

## 验收

1. 明确非查数分别本地回答、执行结果动作或无感委派，不再统一提示切换。
2. ChatBI 后续灰区请求会进入语义 Router，明确话题切换仍能快速离开。
3. 首轮灰区不触发 Schema/SQL；明确查数无新增延迟。
4. 连续 Schema miss 能重判来源并产生正确出口。
5. 用户能引用最近多个结果，结果分析/渲染/动作/重新查数互不混淆。
6. 元数据导航和澄清候选全部来自真实授权元数据。
7. 混合请求按依赖顺序执行，条件下钻保留父节点范围。
8. 简报的结论、图表与数据说明可追溯到结果节点。
9. 分析节点可创建无条件订阅或条件告警，旧订阅不受影响。

