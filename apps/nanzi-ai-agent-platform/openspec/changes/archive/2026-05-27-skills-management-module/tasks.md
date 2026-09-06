# 任务清单

## 1. 后端依赖与核心配置
- [x] 1.1 在系统配置中定义并规范 `SKILLS_DIR` 目录路径，处理本地开发调试的降级路径
- [x] 1.2 在 `app/api/portal/endpoints/` 下新建 `skills.py` 路由控制文件

## 2. 后端 API 接口与安全防御实现
- [x] 2.1 编写 `GET /api/portal/skills` 列表读取接口，利用正则解析 `SKILL.md` 的 YAML 头部
- [x] 2.2 编写 `POST /api/portal/skills` 新建技能接口，自动初始化默认的 `SKILL.md` 模板文件
- [x] 2.3 编写 `GET /api/portal/skills/{skill_id}` 详情获取接口，构建文件树结构并读取 Markdown
- [x] 2.4 编写 `PUT /api/portal/skills/{skill_id}/files` 保存更新技能文件内容接口，支持任意文本类型编辑
- [x] 2.5 编写 `POST /api/portal/skills/{skill_id}/upload` 文件上传接口，支持直接上传 Python 等脚本文件
- [x] 2.6 编写 `DELETE /api/portal/skills/{skill_id}/files` 删除技能内部指定文件接口
- [x] 2.7 编写 `DELETE /api/portal/skills/{skill_id}` 彻底注销删除技能文件夹接口
- [x] 2.8 在上述所有写、删、查 API 统一加入 `os.path.commonpath` 防越界路径穿越拦截
- [x] 2.9 在 `app/api/portal/api.py` 中挂载 `skills.py` 路由服务


## 3. 前端 UI 看板与在线编辑管理集成
- [x] 3.1 修改 `frontend/src/views/Dashboard.vue` 侧边栏菜单，挂载“技能管理”导航条目及专属 SVG 魔法书图标
- [x] 3.2 增加前端路由映射，将 `/dashboard/skills` 指向 `SkillsManagement.vue` 视图
- [x] 3.3 新建 `frontend/src/views/SkillsManagement.vue` 主页面，实现页面顶部的「？」帮助弹窗（内含 `npx skills add` 教学文案与 skills.sh 生态外链）
- [x] 3.4 实现玻璃渐变卡片网格列表、实时搜索过滤、右上角“新建技能”弹窗以及技能删除按钮
- [x] 3.5 编写详情抽屉弹窗（Drawer），左半区包含大文本框编辑器与“保存”按钮，支持对 `SKILL.md` 或脚本等文本文件进行在线高亮编辑保存
- [x] 3.6 在抽屉弹窗右半区渲染技能下的文件目录树，在树节点上提供删除按钮，并在底部挂载“上传文件”上传控件，支持直传脚本

## 4. 自动化测试编写与验证
- [x] 4.1 编写 `tests/test_skills_management.py` 自动化测试用例，覆盖技能与文件的创建、获取、保存编辑、上传、删除、以及目录穿越攻击的异常验证
- [x] 4.2 将新增的测试用例项更新至系统 `tests/CHECKLIST.md` 清单

## 5. 编译重启与验证
- [x] 5.1 执行 `./dev.sh` 热编译，验证前端与后端热启动均无异常报错
- [x] 5.2 在平台管理端进行新建、上传脚本、在线保存编辑及删除的全生命周期联调测试，确认运行平稳
