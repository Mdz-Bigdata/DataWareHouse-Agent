## MODIFIED Requirements

### Requirement: 智能体状态快速切换
> 智能体列表卡片必须 (MUST) 提供直观的开关控件来切换启用/禁用状态。

#### Scenario: 切换智能体状态
- **Given** 智能体管理列表页面。
- **Then** 每个卡片右上角（或显眼位置）展示一个 Toggle Switch。
- **When** 用户点击开启状态的 Switch。
- **Then** Switch 变为关闭状态。
- **And** 发送 API 请求禁用该智能体。

### Requirement: EmbedChat 预览入口
> 智能体卡片必须 (MUST) 提供直接预览 EmbedChat 的入口。

#### Scenario: 打开预览
- **Given** 智能体管理列表页面。
- **When** 用户点击卡片底部的“预览”按钮（原状态按钮位置）。
- **Then** 浏览器打开一个新标签页。
- **And** URL 指向 `/embed-chat`。
- **And** URL 包含参数 `?token={api_key}&agent_id={id}` 以自动登录并加载智能体。
