# Metadata to RAGFlow Integration (元数据发布至 RAGFlow)

## 1. Background (背景)
当前系统中，元数据主要用于 Text-to-SQL。
用户希望将这些元数据同步到 RAGFlow 知识库，利用 RAGFlow 的向量检索能力来辅助 AI 更好地定位表结构，从而生成 SQL。

## 2. Goals (目标)
- 实现从本地元数据到 RAGFlow 知识库的 **1:1 同步**。
- 确保 RAGFlow 中的 Chunk 内容包含完整的 **Schema 定义**，以便 AI 能据此生成 SQL。
- 在 UI 上直观展示同步状态。

## 3. Key Decisions (关键决策)

1.  **1:1 映射策略**:
    - 本地 `MetaDataset` 对应 RAGFlow 中的一个独立的 `Dataset` (KB)。
    - KB 命名规范：`meta-{本地数据集名称}`。

2.  **Chunk 内容策略 (SQL-Optimized Markdown)**:
    - 采用 **Markdown** 格式。
    - **分块粒度**: 一表一文件 (One Table per File)。
    - **内容结构**:
        1.  **Header**: 表名与业务描述 (用于语义检索)。
        2.  **Schema Table**: Markdown 表格形式列出 `Column | Type | Comment | Enum` (用于 SQL 生成)。
        3.  **Metrics/Samples**: 相关的指标计算逻辑转换为 SQL 示例 (Few-Shot)。

3.  **同步策略**:
    - **触发**: 用户手动点击“发布/同步”。
    - **覆盖**: 采用“先删后加”策略，确保无旧版本残留。
    - **关联**: 本地 DB 记录 `rag_dataset_id` 以便后续查询路由。

4.  **UI 表现**:
    - 在数据集卡片上通过徽章 (Badge) 展示同步状态 (已同步/同步中/失败)。
    - 详情页展示“上次同步时间”。

## 4. Detailed Design (详细设计)

### 4.1 Database Changes
在 `meta_datasets` 表中添加：
```sql
ALTER TABLE meta_datasets ADD COLUMN rag_dataset_id VARCHAR(64) COMMENT 'RAGFlow Dataset ID';
ALTER TABLE meta_datasets ADD COLUMN rag_synced_at DATETIME COMMENT 'Last Sync Time';
ALTER TABLE meta_datasets ADD COLUMN rag_sync_status INT DEFAULT 0 COMMENT '0:None, 1:Syncing, 2:Synced, -1:Failed';
```

### 4.2 Backend Logic (`MetadataRagService`)

**Markdown 模板示例**:
```markdown
# Table: orders (订单表)
> Description: 存储用户下单交易记录。

## Columns
| Name | Type | Comment |
| :--- | :--- | :--- |
| order_id | String | 主键 |
| status | Int8 | 0:未支付, 1:已支付 |

## Sample Queries
Question: 统计销售额
SQL: SELECT SUM(amt) FROM orders WHERE status=1
```

**同步流程**:
1.  **Init**: 检查/创建 RAGFlow Dataset。
2.  **Gen**: 生成上述格式的 Markdown 文件。
3.  **Upload**: 调用 RAGFlow API 上传并触发 Parse。
4.  **Update DB**: 更新 `rag_sync_status` 和 `rag_synced_at`。

### 4.3 API Endpoints
- `POST /api/portal/datasets/{id}/rag/sync`: 触发同步。
- `GET /api/portal/datasets/{id}`: 响应体中包含 rag 状态字段。

## 5. Timeline (计划)
- Phase 1: DB 变更 & RAGFlow Client 升级 (CRUD)。
- Phase 2: Markdown 生成与同步逻辑实现。
- Phase 3: UI 状态展示与交互对接。
