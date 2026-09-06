# ChatBI 反馈驱动优化系统 - 技术设计方案

> 版本：v1.0  
> 日期：2026-03-18  
> 状态：设计草案

---

## 一、背景与目标

### 问题

ChatBI 的核心能力是将用户的自然语言问题，动态生成 SQL 并执行返回结果，最终合成自然语言回答。然而：

- 相同或相似的业务问题，每次都从零开始生成 SQL，无法利用历史正确案例
- AI 生成的 SQL 存在偶发性错误，但系统无法从用户反馈中学习
- 缺乏正向激励机制：好的 SQL 模板无法被沉淀和复用

### 目标

构建一套**基于用户点赞/踩反馈驱动的 ChatBI 持续优化系统**，核心能力：

1. **经验沉淀**：将点赞的优质问答对（含 SQL）存入「经验库」
2. **智能检索**：新问题到来时，从经验库语义检索相似案例
3. **Few-Shot 注入**：将匹配的历史案例作为示例注入 Prompt，引导 AI 生成更准确的 SQL
4. **负样本标记**：将踩的案例标记，避免被重复引用

---

## 二、整体架构

```
用户提问
   │
   ▼
ChatBI DataQueryExecutor
   │
   ▼
经验库检索（ai_chatbi_examples）
   ├─── 相似命中 ───▶ Few-Shot 注入 Prompt ─┐
   └─── 无命中  ───▶ 纯 Schema 上下文    ─┤
                                            │
                                            ▼
                                     LLM 生成 SQL
                                            │
                                            ▼
                                        执行 SQL
                                            │
                                            ▼
                                  生成回答 → 流式输出
                                            │
                                            ▼
                                   用户评价：点赞 / 踩
                                    │              │
                                    ▼              ▼
                              提取 SQL         标记负样本
                              存入经验库         / 降权
                                    │
                                    └──────────────▶ 经验库（下次检索可用）
```

---

## 三、核心流程详解

### 3.1 反馈收集流程

```
前端 EmbedChat
   │  POST { message_id, conversation_id, trace_id, feedback: "up"/"down" }
   ▼
/api/chat/feedback
   │
   ├──▶ ai_agent_execution_history
   │         查询 trace_id → 获取 query（用户问题）+ summary（AI 回答）
   │
   ├──▶ ai_agent_execution_traces
   │         查询条件：
   │           tool_name = "execute_sql_query"
   │           status    = "success"          ← 只取成功的
   │         排序：step_number DESC，取最后一条（最终成功的 SQL）
   │
   └──▶ ai_chatbi_examples
             写入新记录（user_query + sql_text + ai_answer + feedback_type）
   │
   ▼
返回 200 OK → 前端显示反馈已收到
```

**关键逻辑 - ReAct 多次重试的处理**：

ChatBI 的 ReAct 循环在遇到 SQL 错误时会自动重试（自愈机制），一次对话中同一 trace_id 下可能存在多条 `execute_sql_query` 的 trace 记录，其中部分是失败的中间步骤。

提取规则：
```
SELECT tool_input
FROM   ai_agent_execution_traces
WHERE  trace_id  = :trace_id
  AND  tool_name = 'execute_sql_query'
  AND  status    = 'success'       -- 排除所有报错的 SQL 记录
ORDER  BY step_number DESC         -- 取步骤最大的（最终采纳的那条）
LIMIT  1
```

若查询结果为空（整个 trace 没有一条 SQL 执行成功），则**拒绝写入经验库**，并返回友好提示。

---

### 3.2 经验库检索与注入流程

```
DataQueryExecutor（收到用户问题）
   │
   │  search_examples(query=用户问题, dataset_id=xxx)
   ▼
ExampleService（新增）
   │
   │  查询 ragflow_dataset_id（经验库对应的 RAGFlow Dataset ID）
   ▼
RagFlowClient.retrieve(query, dataset_ids=[ragflow_dataset_id])
   │
   │  RAGFlow 内部向量检索 + 全文检索 + 相似度排序
   ▼
返回 Top-K chunks（每个 chunk = 一条经验 + 其 SQL）
   ▼
DataQueryExecutor  ── 构建 Few-Shot Prompt Block
   │
   │  System Prompt = Schema 上下文 + Few-Shot 示例块
   ▼
大语言模型 → 生成更准确的 SQL
```

