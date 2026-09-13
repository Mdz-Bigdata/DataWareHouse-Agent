"""裸环境可导入性：全部模块 import 零失败，且不需要任何第三方库。

这是 pyproject.toml 里「核心依赖为空」的执行依据——所有外部客户端库
（pymysql / neo4j / kafka-python / boto3 / redis / requests / PyYAML / pyflink / pyspark）
都必须延迟 import，缺失时报带安装指引的错，而不是让 import 失败。
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import adas_lakehouse

SRC = Path(adas_lakehouse.__file__).resolve().parent

#: 允许出现在**函数体内**的第三方库（全部延迟 import）。
#: 这份清单与 pyproject.toml 的 optional-dependencies 分组一一对应。
LAZY_THIRD_PARTY = {
    "pymysql",
    "mysql",  # mysql-connector-python
    "neo4j",
    "kafka",  # kafka-python
    "boto3",
    "redis",
    "requests",
    "yaml",  # PyYAML
    "pyflink",  # apache-flink
    "pyspark",
}

#: 标准库里本包顶层用到的模块（白名单，用来把「第三方」摘出来）。
STDLIB_TOPLEVEL = {
    "__future__",
    "abc",
    "argparse",
    "collections",
    "collections.abc",
    "contextlib",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "heapq",
    "importlib",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "random",
    "re",
    "shutil",
    "statistics",
    "string",
    "subprocess",
    "sys",
    "tempfile",
    "textwrap",
    "threading",
    "time",
    "types",
    "typing",
    "unicodedata",
    "urllib",
    "uuid",
    "warnings",
    "weakref",
}


def _module_names() -> list[str]:
    return sorted(
        m.name
        for m in pkgutil.walk_packages(adas_lakehouse.__path__, f"{adas_lakehouse.__name__}.")
    )


ALL_MODULES = _module_names()


def _source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _top_level_imports(tree: ast.Module) -> set[str]:
    """只取模块顶层（不含函数/方法体内）的 import 名。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import，是本包自己的
                continue
            if node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.If):  # if TYPE_CHECKING: 之类
            for sub in node.body:
                if isinstance(sub, ast.Import):
                    names |= {alias.name.split(".")[0] for alias in sub.names}
                elif isinstance(sub, ast.ImportFrom) and node.level == 0 and sub.module:
                    names.add(sub.module.split(".")[0])
    return names


# --------------------------------------------------------------------------- 导入


def test_the_package_has_a_version():
    assert adas_lakehouse.__version__


def test_module_walk_finds_the_whole_tree():
    assert len(ALL_MODULES) >= 100
    for expected in (
        "adas_lakehouse.cli",
        "adas_lakehouse.catalog.registry",
        "adas_lakehouse.ids",
        "adas_lakehouse.naming",
        "adas_lakehouse.domains",
    ):
        assert expected in ALL_MODULES


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_every_module_imports_cleanly(module_name):
    importlib.import_module(module_name)


# --------------------------------------------------------------------------- 依赖纪律


def test_no_module_imports_a_third_party_library_at_top_level():
    """顶层不许 import 第三方库——否则「裸装即可用」的承诺就破了。"""
    offenders: dict[str, set[str]] = {}
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        third_party = {
            name
            for name in _top_level_imports(tree)
            if name not in STDLIB_TOPLEVEL and name != "adas_lakehouse"
        }
        if third_party:
            offenders[str(path.relative_to(SRC))] = third_party
    assert offenders == {}, f"顶层 import 了第三方库: {offenders}"


def test_lazy_third_party_list_matches_what_the_code_actually_uses():
    """扫出来的延迟 import 必须都在已声明的可选依赖清单里，不能有野依赖。"""
    used: set[str] = set()
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        top = _top_level_imports(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                used |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                used.add(node.module.split(".")[0])
        used -= top

    third_party = used - STDLIB_TOPLEVEL - {"adas_lakehouse"}
    assert third_party <= LAZY_THIRD_PARTY, (
        f"出现了未在 pyproject optional-dependencies 里声明的库: "
        f"{sorted(third_party - LAZY_THIRD_PARTY)}"
    )
