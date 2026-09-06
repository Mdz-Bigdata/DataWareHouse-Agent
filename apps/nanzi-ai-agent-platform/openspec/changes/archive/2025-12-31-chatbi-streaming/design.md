# 设计：ChatBI 工具链与 SSE 流式架构

## 1. ChatBI 设计：Tool-Calling 模式

我们将利用 LangChain 的 `bind_tools` 能力，将具体的业务查询封装为 Tool。

### 核心组件
1. **`DataToolkit`**:
   - 封装对机房数据的 HTTP 或 SQL 查询逻辑。
   - 定义标准的参数 Schema (如：机房名称、查询时间段)。
2. **`AnalyticAgent`**:
   - 具备 Tool 使用能力的 LangChain 链。
   - 逻辑：用户问句 -> 模型判断 -> 调用 Data API 工具 -> 获取结果 -> 总结陈述。

## 2. 流式输出设计：SSE (Server-Sent Events)

### 架构实现
- **后端**：使用 FastAPI 的 `StreamingResponse`。
- **逻辑流**：
  1. 调用 LangChain 的 `astream` 方法获取异步生成器。
  2. 将每个 Content Chunk 格式化为符合 SSE 标准的事件。
  3. 特殊标记：在流结束时发送特定的 `[DONE]` 信号。

### 对外接口变更
- `POST /api/v1/chat/completions` 增加参数 `stream: true`。
- 当 `stream: true` 时，Content-Type 切换为 `text/event-stream`。
