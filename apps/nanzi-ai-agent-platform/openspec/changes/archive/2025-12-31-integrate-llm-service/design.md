# 设计：基于 LangChain 的智能体编排底座

## 核心架构：LangChain 驱动层

系统将 AI 逻辑抽象为不同的 **Chains** (链)，并通过 **LCEL** 进行声明式组合。

### 核心架构组件
1. **Model Layer (LangChain ChatModels)**:
   - 封装内部 Gateway 为标准的 `ChatOpenAI` 接口，支持 Streaming 和非 Streaming 模式。
2. **Chain Layer (LCEL Workflow)**:
   - **`IntentChain`**: 用户输入 -> Prompt -> LLM -> PydanticParser。
   - 实现自动化的 Prompt 格式控制和输出验证。
3. **Tool Layer (Toolkits)**:
   - 将现有数据服务 API 封装为 `LangChain Tools`，允许 Agent 未来自主决定何时调用 API。
4. **Memory Layer (Conversation Persistence)**:
   - 利用 LangChain 的 `ChatMessageHistory` 结合 Redis 实现多轮会话持久化。

### 实施细节
- **库版本**：`langchain`, `langchain-openai`, `langchain-community`。
- **解析器**：首选 `PydanticOutputParser` 以确保 Python 类型的强校验。
- **异步流**：全链路采用 `astream` 和 `ainvoke`，完美契合 FastAPI 异步特性。

### 意图识别流 (LangChain 实现)
```python
# 概念示例
chain = prompt | model.with_structured_output(IntentSchema)
result = await chain.ainvoke({"input": user_query})
```
1. 构建声明式 Prompt。
2. 绑定结构化输出 (Structured Output)。
3. 异步执行并自动完成 JSON -> Pydantic 的解析。
