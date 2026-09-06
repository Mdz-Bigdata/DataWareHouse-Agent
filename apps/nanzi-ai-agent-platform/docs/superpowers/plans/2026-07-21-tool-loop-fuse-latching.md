# 工具循环熔断持续生效 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 修复熔断后工具重新放行的问题，并向模型返回明确的停止调用与收敛回答指令。

**Architecture:** `ToolLoopDetector` 负责持续维护并返回熔断状态；AgentScope 工具包装器只消费 verdict，通过共享辅助函数构造一致的模型纠正消息。测试覆盖 detector 契约和真实包装器调用边界。

**Tech Stack:** Python 3、pytest、AgentScope RuntimeToolSpec

---

### Task 1: 固化持续熔断契约

**Files:**
- Modify: `tests/ai/runtime/test_tool_loop_detector.py`
- Modify: `app/services/ai/runtime/tool_loop_detector.py`

- [x] **Step 1: 修改现有测试，要求熔断后的 verdict 继续保持 fused**

将 `test_fused_detector_stays_fused` 的后续断言改为 `follow.fused is True`，并校验首次原因、原因码和累计次数被保留。

- [x] **Step 2: 运行单测并确认 RED**

Run: `venv/bin/python -m pytest tests/ai/runtime/test_tool_loop_detector.py::test_fused_detector_stays_fused -q`

Expected: FAIL，旧实现返回 `fused=False`。

- [x] **Step 3: 最小修改 record 短路逻辑**

禁用或空工具名保持非熔断；已熔断时返回保存的熔断 verdict。

- [x] **Step 4: 运行 detector 测试并确认 GREEN**

Run: `venv/bin/python -m pytest tests/ai/runtime/test_tool_loop_detector.py -q`

Expected: PASS。

### Task 2: 向模型提供收敛指令

**Files:**
- Modify: `tests/ai/runtime/test_agentscope_tooling.py`
- Modify: `app/services/ai/runtime/agentscope/tools.py`

- [x] **Step 1: 新增包装器回归测试**

构造阈值为 3 的真实 `AgentScopeRuntimeTool`，断言底层 callable 只执行两次，第 3、4 次均抛出 `ToolLoopFuseError`；错误消息包含“停止继续调用任何工具”和“信息不足”。

- [x] **Step 2: 运行新测试并确认 RED**

Run: `venv/bin/python -m pytest tests/ai/runtime/test_agentscope_tooling.py::test_agentscope_tool_loop_fuse_remains_blocking_and_guides_model -q`

Expected: FAIL，旧实现第 4 次重新执行。

- [x] **Step 3: 增加共享熔断错误消息并供两个包装器使用**

消息保留 detector 原因，并追加：停止继续调用任何工具；基于已有结果直接回答；信息不足时明确说明限制。

- [x] **Step 4: 运行包装器测试并确认 GREEN**

Run: `venv/bin/python -m pytest tests/ai/runtime/test_agentscope_tooling.py -q`

Expected: PASS。

### Task 3: 回归验证

**Files:**
- Verify: `app/services/ai/runtime/tool_loop_detector.py`
- Verify: `app/services/ai/runtime/agentscope/tools.py`

- [x] **Step 1: 运行相关测试切片**

Run: `venv/bin/python -m pytest tests/ai/runtime/test_tool_loop_detector.py tests/ai/runtime/test_agentscope_tooling.py tests/ai/runners/test_assistant_agent_grounding_gate.py -q`

Expected: PASS。

- [x] **Step 2: 检查补丁格式和工作区状态**

Run: `git diff --check`

Expected: 无输出且退出码为 0；不暂存、不提交。
