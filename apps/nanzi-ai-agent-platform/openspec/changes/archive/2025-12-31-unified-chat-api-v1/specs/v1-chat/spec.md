# 规范：V1 统一对话能力 (V1 Unified Chat Capability)

## ADDED Requirements

### Requirement: 统一意图决策 (Unified Intent Decision)
对外接口 **MUST** 在处理任何业务逻辑前，先进行意图识别决策。

#### Scenario: 自动分类分发
当外部系统发送“上月 PUE 统计”时，V1 接口应内部识别为数据查询并触发对应的逻辑链路，而不是按普通聊天返回。

### Requirement: 响应结构标准化 (Standardized Response Structure)
所有 V1 对话响应 **MUST** 包含意图标识，以便第三方系统根据意图类型展示不同的 UI 组件（如表格、图表或文本）。

#### Scenario: 外部系统自适应展示
第三方应用通过响应中的 `intent: "DATA_QUERY"` 标识，决定在界面上弹出一个图表弹窗而不是仅仅显示文字。

### Requirement: 异常处理安全性 (Safe Exception Handling)
当 LLM 解析失败或意图不明时，接口 **MUST** 返回友好的降级处理（Fallback），不能暴露后端堆栈信息。

#### Scenario: 模型异常时的优雅提示
当内部网关响应失败时，接口应返回“抱歉，智能体暂时无法解析此请求，请稍后重试”，并保持 HTTP 200 或适当的业务错误码。
