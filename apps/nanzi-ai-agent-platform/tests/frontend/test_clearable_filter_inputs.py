import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_all_search_filter_inputs_use_clearable_search_type():
    violations = []
    for source_path in Path("frontend/src").rglob("*.vue"):
        source = source_path.read_text()
        for tag in re.findall(r"<input\b[^>]*>", source, flags=re.DOTALL):
            if "搜索" not in tag or "placeholder" not in tag or "例如:" in tag:
                continue
            if not re.search(r'\btype=["\']search["\']', tag):
                line = source[: source.index(tag)].count("\n") + 1
                violations.append(f"{source_path}:{line}")

    assert not violations, "以下搜索输入框缺少清空按钮能力:\n" + "\n".join(violations)


def test_search_cancel_button_has_shared_visual_style():
    stylesheet = Path("frontend/src/style.css").read_text()

    assert 'input[type="search"]::-webkit-search-cancel-button' in stylesheet
    assert "cursor: pointer" in stylesheet
