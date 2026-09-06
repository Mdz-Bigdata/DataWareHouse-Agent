# 元数据查询 API 使用指南

## 概述

该接口用于获取数据库的元数据（Schema）信息。
目前作为一个转发层（Proxy），未来将集成 RAGFlow 接口，通过语义检索获取相关的表结构信息。

## API 端点信息

- **路径**: `/v1/schema`
- **方法**: `POST`
- **功能**: 获取指定数据源或表的结构描述 (DDL/Schema)
- **认证**: 需要 API Key
- **权限**: `system.schema.read` (暂定)

## 请求结构

### 请求体参数

| 参数名 | 类型 | 必需 | 描述 |
| :--- | :--- | :--- | :--- |
| `query` | string | 否 | 自然语言问题或关键词。Agent 将其发给接口，接口去知识库(RAG)或本地元数据中检索最相关的表结构描述。 |

### 示例

```json
{
  "query": "如何计算PUE?"
}
```

## 响应结构

### 成功响应 (200)

接口返回的是非结构化的文本段落（Markdown格式），Agent 需要阅读这些文本来理解数据库结构。

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "schema_context": "### 检索到的相关表结构说明\n\n1. **metrics 表**：\n   - 用于存储所有时序指标...\n   - 字段 `value` 代表具体的数值...\n\n2. **业务规则**：\n   - PUE = 总能耗 / IT设备能耗..."
  }
}
```

## 处理逻辑 (Current & Future)

1. **当前阶段 (Phase 1)**:
   - 接收请求。
   - 内部调用 `call_external_sql_api("DESCRIBE {table}")` 或返回预设 Schema。
   - 返回格式化的 Schema 文本。

2. **未来阶段 (Phase 2 - RAGFlow)**:
   - 接收 `query`。
   - 调用 RAGFlow 检索接口，查找与 Query 相关的表结构文档。
   - 返回 RAG 检索到的 Context。