---

## 四、数据表设计

### 4.1 主表：经验库 `ai_chatbi_examples`

```sql
CREATE TABLE ai_chatbi_examples (
    id             BIGINT       AUTO_INCREMENT PRIMARY KEY,

    -- 关联现有系统
    trace_id       VARCHAR(64)  NOT NULL COMMENT '关联 ai_agent_execution_history.trace_id',
    agent_id       VARCHAR(36)  NOT NULL COMMENT '来自哪个 Agent',
    dataset_id     INT          NOT NULL COMMENT '主要数据集 ID（关联 metadata_datasets.id）',
    dataset_ids    JSON         NULL      COMMENT '查询涉及的所有数据集 IDs',

    -- 核心内容
    user_query     TEXT         NOT NULL COMMENT '用户原始问题（自然语言）',
    sql_text       TEXT         NOT NULL COMMENT '被验证为正确的 SQL 语句',
    ai_answer      TEXT         NULL      COMMENT 'AI 最终的自然语言回答摘要（≤500字）',

    -- 质量与审核状态
    feedback_type  VARCHAR(10)  NOT NULL DEFAULT 'up' COMMENT '反馈类型: up=点赞, down=踩',
    status         VARCHAR(20)  NOT NULL DEFAULT 'approved'
                                COMMENT 'pending=待审核, approved=可用, rejected=已驳回, deprecated=废弃',
    use_count      INT          NOT NULL DEFAULT 0  COMMENT '被引用次数（Few-Shot 注入次数）',
    hit_count      INT          NOT NULL DEFAULT 0  COMMENT '被 RAGFlow 检索命中次数',

    -- RAGFlow 同步状态（核心新增）
    rag_dataset_id VARCHAR(64)  NULL COMMENT 'RAGFlow Dataset ID（冗余存储，来源于系统配置 agent_sample_knowledge）',
    rag_doc_id     VARCHAR(64)  NULL COMMENT '该条经验在 RAGFlow 中的 Document ID',
    rag_sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                                COMMENT 'pending=待同步, synced=已同步, failed=同步失败, removed=已删除',
    rag_sync_error TEXT         NULL COMMENT 'RAGFlow 同步失败时的错误信息',
    rag_synced_at  DATETIME     NULL COMMENT '最后一次同步成功时间',

    -- 用户信息
    user_id        VARCHAR(64)  NULL      COMMENT '点赞用户 ID',
    user_name      VARCHAR(64)  NULL      COMMENT '点赞用户名',

    -- 时间
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_agent_dataset  (agent_id, dataset_id),
    INDEX idx_trace          (trace_id),
    INDEX idx_status         (status),
    INDEX idx_rag_sync       (rag_sync_status),
    INDEX idx_created        (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ChatBI 经验库：存储点赞验证的优质问答对及其 SQL，同步到 RAGFlow 做向量检索';
```

---

### 4.2 关联表：经验库使用记录 `ai_chatbi_example_usages`

记录每次经验库案例被实际引用到 Prompt 的情况，用于分析哪些案例有效。

```sql
CREATE TABLE ai_chatbi_example_usages (
    id            BIGINT      AUTO_INCREMENT PRIMARY KEY,
    example_id    BIGINT      NOT NULL COMMENT '关联 ai_chatbi_examples.id',
    trace_id      VARCHAR(64) NOT NULL COMMENT '本次对话的 trace_id',
    similarity    FLOAT       NULL      COMMENT '检索时的相似度得分（0~1）',
    created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_example (example_id),
    INDEX idx_trace   (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='ChatBI 经验引用记录';
```

---

## 五、核心组件设计

### 5.1 新增后端服务 `ExampleService`

**文件位置**：`app/services/chatbi_example_service.py`

**核心方法**：

