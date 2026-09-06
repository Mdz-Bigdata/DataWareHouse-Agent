# ChatBI 显式最终 SQL 角色设计

## 目标

修复 ChatBI 在诊断查询后已成功执行最终明细 SQL，却因 `IS NOT NULL` 与限行等文本形态再次被识别为诊断 SQL，最终触发“阻止未查数回答”的问题。

## 设计

- 在 `DataRunState` 增加一次性的 `next_sql_is_final_business_query` 运行态标记。
- 仅当 repair 原因为 `diagnostic_sql_pending_final` 时设置该标记，表示平台已明确要求下一次 `execute_sql_query` 是最终业务 SQL。
- SQL 结果处理时优先消费显式标记；已标记的查询不再通过 SQL 文本形态判定为诊断查询。
- 保留现有 `is_diagnostic_sql()` 作为首次模型探查的兼容判定，不改变真实 `SELECT DISTINCT ... LIMIT ...` 样本查询的门禁。

## 验证

- 复现用例：诊断 repair 后执行带 `IS NOT NULL + LIMIT` 的明细 SQL，应允许回答。
- 兼容用例：未经最终 SQL repair 的同形态查询仍按诊断 SQL 处理。
- 回归 ChatBI runner 的诊断、空结果与最终回答门禁用例。

