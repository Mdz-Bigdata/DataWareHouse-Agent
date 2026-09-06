# chatbi Specification

## Purpose
ChatBI 模块旨在通过自然语言交互实现复杂业务数据的查询、分析与可视化。它通过动态生成 SQL 并结合用户反馈持续演进，确保数据获取的准确性与合规性。

## Requirements

### Requirement: 反馈驱动的 Few-Shot 优化 (Feedback-Driven Optimization)
系统 **MUST** 支持收集用户对回答的点赞/点踩反馈，并将优质案例沉淀为经验库。

#### Scenario: 经验沉淀
当用户点赞时，系统应从执行链路中提取最终成功的 SQL，并同步至 RAGFlow 知识库。

#### Scenario: 相似检索
在新问题生成 SQL 前，系统 **MUST** 语义检索经验库。若匹配到相似案例（相似度 > 0.2），应作为 Few-Shot 示例注入 Prompt。

#### Scenario: 幂等性与一致性
同步逻辑 **MUST** 确保 RAGFlow 端文档的唯一性。废弃或驳回案例时，**MUST** 同步物理删除远程文档。

### Requirement: 数据来源真实性 (Data Veracity)
... (保持原有内容) ...
ChatBI 模块 **MUST** 仅能从定义的 Tools 中获取数据，严禁模型幻觉生成虚假指标。

#### Scenario: 查询不存在的机房
用户查询一个不存在的机房数据时，系统应明确通过工具反馈“未找到该机房”，而不是编造数据。

### Requirement: 结果解释能力 (Interpretability)
ChatBI 在返回查询数据后，**MUST** 同时附带一段人类可读的结论性分析。

#### Scenario: 能耗异常提醒
当查询结果显示 PUE 超过 2.0 时，AI 不仅要展示数字，还应在回复中指出“该指标偏高，建议检查冷却系统”。

### Requirement: 行级数据隔离 (Row-Level Security)
ChatBI 在执行 SQL 查询时，**MUST** 将预定义的权限过滤条件注入到原始 SQL 的 `WHERE` 子句中，确保用户仅能访问其授权范围内的数据。

#### Scenario: 注入位置
改写引擎 **MUST** 遍历 SQL AST 中的所有 `SELECT` 表达式，并将条件追加到 `WHERE` 子句中，使用 `AND` 操作符连接。

#### Scenario: 别名处理
注入的条件 **MUST** 使用对应的表别名作为前缀（如：`original_table AS t1` -> `t1.region_code = 'SH'`）。

#### Scenario: 占位符解析
注入字符串中包含的占位符（如 `{user.dept_id}`） **MUST** 在注入前被替换为当前用户的实际维度值。

#### Scenario: SQL 注入防御
所有的动态注入值（特别是字符串） **MUST** 经过转义或参数化处理，防止恶意用户利用维度值进行二次 SQL 注入。

