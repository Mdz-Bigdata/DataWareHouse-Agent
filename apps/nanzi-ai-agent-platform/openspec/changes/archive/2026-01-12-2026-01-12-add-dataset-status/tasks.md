# Implementation Tasks

## 1. Database
- [ ] 创建并执行 SQL 迁移脚本 `db-prod/V25-add_dataset_status.sql`。 <!-- id: db-migration -->

## 2. Backend
- [ ] 修改 `app/models/metadata.py`，在 `MetaDataset` 模型中增加 `status` 字段。 <!-- id: be-model -->
- [ ] 修改 `app/schemas/metadata.py`，在 Pydantic Schema 中增加 `status` 字段。 <!-- id: be-schema -->
- [ ] 修改 `app/services/metadata_service.py`，在 `search_datasets` 方法中增加 `status=1` 的过滤条件。 <!-- id: be-service-search -->

## 3. Frontend
- [ ] 修改 `frontend/src/views/MetadataDatasets.vue`，在数据集卡片上增加状态切换开关（Pill UI），并绑定更新逻辑。 <!-- id: fe-ui -->
