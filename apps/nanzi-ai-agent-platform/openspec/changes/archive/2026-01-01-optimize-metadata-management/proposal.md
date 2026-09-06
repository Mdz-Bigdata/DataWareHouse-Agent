# Proposal: 优化元数据管理 (Optimize Metadata Management)

## 目标 (Goal)
通过实现缺失的**指标 (Metrics)** 和 **关系 (Relationships)** 的后端服务、API 和前端管理界面，并升级 **AI 上下文导出 (AI Context Export)** 功能以包含这些新数据，从而完善元数据管理能力，提升 AI 智能体的语义理解和查询能力。

## 动机 (Motivation)
当前的元数据管理仅限于数据集 (Datasets)、表 (Tables) 和列 (Columns)。关键的语义信息——特别是**指标**（计算业务逻辑）和**关系**（表之间如何关联）——虽然在数据库模型中存在，但无法通过 API 或 UI 访问。这导致：
1.  **AI 上下文不完整 (Incomplete AI Context)**：AI 智能体缺乏业务指标和表关联的知识，难以生成用于复杂查询的准确 SQL。
2.  **管理缺失 (Management Gaps)**：用户无法定义或管理这些核心语义元素。

## 范围 (Scope)
1.  **指标管理 (Metrics Management)**：
    *   后端：为 `MetaMetric` 实现 CRUD 服务和 API 接口。
    *   前端：在 `Metadata` 模块中创建“指标”管理 UI。
2.  **关系管理 (Relationships Management)**：
    *   后端：为 `MetaRelationship` 实现 CRUD 服务和 API 接口。
    *   前端：创建“关系”管理 UI（初步支持列表/表单形式）。
3.  **智能导入增强 (Smart Import Enhancements)**：
    *   升级 `MetadataGeneratorService`，使其能从 SQL DDL（外键）、注释或自然语言需求文档中自动提取指标和关系定义。
    *   更新前端导入向导，支持预览和确认自动提取的指标与关系。
4.  **上下文增强 (Context Enhancement)**：
    *   更新 `MetadataService.export_dataset_yaml`，将指标和关系数据序列化为 AI 智能体可消费的 YAML 格式。

## 成功标准 (Success Criteria)
1.  用户可以通过控制台 Dashboard 创建、更新和删除指标及关系。
2.  导出的数据集 YAML 上下文包含所有定义的指标和关系。
3.  AI 智能体（通过模拟器验证）能够成功检索并利用这一增强的上下文信息。