```
ExampleService
├── create_from_feedback(trace_id, feedback_type, user_id)
│       → 从 trace 中提取最终成功的 SQL，写入数据库，触发异步 RAGFlow 同步
│
├── sync_to_ragflow(example_id)
│       → 将经验格式化为 Markdown，上传到 RAGFlow Dataset，触发惟一化
│       → 写回 rag_doc_id + rag_sync_status='synced'
│
├── search_examples(query, top_k=3)
│       → 调用 RagFlowClient.retrieve() 向量检索，返回 Top-K chunks
│
├── build_few_shot_prompt(chunks)
│       → 将 RAGFlow 返回的 chunks 格式化为 Few-Shot Prompt 块
│
├── deprecate_example(example_id)
│       → 将记录标记为 deprecated，并同步删除 RAGFlow 中的 Document
│
└── mark_used(example_ids, trace_id)
        → 记录本次引用，更新 hit_count
```

### 5.2 RAGFlow 经验库数据集管理

全局建立**一个独立的 RAGFlow Dataset** 作为 ChatBI 经验库（类比元数据知识库），命名为：

```
chatbi-examples-knowledge-base
```

> 参考现有元数据同步机制：元数据库里**每张表**对应 RAGFlow 里的一个 Document/Chunk；  
> 经验库同理：**每条 `ai_chatbi_examples` 记录**对应 RAGFlow 里的**一个独立 Document**（1 条记录 = 1 个 chunk）。

`rag_doc_id` 字段记录每条经验对应的 Document ID。全局经验库的 RAGFlow Dataset ID 存储在**系统配置**中，key 为 `agent_sample_knowledge`，通过 `ConfigService.get('agent_sample_knowledge')` 读取，无需硬编码。

每条经验上传到 RAGFlow 时，内容格式化为 Markdown：

```markdown
## 用户问题
上个月各区域的销售额是多少？

## 验证通过的 SQL
SELECT region, SUM(amount) AS total_sales
FROM t_orders
WHERE toStartOfMonth(order_date) = toStartOfMonth(now() - INTERVAL 1 MONTH)
GROUP BY region

## AI 回答摘要
上月全国共产生销售额 ¥1.2 亿，华东区最高达 ¥4500 万...
```

RAGFlow 解析后生成 1 个 chunk，向量化存入知识库，通过 `retrieve()` 做语义检索。

搜索时，按 `agent_id` 在知识库搜索时可在 chunk content 中加入 `agent_id` 标签用于过滤，或直接全局检索（经验库规模通常较小）。

**同步触发时机**：

| 事件 | 动作 |
|------|------|
| 用户点赞（`approved` 状态） | 异步上传 Document → 触发 `parse_documents()` 向量化 → 写回 `rag_doc_id` |
| 管理员驳回 / 废弃 | 调用 `delete_documents(rag_doc_id)` 从 RAGFlow 删除 → `rag_sync_status='removed'` |
| 同步失败 | 更新 `rag_sync_status='failed'` + `rag_sync_error`，支持手动重试 |

---

### 5.3 新增 API 接口

#### POST `/api/chat/feedback`

收集用户对 AI 回答的反馈。

```json
// Request Body
{
  "trace_id": "abc-123",
  "conversation_id": "conv-456",
  "message_id": "msg-789",
  "feedback": "up"   // "up" | "down"
}

// Response
{
  "code": 200,
  "message": "感谢您的反馈！",
  "data": { "example_id": 42 }  // 仅 feedback=up 时返回
}
```

#### GET `/api/admin/chatbi/examples`

管理员审核经验库（查询列表）。

```
Query Params:
  status: pending | approved | rejected
  agent_id: string
  dataset_id: int
  page: int
  size: int
```

#### PATCH `/api/admin/chatbi/examples/{id}`

审核操作：批准 / 驳回 / 废弃。

```json
// Request Body
{ "status": "approved" }
```

---

### 5.4 DataQueryExecutor 改造

**文件**：`app/services/ai/executors/data_executor.py`

在 `_build_system_prompt()` 方法中，增加经验库检索与 Few-Shot 注入逻辑：

```
原始 System Prompt 构建流程：
  Schema Context（元数据 YAML）
  ↓
  [新增] ExampleService.search_examples()
  ↓
  [新增] Few-Shot Block 注入
  ↓
  最终 System Prompt → LLM 推理
```

**Few-Shot Prompt 格式**：

