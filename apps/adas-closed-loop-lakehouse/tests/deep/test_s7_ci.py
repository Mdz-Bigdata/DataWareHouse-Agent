"""adas 侧的 CI 接线契约。

在 §7.9-3 之前，本仓库根目录没有任何 `.github/workflows`，所以这 1372 个用例和
`make check` 的四道离线门禁（validate / ports / lint / fmt-check）**从未被自动执行过**。
现在仓库根的 CI 会调用本包的 `make check`，于是产生了一组跨目录的隐式契约：

* CI 靠一条 `make check` 覆盖全部五项——`check` 少挂一个依赖目标，CI 就会静默漏检；
* CI 传 `PORTS_ARGS="--no-docker --skip-listen"`——runner 上没有 Docker 守护进程，
  也不该把 runner 自身的监听端口当成本项目的冲突。这两个开关一旦被删，CI 会红得
  莫名其妙（或更糟：退化成永远通过）；
* CI 的 Python 矩阵必须落在本包 `requires-python` 声明的范围内。

这些契约横跨两个目录、谁都不拥有对方，最容易在重构时单边改掉。这里把它钉住。

本包也可能被单独 checkout 使用（没有仓库根），那种情况下与 CI 相关的用例自动跳过。
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE_ROOT.parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = PACKAGE_ROOT / "Makefile"
CHECK_PORTS = PACKAGE_ROOT / "scripts" / "check_ports.py"

# CI 调用本包时使用的端口体检参数，必须与 ci.yml 里那一行保持一致。
CI_PORTS_ARGS = ["--no-docker", "--skip-listen"]

needs_repo_ci = pytest.mark.skipif(
    not WORKFLOW.is_file(),
    reason="未找到仓库根的 .github/workflows/ci.yml（本包被单独 checkout）",
)


def _makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _target_prerequisites(text: str, target: str) -> list[str]:
    """取出 ``target: a b c`` 里的依赖列表（跳过 .PHONY 声明行）。"""
    for line in text.splitlines():
        if line.startswith(".PHONY"):
            continue
        match = re.match(rf"^{re.escape(target)}\s*:\s*(.*)$", line)
        if match:
            return match.group(1).split()
    return []


# ---------------------------------------------------------------------------
# make check 的组成——CI 只调它一条命令
# ---------------------------------------------------------------------------
class TestCheckTargetContract:
    def test_check_covers_all_five_offline_gates(self):
        prereqs = _target_prerequisites(_makefile_text(), "check")
        assert prereqs, "Makefile 里找不到 check 目标"
        expected = {"validate", "ports", "lint", "fmt-check", "test"}
        missing = expected - set(prereqs)
        assert not missing, (
            f"check 目标不再覆盖 {sorted(missing)}；CI 只调一条 make check，少一个就是静默漏检"
        )

    def test_every_prerequisite_of_check_is_a_real_target(self):
        text = _makefile_text()
        for prereq in _target_prerequisites(text, "check"):
            assert re.search(rf"(?m)^{re.escape(prereq)}\s*:", text), (
                f"check 依赖了不存在的目标 {prereq}"
            )

    def test_ports_args_stays_overridable(self):
        # CI 靠 `make check PORTS_ARGS=...` 覆盖它；写成 `=` 或写死就覆盖不了。
        assert re.search(r"(?m)^PORTS_ARGS\s*\?=", _makefile_text()), (
            "PORTS_ARGS 必须是 ?= 的可覆盖变量，否则 CI 传的参数会被忽略"
        )

    def test_ports_target_forwards_ports_args(self):
        text = _makefile_text()
        ports_recipe = re.search(r"(?m)^ports\s*:\s*\n((?:\t.*\n)+)", text)
        assert ports_recipe, "找不到 ports 目标的命令体"
        assert "$(PORTS_ARGS)" in ports_recipe.group(1), (
            "ports 目标没把 PORTS_ARGS 透传给 check_ports.py"
        )


# ---------------------------------------------------------------------------
# CI 传进来的参数必须真的被接受
# ---------------------------------------------------------------------------
class TestCiPortsArguments:
    def test_check_ports_accepts_the_flags_ci_passes(self):
        """直接跑一遍——比断言 argparse 里有这个字符串更有说服力。"""
        result = subprocess.run(
            [sys.executable, str(CHECK_PORTS), *CI_PORTS_ARGS, "--quiet"],
            capture_output=True,
            text=True,
            cwd=str(PACKAGE_ROOT),
        )
        assert result.returncode == 0, (
            f"CI 使用的 PORTS_ARGS 跑不通：\n{result.stdout}\n{result.stderr}"
        )

    def test_unknown_flag_still_fails(self):
        """前一条若因为 argparse 静默吞掉未知参数而通过，就毫无意义——反证一下。"""
        result = subprocess.run(
            [sys.executable, str(CHECK_PORTS), "--definitely-not-a-flag"],
            capture_output=True,
            text=True,
            cwd=str(PACKAGE_ROOT),
        )
        assert result.returncode != 0, "check_ports.py 居然接受了未知参数"


# ---------------------------------------------------------------------------
# 与仓库根 CI 的接线
# ---------------------------------------------------------------------------
@needs_repo_ci
class TestRepositoryWorkflowWiresAdasIn:
    @staticmethod
    def _workflow_text() -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_invokes_make_check_in_this_package(self):
        text = self._workflow_text()
        rel = PACKAGE_ROOT.relative_to(REPO_ROOT).as_posix()
        assert f"working-directory: {rel}" in text, f"仓库根 CI 没有在 {rel} 目录下执行任何步骤"
        assert "make check" in text, "仓库根 CI 没有调用 make check"

    def test_workflow_passes_the_expected_ports_args(self):
        text = self._workflow_text()
        for flag in CI_PORTS_ARGS:
            assert flag in text, (
                f"仓库根 CI 没有给端口体检传 {flag}；runner 上没有 Docker，缺了它这一步会失败或退化"
            )

    def test_workflow_python_matrix_satisfies_requires_python(self):
        with (PACKAGE_ROOT / "pyproject.toml").open("rb") as handle:
            requires = tomllib.load(handle)["project"]["requires-python"]
        floor = tuple(int(part) for part in re.search(r"(\d+)\.(\d+)", requires).groups())

        text = self._workflow_text()
        block = re.search(r"(?s)\n  adas:\n.*?(?=\n  [a-z-]+:\n|\Z)", text)
        assert block, "CI 里找不到 adas job"
        versions = re.findall(r'"(\d+)\.(\d+)"', block.group(0))
        assert versions, "adas job 没有声明 Python 版本"
        for major, minor in versions:
            assert (int(major), int(minor)) >= floor, (
                f"CI 用 Python {major}.{minor} 跑 adas，低于 requires-python {requires}"
            )
