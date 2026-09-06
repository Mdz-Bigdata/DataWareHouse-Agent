## 1. 后端批量删除 API 接口设计与实现

- [x] 1.1 在后端聊天服务层编写批量删除会话的核心业务逻辑，能够一次性接受由多个会话 ID（`conversation_id`）组成的数组，并在单个数据库 Transaction 事务内级联物理删除消息历史、Trace 链路以及对应的步骤日志。
- [x] 1.2 新增后端 API 路由 `POST /api/v1/chat/history/batch-delete`，关联批量删除核心处理函数，确保其对 token 或 API-Key 校验流程的向下兼容。
- [ ] 1.3 本地对新增接口进行 API 验证，确保在传入非空 UUID 数组时级联清理正常，且事务能够在发生故障时完全回滚。

## 2. 前端父组件 EmbedChat.vue 分拣与事件对接

- [x] 2.1 修改 `EmbedChat.vue`，实现 `groupedHistoryList` 响应式计算属性。此属性读取扁平的去重历史列表并归类至“今天”、“昨天”、“3天前”、“7天前”、“更早”五个子对象数组中。
- [x] 2.2 更新 `<ChatHistorySidebar>` 挂载声明，将绑定的 `:history-list` 属性变更为新的计算属性 `groupedHistoryList`。
- [x] 2.3 在 `EmbedChat.vue` 中编写批量删除事件处理函数 `handleDeleteGroup`：向后端 `batch-delete` 接口发起 POST 请求，如果成功则重新拉取刷新历史会话列表。
- [x] 2.4 在 `handleDeleteGroup` 删除逻辑中，检查被删除的会话 ID 列表中是否包含当前的 `conversationId.value`，若是则立即触发 `generateNewConversation()` 清空当前会话界面并生成全新会话。
- [x] 2.5 绑定 `<ChatHistorySidebar>` 的 `@delete-group` 事件到前端新实现的 `handleDeleteGroup` 处理函数上。

## 3. 前端侧边栏子组件 ChatHistorySidebar.vue UI 重构

- [x] 3.1 调整 `ChatHistorySidebar.vue` 组件的 `defineProps` 声明，使 `:history-list` 兼容新的分组对象数组结构。
- [x] 3.2 调整 `defineEmits` 声明，新增 `(e: "delete-group", group: any): void` 事件，以便向父组件反馈删除指令。
- [x] 3.3 重构侧边栏模板，使用双重 `v-for` 循环：外循环渲染时间分组 Header（呈现分组标签如“今天”以及批量删除图标按钮），内循环渲染对应组内所有的具体会话卡片。
- [x] 3.4 调整侧边栏中的分页逻辑、触底监听逻辑（`handleScroll`）与搜索过滤逻辑，确保其能完全兼容多维的分组数据结构并顺畅运作。

## 4. 全链路调优与自动化测试更新

- [x] 4.1 在本地环境中编译前端并前台启动 `./dev.sh` 服务。
- [x] 4.2 进行功能验证：创建测试对话，跨越多天验证时间分组渲染的直观度；点击分组 Header 上的删除按钮，验证一键删除及自动重新生成全新会话的交互。
- [x] 4.3 检查是否需要更新 `tests/` 目录下的自动化测试清单，更新 `tests/CHECKLIST.md` 以涵盖该项历史会话日期分组及一键删除新功能的测试条件。
