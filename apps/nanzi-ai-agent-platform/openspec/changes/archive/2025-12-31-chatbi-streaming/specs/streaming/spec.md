# 规范：流式响应能力 (Streaming Response Capability)

## ADDED Requirements

### Requirement: SSE 协议兼容性 (SSE Compliance)
流式输出 **MUST** 符合标准的 W3C Server-Sent Events 协议格式。

#### Scenario: 逐字生成效果
前端通过 EventSource 或 Fetch 流接收到 `data: {"content": "..."}` 格式的消息块，并能实时在界面渲染。

### Requirement: 响应终止一致性 (Consistency of Termination)
流式传输结束时，**MUST** 发送明确的结束标识。

#### Scenario: 长文本截断防止
当回复较长时，前端应通过接收到的 `finish_reason` 或特殊 Chunk 判断流已完全结束，从而关闭连接。
