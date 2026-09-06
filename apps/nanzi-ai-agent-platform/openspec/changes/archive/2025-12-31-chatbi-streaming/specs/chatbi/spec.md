# 规范：ChatBI 数据查询能力 (ChatBI Data Capability)

## ADDED Requirements

### Requirement: 数据来源真实性 (Data Veracity)
ChatBI 模块 **MUST** 仅能从定义的 Tools 中获取数据，严禁模型幻觉生成虚假指标。

#### Scenario: 查询不存在的机房
用户查询一个不存在的机房数据时，系统应明确通过工具反馈“未找到该机房”，而不是编造数据。

### Requirement: 结果解释能力 (Interpretability)
ChatBI 在返回查询数据后，**MUST** 同时附带一段人类可读的结论性分析。

#### Scenario: 能耗异常提醒
当查询结果显示 PUE 超过 2.0 时，AI 不仅要展示数字，还应在回复中指出“该指标偏高，建议检查冷却系统”。
