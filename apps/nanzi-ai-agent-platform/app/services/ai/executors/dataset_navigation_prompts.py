"""数据集导航 Prompt 生成与解析模块。"""

from __future__ import annotations

import re
import logging
from typing import Any, List, Dict, Optional
from app.services.ai.time_anchor import build_data_query_time_anchor_block
from app.services.ai.executors.prompt_constants import (
    DATASET_PORTAL_SLASH_COMMAND,
    DATASET_PORTAL_LEGACY_SLASH_COMMAND,
)

logger = logging.getLogger(__name__)


def is_dataset_portal_slash_query(query: str) -> bool:
    """判断当前查询是否是数据门户斜杠命令。"""
    q = str(query or "").strip()
    return (
        q in (DATASET_PORTAL_SLASH_COMMAND, DATASET_PORTAL_LEGACY_SLASH_COMMAND)
        or DATASET_PORTAL_SLASH_COMMAND in q
        or DATASET_PORTAL_LEGACY_SLASH_COMMAND in q
    )


class DatasetNavigationPrompts:
    """数据集导航 Prompts 与辅助方法容器类。"""

    @staticmethod
    def quick_link_inline(label: str, target: str) -> str:
        """内联 quick 追问按钮（无列表前缀，可放入表格单元格、引用块等行内场景）。"""
        target_val = target.strip()
        if label.startswith("⚡") or target_val.startswith("/switch"):
            clean_label = label.lstrip("⚡ ").strip()
            return f"[⚡ {clean_label}](quick:{target_val})"
        return f"[🙋 {label}](quick:{target_val})"

    @staticmethod
    def quick_button(label: str, target: str) -> str:
        """格式化为列表项形式的快捷按钮。"""
        target_val = target.strip()
        if label.startswith("⚡") or target_val.startswith("/switch"):
            clean_label = label.lstrip("⚡ ").strip()
            return f"- [⚡ {clean_label}](quick:{target_val})"
        return f"- [🙋 {label}](quick:{target_val})"

    @staticmethod
    def _portal_time_recommendation_rules() -> str:
        return (
            f"{build_data_query_time_anchor_block()}\n\n"
            "【相对时间推荐规则】\n"
            "生成示例问题时，若使用「今天/昨天/本周/上周/本月/上月/最近7天/本季度」等相对时间表述，"
            "必须与上方【当前时间锚点】中的具体日期或区间一致，并在问题中尽量写出明确日期范围。"
        )

    @staticmethod
    def dataset_navigation_generation_prompt(dataset_menu: str) -> str:
        return f"""你是 ChatBI 我的数据门户生成模块。

用户执行了 `{DATASET_PORTAL_SLASH_COMMAND}` 指令，希望了解当前账号**有权访问**哪些数据、能问什么问题。
下方【可用数据集目录】与 ChatBI 智能体 system prompt 中的 `{{dataset_menu}}` **完全一致**，请仅基于其中信息生成导航，不要编造未列出的数据集、表或指标。

任务：
1. 开头用**引用块**（`>` 开头）做整体概要：用 1-2 句话说明数据集数量、覆盖的主要业务域与整体能查询的能力范围。
2. **每个授权数据集对应一张独立的业务场景卡片**，卡片标题必须使用该数据集 the **Display Name**（若无则使用 Dataset 名）；不要将多个数据集合并到同一张卡片。
3. 每张业务场景卡片必须包含：
   - `#### 场景标题`
   - 一段引用块概要，说明适合解决什么业务问题。
   - `**你可以这样问：**`，下面给 2-4 个 quick 示例问题。
   - `**相关数据：**`，列出真实 Dataset 展示名与相关表中文术语。
   - `**继续追问：**`，给 1-2 个围绕该场景继续探索的 quick 按钮。
4. 示例问题必须可直接发送给 ChatBI，问题中优先使用中文表术语、业务对象、指标或时间范围。
5. 有 `Metrics` 时，补充围绕指标的趋势、排名、同比环比或异常分析追问。
6. 文末提供全局快捷入口（含重新查看导航）。

命名与文案要求：
- **有中文备注/术语时一律优先用中文**：Display Name、表 term、Table Details 中的中文描述。
- 不要输出英文物理表名，除非目录中没有中文术语。

输出要求：
- 只输出 Markdown，不要 JSON，不要用代码块包裹全文。
- 以 `### 📚 我的数据门户` 开头，用 `---` 分隔区块。
- 整体概要与每个场景介绍必须使用引用块（`>` 开头）。
- quick 示例问题必须使用列表项格式：`- [🙋 简短标签](quick:完整可发送问题)`。
- 文末必须包含 `### 💬 您可能还想了解`，其中至少包含一行列表项：`- [🙋 重新查看数据门户](quick:{DATASET_PORTAL_SLASH_COMMAND})`。
- “继续追问”属于每张业务场景卡片内部；全局“您可能还想了解”区块必须放在整段回答最末尾。
- 不要编造目录中未出现的具体数值、表名或指标名。

{DatasetNavigationPrompts._portal_time_recommendation_rules()}

【可用数据集目录】
{dataset_menu}
"""

    @classmethod
    def _parse_dataset_blocks(cls, dataset_menu: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        in_table_details = False

        for raw_line in str(dataset_menu or "").splitlines():
            line = raw_line.rstrip()
            if line.startswith("- Dataset:"):
                if current:
                    blocks.append(current)
                match = re.match(r"- Dataset:\s*(\S+)", line)
                current = {
                    "name": match.group(1) if match else "",
                    "display_name": "",
                    "description": "",
                    "tables": [],
                    "metrics": [],
                }
                in_table_details = False
                continue
            if not current:
                continue
            if line.startswith("  Display Name:"):
                current["display_name"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Description:"):
                current["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("  Includes Tables:"):
                tables_part = line.split(":", 1)[1].strip()
                current["tables"] = [
                    {"term": t.strip(), "desc": ""}
                    for t in tables_part.split(",")
                    if t.strip()
                ]
                in_table_details = False
            elif line.strip() == "Table Details:":
                in_table_details = True
            elif in_table_details and line.startswith("    - "):
                detail = line[6:].strip()
                parts = detail.split(":", 1)
                term = parts[0].strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                if not term:
                    continue
                existing = next(
                    (t for t in current["tables"] if t.get("term") == term),
                    None,
                )
                if existing is None:
                    current["tables"].append({"term": term, "desc": desc})
                elif desc and not existing.get("desc"):
                    existing["desc"] = desc
            elif line.startswith("  Metrics:"):
                metrics_part = line.split(":", 1)[1].strip()
                current["metrics"] = [m.strip() for m in metrics_part.split(",") if m.strip()]
                in_table_details = False

        if current:
            blocks.append(current)
        
        # 优化点 10：增加解析后的断言/警告日志，当 dataset_menu 非空但解析 blocks 为空时报警
        if dataset_menu.strip() and not blocks:
            logger.warning(
                "[DatasetNavigationPrompts] dataset_menu is non-empty, but parsed blocks is empty. "
                "Format might be incorrect. Input menu snippet: %s",
                dataset_menu[:500]
            )
            
        return blocks

    @classmethod
    def _dataset_heading(cls, block: dict[str, Any]) -> str:
        name = str(block.get("name") or "").strip()
        display_name = str(block.get("display_name") or "").strip()
        if display_name and display_name != name:
            return f"{display_name} ({name})"
        return name or "未命名数据集"

    @staticmethod
    def _sanitize_table_cell(text: str, *, max_len: int = 40) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip()).replace("|", "/")
        if max_len and len(cleaned) > max_len:
            cleaned = cleaned[:max_len] + "..."
        return cleaned

    @staticmethod
    def _slugify_scene_id(text: str) -> str:
        slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(text or "").strip()).strip("_")
        return slug[:48] or "dataset_scene"

    @classmethod
    def _dataset_scene_title(cls, block: dict[str, Any]) -> str:
        display_name = str(block.get("display_name") or "").strip()
        name = str(block.get("name") or "").strip()
        return display_name or name or "未命名数据集"

    @classmethod
    def _extract_dataset_tags(cls, block: dict[str, Any], *, max_tags: int = 4) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()

        def add(tag: str) -> None:
            cleaned = str(tag or "").strip()
            if not cleaned or cleaned in seen:
                return
            seen.add(cleaned)
            tags.append(cleaned)

        for metric in block.get("metrics") or []:
            add(str(metric))
            if len(tags) >= max_tags:
                return tags[:max_tags]

        if tags:
            return tags[:max_tags]

        for table in block.get("tables") or []:
            add(str(table.get("term") or ""))
            if len(tags) >= max_tags:
                break

        if not tags:
            return ["明细查询", "统计汇总"]
        return tags[:max_tags]

    @classmethod
    def _infer_dataset_scene(cls, block: dict[str, Any]) -> dict[str, Any]:
        title = cls._dataset_scene_title(block)
        description = str(block.get("description") or "").strip()
        tables = [
            str(t.get("term") or "").strip()
            for t in block.get("tables") or []
            if str(t.get("term") or "").strip()
        ]
        table_hint = "、".join(tables[:3]) if tables else "相关表"
        generic_descriptions = {
            "no description",
            "imported via smart wizard",
            "无",
            "暂无描述",
        }
        if description and description.lower() not in generic_descriptions:
            summary = f"适合围绕「{title}」查询与分析。{description}"
        else:
            summary = (
                f"适合围绕「{title}」中的{table_hint}发起明细查询、统计汇总与趋势分析。"
            )
        return {
            "title": title,
            "summary": summary,
            "tags": cls._extract_dataset_tags(block),
        }

    @classmethod
    def _question_templates_for_group(
        cls,
        *,
        scene_title: str,
        tables: list[dict[str, Any]],
        metrics: list[str],
    ) -> list[dict[str, str]]:
        table_terms = [str(t.get("term") or "").strip() for t in tables if str(t.get("term") or "").strip()]
        primary = table_terms[0] if table_terms else scene_title
        secondary = table_terms[1] if len(table_terms) > 1 else primary
        questions: list[dict[str, str]] = [
            {
                "label": "查看概览",
                "query": f"统计{primary}最近30天的数据概览和关键变化",
                "type": "summary",
            },
            {
                "label": "查询明细",
                "query": f"查询{primary}最近10条明细记录",
                "type": "detail",
            },
            {
                "label": "趋势分析",
                "query": f"分析{secondary}最近一个月的变化趋势",
                "type": "trend",
            },
        ]
        if metrics:
            metric = str(metrics[0]).strip()
            if metric:
                questions.append(
                    {
                        "label": "指标趋势",
                        "query": f"查询{scene_title}最近6个月的{metric}趋势",
                        "type": "metric",
                    }
                )
        return questions

    @classmethod
    def build_dataset_navigation_groups(cls, dataset_menu: str) -> list[dict[str, Any]]:
        """每个授权数据集生成一张独立场景卡片，标题与标签来自数据集元数据。"""
        groups: list[dict[str, Any]] = []

        for block in cls._parse_dataset_blocks(dataset_menu):
            scene = cls._infer_dataset_scene(block)
            name = str(block.get("name") or "").strip()
            display_name = str(block.get("display_name") or "").strip() or name or "未命名数据集"
            scene_title = scene["title"]
            tables = [t for t in (block.get("tables") or []) if str(t.get("term") or "").strip()]
            metrics = [str(m).strip() for m in (block.get("metrics") or []) if str(m).strip()]

            table_terms: list[str] = []
            table_descriptions: list[dict[str, str]] = []
            for table in tables:
                term = str(table.get("term") or "").strip()
                table_terms.append(term)
                table_descriptions.append(
                    {
                        "name": term,
                        "description": str(table.get("desc") or "").strip(),
                    }
                )

            groups.append(
                {
                    "id": cls._slugify_scene_id(name or display_name),
                    "title": scene_title,
                    "summary": scene["summary"],
                    "tags": scene["tags"],
                    "metrics": metrics,
                    "questions": cls._question_templates_for_group(
                        scene_title=scene_title,
                        tables=tables,
                        metrics=metrics,
                    ),
                    "related_data": [
                        {
                            "dataset": name,
                            "display_name": display_name,
                            "tables": table_terms,
                            "table_descriptions": table_descriptions,
                        }
                    ],
                    "followups": [
                        {
                            "label": "更多问题",
                            "query": f"围绕「{scene_title}」，推荐我还能问哪些数据问题",
                        },
                        {
                            "label": "字段说明",
                            "query": f"说明「{scene_title}」里有哪些可查询字段 and 适合的分析口径",
                        },
                    ],
                }
            )
        return groups

    @classmethod
    def _render_group_fallback_section(cls, group: dict[str, Any]) -> list[str]:
        """单个业务场景卡片：标题 + 示例问题 + 相关数据 + 继续追问。"""
        lines: list[str] = [f"#### {group['title']}"]
        lines.append(f"> {group['summary']}")
        if group.get("tags"):
            lines.append("> 标签：" + "、".join(group["tags"]))
        lines.append("")
        lines.append("**你可以这样问：**")
        for question in group.get("questions") or []:
            lines.append(cls.quick_button(question.get("label") or "提问", question.get("query") or ""))
        lines.append("")
        related_items = group.get("related_data") or []
        if not related_items:
            lines.append("**相关数据：** 暂无表信息")
        else:
            for idx, related in enumerate(related_items):
                related_name = related.get("display_name") or related.get("dataset") or "相关数据"
                dataset_name = related.get("dataset") or ""
                related_title = (
                    f"{related_name} ({dataset_name})"
                    if dataset_name and dataset_name != related_name
                    else related_name
                )
                if idx == 0:
                    lines.append(f"**相关数据：** {related_title}")
                else:
                    lines.append(f"- {related_title}")
                for table in related.get("table_descriptions") or []:
                    desc = str(table.get("description") or "").strip()
                    suffix = f"：{cls._sanitize_table_cell(desc, max_len=80)}" if desc else ""
                    lines.append(f"  - {table.get('name')}{suffix}")
                if not related.get("tables"):
                    lines.append("  - 暂无表信息")
        lines.append("")
        lines.append("**继续追问：**")
        for followup in group.get("followups") or []:
            lines.append(cls.quick_button(followup.get("label") or "继续追问", followup.get("query") or ""))
        return lines

    @classmethod
    def build_dataset_navigation_fallback(cls, dataset_menu: str) -> str:
        menu = str(dataset_menu or "").strip()
        if not menu or "No authorized datasets" in menu:
            return (
                "### 📚 我的数据门户\n"
                "---\n"
                "> 当前账号暂无可查询的数据集。请联系管理员开通数据权限后，再使用 `{DATASET_PORTAL_SLASH_COMMAND}` 查看导航。\n\n"
                "### 💬 您可能还想了解\n"
                "---\n"
                f"{cls.quick_button('重新查看数据门户', DATASET_PORTAL_SLASH_COMMAND)}\n"
            )

        blocks = cls._parse_dataset_blocks(menu)
        if blocks:
            groups = cls.build_dataset_navigation_groups(menu)
            scene_titles = "、".join(group.get("title", "") for group in groups[:4] if group.get("title"))
            lines = [
                "### 📚 我的数据门户",
                "---",
                f"> 您当前可访问 **{len(blocks)}** 个数据集，已整理为业务场景卡片。"
                + (f"主要覆盖：{scene_titles}。" if scene_titles else ""),
                "",
            ]
            for group in groups:
                lines.extend(cls._render_group_fallback_section(group))
                lines.append("")

            lines.extend(
                [
                    "### 💬 您可能还想了解",
                    "---",
                    cls.quick_button("重新查看数据门户", DATASET_PORTAL_SLASH_COMMAND),
                ]
            )
            return "\n".join(lines)
        body = menu
        if len(body) > 2400:
            body = body[:2400] + "\n... [目录过长已截断]"

        return (
            "### 📚 我的数据门户\n"
            "---\n"
            "暂时无法自动生成导航建议，以下为当前账号可访问的数据集目录（与 ChatBI 一致）：\n\n"
            "```\n"
            f"{body}\n"
            "```\n\n"
            "您可以直接用自然语言提问，或点击下方按钮重试。\n\n"
            "### 💬 您可能还想了解\n"
            "---\n"
            f"{cls.quick_button('重新查看数据门户', DATASET_PORTAL_SLASH_COMMAND)}\n"
        )

