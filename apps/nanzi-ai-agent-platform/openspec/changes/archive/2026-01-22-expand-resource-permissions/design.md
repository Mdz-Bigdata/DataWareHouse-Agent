# Design: 菜单与功能按钮权限控制方案

## 1. 资源 ID 命名规范
为了保持一致性，建议采用层级化的命名空间：

### 菜单 (Menu)
格式：`menu:{module}` 或 `menu:{module}:{sub}`
- 示例：`menu:dashboard`, `menu:metadata:tables`, `menu:system:config`

### 功能点 (Element/Action)
格式：`element:{module}:{action}`
- 示例：`element:user:delete`, `element:agent:publish`, `element:dataset:sync`

## 2. 数据结构演进
### 后端返回结构 (UserPermissionsResponse)
更新 `PermissionSet` Schema：
```json
{
  "roles": ["user", "data_analyst"],
  "permissions": {
    "agents": ["sys-bi", "jira-bot"],
    "metadata": ["1", "5"],
    "menus": ["menu:dashboard", "menu:metadata"],
    "elements": ["element:dataset:sync"]
  }
}
```

## 3. 前端集成方案
### A. 菜单控制
1.  **路由配置**：在 `router/index.ts` 中，为需要权限的路由添加 `meta: { perm: 'menu:xxx' }`。
2.  **菜单过滤**：侧边栏组件监听 `PermissionStore` 的变化，使用 `filter` 递归过滤原始菜单树。

### B. 按钮控制 (Custom Directive)
实现 `v-has-perm` 指令：
```html
<!-- 仅在拥有同步权限时显示 -->
<button v-has-perm="'element:dataset:sync'" @click="sync">同步</button>
```
实现原理：在该指令的 `mounted` 钩子中，从 Store 获取当前用户权限集，若不包含指定 ID，则移除该 DOM 元素。

### C. 无权限状态处理 (No Permission Handling)
1.  **自动识别**：在全局路由守卫中，如果检测到非 Admin 用户的 `menus` 列表为空，则强制重定向至 `/no-permission` 页面。
2.  **兜底页面设计**：
    - **视觉**：采用简约的插画或图标（如：带锁的保险箱）。
    - **提示**：明确告知用户“当前账号尚未分配任何界面权限”。
    - **操作**：提供“联系系统管理员”或“退出登录”按钮。

## 4. 权限管理界面优化 (UI/UX)
在“分配权限”对话框（Modal）中，采用 **Tab 布局** 进行分类管理：

### Tab 1: 数据资产 (Data Assets)
- 原有的功能保持不变。
- 负责：`agents` (智能体), `metadata` (数据集), `api` (外部API), `datasets` (知识库)。

### Tab 2: 界面权限 (Interface Permissions) - 新增
- **展示形式**：以 **Tree (树形控件)** 展示。
- **层级结构**：
  - 根节点：菜单项（如：智能体管理）
    - 子节点：子菜单（如：版本历史）
    - 子节点：功能按钮（如：发布、下线、调试）
- **交互逻辑**：
  - 勾选父节点（菜单）自动展开或全选子节点（建议保持独立性）。
  - 存储时，菜单保存为 `menu:xxx`，按钮保存为 `element:xxx`。
  - **Admin 状态**：如果是系统管理员角色，该 Tab 下的所有项应默认选中且不可取消（或显示全局授权提示）。

