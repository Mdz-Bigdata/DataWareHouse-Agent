"""SKILL.md Frontmatter 解析与技能附件隐式指令构建。"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

USER_MESSAGE_CONTEXT_DIVIDER = "\n\n---\n\n"


def parse_skill_frontmatter(skill_id: str, skill_md_path: str) -> Dict[str, str]:
    """从 SKILL.md 顶部 YAML Frontmatter 提取 name / description 等元数据。"""
    meta: Dict[str, str] = {"id": skill_id, "name": skill_id, "description": "", "enabled": "true"}
    if not os.path.exists(skill_md_path):
        return meta
    try:
        with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(8192)
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            for line in match.group(1).splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    except Exception:
        pass
    if "enabled" not in meta:
        meta["enabled"] = "true"
    return meta


def toggle_skill_enabled_in_file(skill_md_path: str, enabled: bool) -> bool:
    """在 SKILL.md 的 YAML Frontmatter 中修改或插入 enabled 状态。"""
    if not os.path.exists(skill_md_path):
        return False
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            yaml_block = match.group(1)
            rest_content = content[match.end():]
            
            new_lines = []
            has_enabled = False
            for line in yaml_block.splitlines():
                if line.strip().lower().startswith("enabled:"):
                    new_lines.append(f"enabled: {str(enabled).lower()}")
                    has_enabled = True
                else:
                    new_lines.append(line)
            
            if not has_enabled:
                new_lines.append(f"enabled: {str(enabled).lower()}")
                
            new_yaml_block = "\n".join(new_lines)
            new_content = f"---\n{new_yaml_block}\n---\n{rest_content}"
            
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return True
    except Exception:
        pass
    return False


def format_skill_meta_text(meta: Dict[str, str]) -> str:
    """将 Frontmatter 格式化为隐式指令中的 meta 摘要。"""
    name = (meta.get("name") or meta.get("id") or "").strip()
    desc = (meta.get("description") or "").strip()
    parts: List[str] = []
    if name:
        parts.append(f"name: {name}")
    if desc:
        parts.append(f"description: {desc}")
    return ", ".join(parts) if parts else "（无 SKILL.md Frontmatter 元数据）"


def build_skill_attachment_hint(
    skill_id: str,
    *,
    skill_name: Optional[str] = None,
    meta_override: Optional[Dict[str, Any]] = None,
) -> str:
    """构建技能挂载隐式指令（含路径与 Frontmatter meta）。"""
    from app.core.config import settings

    skill_md_path = os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md")
    if meta_override:
        meta = {
            "id": skill_id,
            "name": str(meta_override.get("name") or skill_id),
            "description": str(meta_override.get("description") or ""),
        }
    else:
        meta = parse_skill_frontmatter(skill_id, skill_md_path)

    display_name = (skill_name or meta.get("name") or skill_id).replace(" (技能)", "")
    path = f"/app/data/skills/{skill_id}/SKILL.md"
    meta_text = format_skill_meta_text(meta)
    return (
        f"用户本轮已调用生态技能工作流：{display_name}，对应的物理描述文件绝对路径是：{path}。\n"
        f"skills meta 为：{meta_text}"
    )


def enrich_messages_with_skill_meta(messages: List[Dict[str, Any]]) -> None:
    """若最后一条 user 消息挂载技能但隐式指令缺少 meta，则补全或重建技能上下文块。"""
    if not messages:
        return
    last = messages[-1]
    files = last.get("files") or []
    skill_files = [f for f in files if f.get("type") == "skill"]
    if not skill_files:
        return
    content = last.get("content") or ""
    if "skills meta 为：" in content:
        return

    hints = []
    for sf in skill_files:
        skill_id = sf.get("url")
        if not skill_id:
            continue
        skill_name = (sf.get("filename") or "").replace(" (技能)", "")
        meta_override = sf.get("skillMeta") or sf.get("skill_meta")
        hints.append(
            build_skill_attachment_hint(
                skill_id,
                skill_name=skill_name,
                meta_override=meta_override,
            )
        )
    if not hints:
        return

    context_block = "\n\n".join(hints)
    if USER_MESSAGE_CONTEXT_DIVIDER in content:
        user_part, _ctx = content.split(USER_MESSAGE_CONTEXT_DIVIDER, 1)
        user_part = user_part.strip()
        if user_part:
            last["content"] = f"{user_part}{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
        else:
            last["content"] = f"{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
    elif content.strip():
        last["content"] = f"{content.strip()}{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
    else:
        last["content"] = f"{USER_MESSAGE_CONTEXT_DIVIDER}{context_block}"
