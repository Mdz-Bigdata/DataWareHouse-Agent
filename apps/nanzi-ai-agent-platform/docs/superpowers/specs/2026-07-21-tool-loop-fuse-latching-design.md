# 工具循环熔断持续生效修复设计

## 背景

`ToolLoopDetector` 达到重复、工具交替或全局调用阈值后会把内部状态设置为 `fused=True`。当前 `record()` 在后续调用中却返回 `fused=False`，导致 AgentScope 将首次 `ToolLoopFuseError` 转成普通工具错误后，下一次工具调用重新获得执行机会。

## 目标

- 熔断器一旦在本轮进入 `fused` 状态，本轮后续所有工具调用均持续拒绝。
- 后续拒绝复用首次熔断的原因、原因码及有意义的调用计数。
- 返回给模型的熔断错误包含明确收敛指令：停止调用工具，基于已有结果回答；信息不足时说明限制。
- 不改变未熔断、禁用熔断、阈值计算、工具权限和最大迭代配置。

## 设计

修改 `ToolLoopDetector.record()` 的短路逻辑：

1. `enabled=False` 或工具名为空时仍返回非熔断结果。
2. `self.fused=True` 时返回 `fused=True`，携带保存的 `fuse_reason`、`fuse_reason_code` 和当前累计调用数。
3. 正常状态继续执行现有 repeat、circuit breaker 和 ping-pong 判断。

工具包装器无需增加第二套状态判断；它继续以 `verdict.fused` 为唯一契约并抛出 `ToolLoopFuseError`。
异常消息由统一辅助函数追加模型纠正指令，两个 AgentScope 工具包装器共享同一文案。

## 测试

采用 TDD：

1. 先修改 detector 测试，断言熔断后的每次 `record()` 仍返回 `fused=True`，确认旧实现失败。
2. 新增 AgentScope 工具包装器回归测试：阈值为 3 时，底层工具只执行前两次；第 3 次及后续调用均抛出 `ToolLoopFuseError`。
3. 断言错误消息要求模型停止工具调用，并在信息不足时明确说明限制。
4. 运行工具循环、AgentScope tooling、通用智能体相关聚焦测试，并执行 `git diff --check`。

## 非目标

- 不调整线上系统配置。
- 不修改 `agent_max_iterations`。
- 不修改模型工具选择策略。
- 不启动或重启服务。
