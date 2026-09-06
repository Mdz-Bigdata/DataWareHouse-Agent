## ADDED Requirements

### Requirement: 前端数据透视表 (Pivot Table) 交互
系统 MUST 在 `ChatCanvas.vue` 中新增“💡 数据透视”标签页，支持用户拖拽行/列维度与度量值，在前端内存中完成交叉汇总运算并渲染出透视表格。

#### Scenario: 动态拖拽维度更新透视结果
- **WHEN** 用户将行维度框中的“机房区域”与度量值框中的“机架总数”拖拽放置
- **THEN** 前端表格毫秒级重绘，按照机房区域对机架数进行求和（SUM）并输出

### Requirement: ECharts 样式微调指令拦截与本地重绘
系统 MUST 对非数据查询（如“折线改成柱状图”、“柱子标红”等）的排版与渲染类指令实施优先拦截。支持前端直接调用 `chartInstance.setOption` 完成重绘，而无需发回大模型重新执行 Text-to-SQL。

#### Scenario: 点击快捷工具栏切换图表类型
- **WHEN** 用户在图表卡片右上角点击“饼图”小图标
- **THEN** 图表瞬间切换为饼图形式，耗时小于 10ms，且网络面板中无新的数据请求

#### Scenario: 输入框样式微调指令短路渲染
- **WHEN** 用户在对话框中输入“把刚才的图表改为柱状图”
- **THEN** 后端分类器将其判定为 `FORMAT_CORRECTION` 短路返回，读取 Redis 缓存的上轮数据重新输出 ECharts Option 补丁，直接覆盖到画布中
