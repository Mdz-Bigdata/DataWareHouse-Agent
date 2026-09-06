# Design: 元数据优化 (Metadata Optimization)

## 1. 后端架构 (Backend Architecture)

### 1.1 指标服务 (`MetaMetric`)
*   **Service Layer**: `MetadataService` 需要新增方法：
    *   `create_metric(dataset_id, metric_data)`
    *   `update_metric(metric_id, metric_data)`
    *   `delete_metric(metric_id)`
    *   `get_metrics_by_dataset(dataset_id)`
*   **API Layer**: `api/portal/endpoints/metadata.py` 新增端点：
    *   `POST /datasets/{id}/metrics`
    *   `PUT /metrics/{id}`
    *   `DELETE /metrics/{id}`
    *   `GET /datasets/{id}/metrics`

### 1.2 关系服务 (`MetaRelationship`)
*   **Service Layer**: `MetadataService` 需要新增方法：
    *   `create_relationship(dataset_id, rel_data)` -> *关系依赖于表，但在管理上按数据集分组。为简化起见，我们目前在数据集层级进行管理，以便呈现跨表关系。*
    *   `update_relationship(rel_id, rel_data)`
    *   `delete_relationship(rel_id)`
    *   `get_relationships_by_dataset(dataset_id)`
*   **API Layer**: 新增端点：
    *   `POST /datasets/{id}/relationships`
    *   `DELETE /relationships/{id}`
    *   `GET /datasets/{id}/relationships`

### 1.3 智能生成服务 (`MetadataGenerator`)
*   **LLM Prompt**: 更新 Prompt 以支持提取：
    *   从 DDL 外键约束提取 **关系 (Relationships)**。
    *   从 SQL 注释或 `CREATE VIEW` 语句提取 **计算指标 (Metrics)**。
    *   从自然语言需求文档中推断指标和实体关系。
*   **Output Schema**: 更新 Pydantic 模型以包含 `metrics: List[MetricMetadata]` 和 `relationships: List[RelationshipMetadata]`。

## 2. 前端架构 (Frontend Architecture)
*   **视图 (Views)**:
    *   更新 `MetadataDatasets.vue`，在“表 (Tables)”旁边增加“指标 (Metrics)”和“关系 (Relationships)”的 Tab 页签或区块。
    *   *决策：在数据集详情视图中放置 Tab 页，包含表、指标和关系三个子项。*
*   **组件 (Components)**:
    *   `MetricFormModal.vue`: 用于创建/编辑指标。
    *   `RelationshipFormModal.vue`: 用于定义关联 (Source Table, Target Table, Join Type, Condition)。
    *   `SmartImportWizard.vue`: 升级现有的导入弹窗，增加“解析结果预览”步骤，允许用户在保存前勾选/修改识别出的指标和关系。

## 3. 数据序列化 (YAML Export)
`export_dataset_yaml` 的输出格式需要扩展：
```yaml
dataset: ...
description: ...
tables:
  - name: ...
    columns: ...
# 新增部分 (New Sections)
metrics:
  - name: active_users
    display_name: 活跃用户
    description: ...
    calculation: count(distinct user_id) where status='active'
    unit: person
relationships:
  - source: users
    target: orders
    type: one_to_many
    condition: users.id = orders.user_id
```
