# group-history-by-date Specification

## Purpose
TBD - created by archiving change group-history-by-date. Update Purpose after archive.
## Requirements
### Requirement: 会话历史按相对日期智能分组
系统必须（MUST）将从接口获取到的所有平铺历史会话，根据会话的创建时间与当前日期的相对间隔，自动归类并分拣到以下五个日期分组中展示：
- **今天**：创建时间在今天 00:00:00 之后。
- **昨天**：创建时间在昨天 00:00:00 至今天 00:00:00 之间。
- **3天前**：创建时间在昨天 00:00:00 往前推 2 天内。
- **7天前**：创建时间在昨天 00:00:00 往前推 3 至 6 天内。
- **更早**：创建时间在 7 天以前。

#### Scenario: 成功在侧边栏呈现日期分组列表
- **WHEN** 用户打开侧边栏或输入关键字搜索历史会话
- **THEN** 系统必须计算每个会话所属的时间跨度，将它们渲染在对应的日期分组 Header 下方；如果没有对应区间的会话，则该分组 Header 不予展示。

### Requirement: 日期分组 Header 一键批量删除
系统必须（MUST）在每个日期分组的 Header（标题行）右侧展示一个一键批量删除图标。用户点击并确认后，系统必须（MUST）提取该分组下的所有会话 ID 并发起后端批量删除请求。

#### Scenario: 成功一键删除特定分组下的所有会话
- **WHEN** 用户点击“3天前”分组 Header 上的删除按钮，并在弹出的确认框中点击同意
- **THEN** 系统必须通过批量删除 API 发送该组内所有的会话 ID；请求成功后，系统必须从前端侧边栏中移除整个“3天前”的分组，且如果当前视窗正处于被删除的会话中，必须自动清空聊天界面并生成新会话。

### Requirement: 会话历史批量删除后端接口 (API)
后端系统必须（MUST）提供一个批量删除接口 `/api/v1/chat/history/batch-delete`，该接口接受含有多个 `conversation_id` 的数组。后端必须（MUST）在单个数据库事务中原子性地清理对应会话及其关联的所有 Trace 日志、步骤日志和聊天消息。

#### Scenario: 成功调用后端批量删除会话
- **WHEN** 客户端以 POST 方式请求 `/api/v1/chat/history/batch-delete` 并传入 `{"conversation_ids": ["uuid-1", "uuid-2"]}`
- **THEN** 后端必须在数据库中执行原子化级联删除，成功后返回状态码 200 及 `status: "success"`，确保被删除的会话数据在侧边栏和任何其他查询中均不可见。

