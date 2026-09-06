# GroundingService.audit 统一审核设计

## 目标

把 Main、ChatBI 和 KnowledgeAgent 中重复的“调用策略、判断是否提示、生成统一提示内容”收口到 `GroundingService`，同时保持当前用户行为不变。

## 边界

`GroundingService.audit()` 只接收完整候选回答、事实要求和当前证据账本，返回审核结论与可选提示消息。它不消费异步流、不发送 SSE、不执行工具、不重试模型，也不负责 ChatBI SQL/Schema/权限门禁。

流式策略仍归 Runner：GENERAL 和 ChatBI 继续先流式输出正文、结束后审核并按需追加提示；需要缓冲的 Main 请求保持原缓冲方式；KnowledgeAgent 保留现有 NLI 反思循环，仅把最终提示生成交给统一服务。

## 接口

新增 `app/services/ai/grounding/service.py`：

- `GroundingAuditResult`：包含原始 `GroundingDecision` 和可选 `warning_chunk`；
- `GroundingService.audit(candidate_text, requirement, ledger)`：调用纯策略并统一生成提示；
- `GroundingService.warning_chunk(...)`：供 KnowledgeAgent 的独立 NLI 失败路径复用同一提示格式。

`policy.py` 继续只负责纯规则判断，`EvidenceLedger` 继续只负责证据签收和关联。

## 接入

- Main 的首次输出、GENERAL 结束审核、数据幻觉复核和恢复流全部改用 `GroundingService.audit()`；
- ChatBI 保留轻量适配器来构造当前结果的临时 `internal_data` 账本，但不再自行调用策略或生成提示；
- KnowledgeAgent 反思耗尽后通过 `GroundingService.warning_chunk()` 输出高风险提示。

## 兼容性与验收

- 不新增或恢复 `grounding_blocked`；
- 正文顺序、流式时机和风险提示位置不变；
- MCP 正确结果、空结果、错误结果和内容关联规则不变；
- 现有聚焦回归必须全部通过，并新增统一服务单元测试及 Runner 委托测试。
