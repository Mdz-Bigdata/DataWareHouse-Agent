# Implementation Tasks

## 1. Backend: Metadata Service
- [ ] 修改 `app/services/metadata_service.py` 中的 `export_dataset_yaml` 方法，增加 `data_source` 字段导出逻辑（含默认值兜底）。 <!-- id: be-yaml-export -->

## 2. Backend: Tool Logic
- [ ] 修改 `app/services/ai/tools/data_api.py`，更新 `validate_sql` 函数以支持 `dialect` 参数。 <!-- id: be-tool-validate -->
- [ ] 修改 `app/services/ai/tools/data_api.py`，更新 `execute_sql_query` 工具签名，支持 `data_source` 参数并实现路由逻辑。 <!-- id: be-tool-exec -->

## 3. Frontend: Metadata UI
- [ ] 修改 `frontend/src/views/MetadataDatasets.vue`，在新建和编辑模态框中增加 `Data Source ID` 输入框。 <!-- id: fe-dataset-ui -->

## 4. System Prompt
- [ ] 创建 `architech/prompts/system_agents/chatbi/V5_chatbi_optimized.md`，基于 V4 版本增加数据源路由指令。 <!-- id: prompt-v5 -->
- [ ] (Optional) 更新数据库中的 Agent 配置以应用新 Prompt (视部署策略而定，可暂手动更新)。

## 5. Validation
- [ ] 验证 MySQL 数据源的 SQL 生成与执行是否成功。 <!-- id: test-mysql -->
- [ ] 验证 ClickHouse 数据源的兼容性是否受影响。 <!-- id: test-ck -->
