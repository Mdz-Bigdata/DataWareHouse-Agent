import os

import pytest

from app.utils.skill_metadata import (
    build_skill_attachment_hint,
    enrich_messages_with_skill_meta,
    format_skill_meta_text,
    parse_skill_frontmatter,
)

pytestmark = pytest.mark.no_infrastructure


def test_parse_skill_frontmatter(tmp_path):
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        '---\nname: 演示技能\ndescription: "用于测试的技能说明"\n---\n# Body\n',
        encoding="utf-8",
    )
    meta = parse_skill_frontmatter("demo-skill", str(skill_dir / "SKILL.md"))
    assert meta["name"] == "演示技能"
    assert meta["description"] == "用于测试的技能说明"


def test_format_skill_meta_text():
    text = format_skill_meta_text({"name": "foo", "description": "bar"})
    assert text == "name: foo, description: bar"


def test_build_skill_attachment_hint_with_override():
    hint = build_skill_attachment_hint(
        "my-skill",
        skill_name="展示名",
        meta_override={"name": "my-skill", "description": "技能描述"},
    )
    assert "展示名" in hint
    assert "/app/data/skills/my-skill/SKILL.md" in hint
    assert "skills meta 为：name: my-skill, description: 技能描述" in hint


def test_enrich_messages_with_skill_meta(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "auto-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: 自动技能\ndescription: 从磁盘读取\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.core.config.settings",
        type("S", (), {"SKILLS_DIR": str(skills_dir)})(),
    )

    messages = [
        {
            "role": "user",
            "content": (
                "\n\n---\n\n"
                "用户本轮已调用生态技能工作流：自动技能，对应的物理描述文件绝对路径是："
                "/app/data/skills/auto-skill/SKILL.md。"
            ),
            "files": [{"type": "skill", "url": "auto-skill", "filename": "自动技能 (技能)"}],
        }
    ]
    enrich_messages_with_skill_meta(messages)
    assert "skills meta 为：name: 自动技能, description: 从磁盘读取" in messages[0]["content"]
