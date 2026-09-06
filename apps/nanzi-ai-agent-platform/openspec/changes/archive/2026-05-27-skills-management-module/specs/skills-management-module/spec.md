## ADDED Requirements

### Requirement: skills_lifecycle 技能全生命周期管理
系统必须提供（MUST）新建（POST）、删除（DELETE）、及更新（PUT）技能及其目录内资产文件的能力。所有的写操作必须实施严格的路径边界防穿越校验。

#### Scenario: 成功创建新技能
- **WHEN** 管理员发送 `POST /api/portal/skills` 请求，Body 中包含 `id="test-lifecycle"`, `name="生命周期测试"`, `description="desc"`
- **THEN** 后端自动在物理目录创建文件夹 `/app/data/skills/test-lifecycle` 并自动写入默认的 `SKILL.md`，返回 200 成功响应。

---

### Requirement: skills_help_tooltip 技能安装指南帮助弹窗
前端技能管理页面必须提供（MUST）一个显著的帮助按钮，点击后能够弹出玻璃磨砂风格的引导说明。说明必须清晰告知用户如何通过 Skills CLI 下载社区技能，并包含指向 skills.sh 的超链接。

#### Scenario: 打开帮助指引弹窗并跳转开放市场
- **WHEN** 用户在技能管理页面点击标题旁的 `?` 按钮
- **THEN** 界面居中弹出精美弹窗，显示 `npx skills add https://github.com/vercel-labs/skills --skill find-skills` 等安装样例命令
- **AND** 弹窗底部提供清晰醒目的 [前往官方开放市场 (https://www.skills.sh/)](https://www.skills.sh/) 按钮，点击能够在新标签页打开外部生态网站
