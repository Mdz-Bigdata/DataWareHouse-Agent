## 1. 后端实现 (Backend Implementation)
- [ ] 1.1 **API 定义**: 创建 `app/api/portal/prompts.py`，聚合来自 `system_configs` 和 `ai_agent_versions` 的 Prompt 数据。 <!-- type: backend -->
- [ ] 1.2 **业务逻辑**: 实现 `PromptService.get_all()` 和 `PromptService.update()`，处理不同存储源的读写逻辑。 <!-- type: backend -->
- [ ] 1.3 **调试接口**: 实现 `POST /api/portal/prompts/test`，支持传入动态变量并运行 LLM 推理。 <!-- type: backend -->

## 2. 前端实现 (Frontend Implementation)
- [ ] 2.1 **工作室视图**: 创建 `src/views/PromptStudio.vue`，实现 Prompt 列表侧边栏。 <!-- type: frontend -->
- [ ] 2.2 **编辑器组件**: 集成代码编辑器（Monaco 或带高亮的文本框），用于 Prompt 编辑。 <!-- type: frontend -->
- [ ] 2.3 **变量提取器**: 实现正则逻辑，自动识别 Prompt 中的 `{variables}` 并生成输入框。 <!-- type: frontend -->
- [ ] 2.4 **测试运行器**: 实现“运行”按钮逻辑，调用调试接口并展示 LLM 返回结果。 <!-- type: frontend -->

## 3. 集成与测试 (Integration & Testing)
- [ ] 3.1 **集成验证**: 确保 Studio 能正确更新数据库，且变更能即时反映在应用中。 <!-- type: integration -->
- [ ] 3.2 **单元测试**: 为 `PromptService` 的变量插值和检索逻辑添加单元测试。 <!-- type: test -->