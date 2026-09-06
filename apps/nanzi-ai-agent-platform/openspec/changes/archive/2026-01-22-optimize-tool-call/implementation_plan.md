# Implementation Plan: 智能体版本编辑器升级

## 1. 目标 (Why)
用户反馈在编写智能体 System Prompt 时，纯文本框体验不佳，且弹框过窄，影响长提示词的编写效率。
为了提升体验，我们将：
1.  将 System Prompt 编辑框升级为 **Markdown 编辑器**，支持编辑和实时预览。
2.  **加宽** 编辑弹框，提供更宽敞的写作空间。

## 2. 详细计划 (How)

### 2.1 新增 MarkdownEditor 组件
创建 `frontend/src/components/MarkdownEditor.vue`：
- **功能**：
  - 接收 `modelValue` (v-model) 绑定内容。
  - 提供 **Tab 切换**：在“编辑 (Edit)”和“预览 (Preview)”模式间切换。
  - **编辑模式**：保持原有的 `textarea` 样式（深色背景/代码字体），保证编辑习惯一致。
  - **预览模式**：使用 `renderMarkdown` 工具函数渲染 Markdown 内容，提供所见即所得的反馈。
- **UI**：
  - 顶部工具栏：显示“编辑”和“预览”按钮。
  - 内容区：全高自适应。

### 2.2 修改 AgentManagement.vue
- **引入组件**：导入并注册 `MarkdownEditor`。
- **替换输入框**：将原有的 `system_prompt` `<textarea>` 替换为 `<MarkdownEditor v-model="versionForm.system_prompt" />`。
- **调整弹框尺寸**：
  - 将 `<Modal>` 的 `size` 属性从 `max-w-4xl` 调整为 **`max-w-7xl`**，大幅增加宽度。
  - 调整内部布局容器高度，确保编辑器能够利用增加的空间。

## 3. 验证方案
1.  编译前端 (`npm run build`)。
2.  启动服务。
3.  进入“智能体管理” -> 点击任意智能体的“版本管理” -> 点击“编辑”或“新建版本”。
4.  验证：
    - 弹框是否变宽。
    - 编辑器是否支持 Markdown 语法预览。
    - 内容是否能正常保存。