```
## 历史优质案例参考（已被用户验证的查询）

### 案例 1
用户问题：上个月各区域的销售额是多少？
参考 SQL：
  SELECT region, SUM(amount) AS total_sales
  FROM t_orders
  WHERE toStartOfMonth(order_date) = toStartOfMonth(now() - INTERVAL 1 MONTH)
  GROUP BY region

### 案例 2
...

---
请优先参考以上案例的表达方式和 SQL 风格，但需根据当前问题进行适当调整。
```

---

## 六、状态机与审核流程

```
                   用户点赞（自动创建）
                          │
                          ▼
                       pending
                      ╱       ╲
        管理员审核通过╱         ╲管理员驳回
                    ╱           ╲
                   ▼             ▼
                approved       rejected ──▶ 结束
                   │
    数据结构变更后手动/自动废弃
                   │
                   ▼
                deprecated ──▶ 结束
```

**策略说明**：

- 初期设置为**自动审核通过**（`status = 'approved'`，跳过 `pending`），降低运营成本
- 待经验库积累一定规模后，可切换为人工审核流程
- 同一 trace_id 只能创建一条 example，防止重复点赞刷库
- 踩（`down`）反馈：若该 trace_id 已有 `approved` 案例，自动将其标记为 `deprecated`

---

## 七、防止"经验污染"的机制

| 风险 | 防护措施 |
|------|---------|
| ReAct 循环中的失败 SQL 被收录 | 提取时加 `status='success'` 过滤 + `ORDER BY step_number DESC LIMIT 1`，只取最终成功的那条 SQL |
| 同一用户刷赞 | `trace_id` 唯一索引，重复点赞覆盖而非新增 |
| 元数据变更导致旧 SQL 失效 | 数据集表结构变更时触发 `deprecated` 钩子，清理关联案例 |
| Few-Shot 污染 Prompt | Top-K 限制为 3 条，且相似度阈值 > 0.75 才注入 |
| 私有数据泄露 | 检索时强制按 `dataset_id` 过滤，确保 agent 只能看到其授权数据集的案例 |

---

## 八、实施路线图

### 第一期（MVP）

| 任务 | 说明 |
|------|------|
| 创建数据库表 | `ai_chatbi_examples` + `ai_chatbi_example_usages` |
| 实现 `POST /api/chat/feedback` | 收集点赞，提取 SQL，写入经验库（自动 approved） |
| 前端打通 feedback API | EmbedChat 点赞/踩时调用接口（当前仅 `postMessageToHost`） |
| `DataQueryExecutor` 注入 Few-Shot | 检索 + 注入 Prompt（BM25 关键词匹配，暂不做向量化） |

### 第二期（智能化）

| 任务 | 说明 |
|------|------|
| 向量化检索 | 写入时生成 embedding，检索时余弦相似度 Top-K |
| 管理审核界面 | 管理员可查看/驳回/废弃经验库条目 |
| 自动废弃钩子 | 元数据更新时自动检查关联的 example |
| 经验库分析看板 | 哪些问题最常被问、命中率、使用率 |

### 第三期（精细化）

| 任务 | 说明 |
|------|------|
| 自动化测试回归 | 对经验库中的 SQL 定期执行，检测是否仍然可用 |
| User-Level 个性化 | 同一问题不同用户有不同偏好时，支持 user 级别权重 |
| 经验导出/导入 | 支持跨环境迁移经验库 |

---

## 九、关键文件变更清单

| 类型 | 文件路径 | 说明 |
|------|---------|------|
| 【新建】数据表 SQL | `db-prod/V40-create_chatbi_examples.sql` | 经验库表 DDL |
| 【新建】数据模型 | `app/models/chatbi_example.py` | SQLAlchemy ORM Model |
| 【新建】服务层 | `app/services/chatbi_example_service.py` | 核心业务逻辑 |
| 【新建】API 端点 | `app/api/portal/endpoints/chat_feedback.py` | 收集反馈接口 |
| 【新建】管理 API | `app/api/portal/endpoints/chatbi_admin.py` | 审核管理接口 |
| 【修改】执行器 | `app/services/ai/executors/data_executor.py` | 注入 Few-Shot |
| 【修改】前端 | `frontend/src/views/EmbedChat.vue` | 打通 feedback API |
| 【修改】权限注册 | `db-prod/V40-register_feedback_resources.sql` | 注册管理页面权限 |
