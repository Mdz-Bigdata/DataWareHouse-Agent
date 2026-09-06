# 智能体技能管理（Skills Management）模块设计方案

## 1. 后端架构与 API 设计

后端使用 FastAPI 架构，通过对物理路径的扫描实现动态读取与解析，保持极致的高内聚与零污染（No Database Impact）。

### 1.1 全局配置常量
在 `app/core/config.py` 或相关文件内，我们约定配置：
```python
# 技能物理存放根目录
SKILLS_DIR = "/app/data/skills" 
# 本地开发环境兜底降级路径
if not os.path.exists(SKILLS_DIR):
    SKILLS_DIR = os.path.abspath("data/skills")
```

### 1.2 安全防穿越助手与核心 API 接口
与之前一致，编写包含 `validate_secure_skill_path(skill_id, relative_path)` 在内的绝对路径沙箱保护机制。

所有的 API 端点均使用 `Depends(require_permission("menu", "menu:skills_management"))` 实施严格的菜单权限保护，防止越权访问。提供以下 7 大核心 API 端点：
1) `GET /api/portal/skills`
2) `POST /api/portal/skills`
3) `GET /api/portal/skills/{skill_id}`
4) `PUT /api/portal/skills/{skill_id}/files`
5) `POST /api/portal/skills/{skill_id}/upload`
6) `DELETE /api/portal/skills/{skill_id}/files`
7) `DELETE /api/portal/skills/{skill_id}`


---

## 2. 前端界面设计与样式规范

前端使用 Vue3 组合式 API + Tailwind CSS 进行开发，保持与主体系统高水准、现代化的视觉规范完美融合。

### 2.1 菜单与导航
* 在 `Dashboard.vue` 的“智能体开发平台”中添加 “技能管理” 菜单，配置 `skills` 图标。

### 2.2 前端核心主页面：`SkillsManagement.vue`
主体设计升级如下：

#### 1) 顶部栏与「？」帮助弹窗
* 在顶部标题“技能管理”右侧，紧邻添加一个 **「？」图标按钮**：
  - **样式**：具有呼吸微动画的蓝色/灰色透明胶囊，如 `text-gray-400 hover:text-primary transition-all p-1 rounded-full hover:bg-gray-100`。
  - **弹窗卡片 (Help Modal)**：点击后以高颜值玻璃磨砂弹出，展示内容：
    * **什么是 Skills**：解释大模型高阶技能规范。
    * **CLI 命令行安装方式**：详细罗列说明，比如当想使用社区的 skills 时，可以在平台的映射目录 `./data/skills` 下执行：
      ```bash
      npx skills add https://github.com/vercel-labs/skills --skill find-skills
      ```
    * **生态链接**：底部以醒目的高科技按钮形式，附上超链接指向：[💡 前往官方 Skills 开放市场 (https://www.skills.sh/)](https://www.skills.sh/)。

#### 2) 网格面板卡片
* 设计为 `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6` 布局。

#### 3) 全功能抽屉详情 (Drawer)
* **左半边 (在线编辑器)**：支持 Monaco 或高效 textarea 形式在线编辑 `SKILL.md` 等规则。
* **右半边 (物理资产与上传)**：展示物理文件树结构，支持删除单个文件或上传辅助代码。
