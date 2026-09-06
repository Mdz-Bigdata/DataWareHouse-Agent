# 公共 Skill 显式绑定智能体一览

**日期:** 2026-07-21  
**状态:** 已批准，实施中

## 目标

技能工作台「平台技能」卡片/列表展示该 skill 被多少智能体**显式白名单绑定**；点击计数弹出绑定列表。文案用「绑定」，与「调用统计」区分。

## 绑定语义

| 条件 | 是否计入 |
|------|----------|
| `skills_custom=true` 且 `skills` 含该 id | ✅ |
| `skills_custom=false`（用全部公共技能） | ❌ |
| 个人技能 | ❌ |

**版本选取（含草稿）**：每个智能体只算一次——优先 `DRAFT`，否则 `PUBLISHED`；忽略 `ARCHIVED`。列表项带 `草稿` / `已发布`。

## 后端

`GET /api/portal/skills/bindings`（静态路径，须注册在 `/{skill_id}` 之前）

```json
{
  "status": "success",
  "bindings": {
    "web-search": {
      "count": 2,
      "agents": [
        {
          "id": "...",
          "name": "客服助手",
          "version_status": "DRAFT",
          "version_number": 3
        }
      ]
    }
  }
}
```

鉴权：与列表一致，`require_api_key`。

## 前端

- 仅平台技能 Tab：卡片徽章 `绑定 N`（含 0，灰色弱化）；列表加「绑定」列
- 点击徽章 `@click.stop` 弹层展示智能体名 + 状态
- 进入工作台并行拉取 `/bindings`；不进技能详情抽屉

## 非目标

- 不改运行时加载
- 详情抽屉不加绑定节
- 不做「默认全部技能」反向统计
