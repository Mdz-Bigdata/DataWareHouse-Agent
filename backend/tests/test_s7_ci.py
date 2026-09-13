# -*- coding: utf-8 -*-
"""§7.9-3 CI 基建 + 硬编码绝对路径清理 的回归测试。

覆盖三块：

1. ``app.core.paths``——路径解析本身（包结构推导 + 环境变量覆盖 + 越权拦截）；
2. 四个历史调用点确实改用了它，且源码里不再有开发机绝对路径；
3. CI 配置自洽——workflow 里用的 marker 必须在根 pytest.ini 里声明过，
   Makefile 与 workflow 的过滤条件必须一致，密钥扫描器行为正确。

第 3 块是「配置漂移」这一类 bug 的闸门：marker 打错字会被 --strict-markers
直接拒掉，而 Makefile 和 workflow 各写一份过滤条件则会悄悄分叉。
"""
import importlib
import importlib.util
import os
import random
import re
import string
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import paths as paths_mod
from app.core.paths import (
    ENV_BACKEND_DIR,
    ENV_LLM_CONFIG_PATH,
    ENV_REPO_ROOT,
    PathEscapeError,
    backend_dir,
    llm_config_path,
    repo_root,
    resolve_within,
)

REPO_ROOT = repo_root()
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYTEST_INI = REPO_ROOT / "pytest.ini"
ROOT_MAKEFILE = REPO_ROOT / "Makefile"
SCANNER = REPO_ROOT / ".github" / "scripts" / "scan_secrets.py"

# 任务点名的 5 处硬编码绝对路径所在文件（相对 backend/）。
PATCHED_SOURCES = (
    "app/api/llm.py",
    "app/api/developer.py",
    "app/service/dev_agent_coordinator.py",
    "app/service/ask_agent.py",
)

# 开发机绝对路径的形状：/Users/<name>/ 或 /home/<name>/
DEV_ABSOLUTE_PATH = re.compile(r"(/Users/|/home/)[A-Za-z0-9._-]+/")


def _synthetic_secret() -> str:
    """造一个「形状像真密钥」的字符串，但**不在源码里留下高熵字面量**。

    直接写一个假 Key 会被 scan_secrets.py 判为泄露——而且那判定是对的：
    扫描器无从分辨「测试夹具」和「真的忘了删」。固定随机种子保证可复现。
    """
    alphabet = string.ascii_lowercase + string.ascii_uppercase + string.digits
    rng = random.Random(20250913)
    return "sk-" + "".join(rng.choice(alphabet) for _ in range(32))


def _load_scanner():
    """把 .github/scripts/scan_secrets.py 当模块加载（目录名不是合法包名）。"""
    spec = importlib.util.spec_from_file_location("_scan_secrets_under_test", SCANNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# 1. 路径解析
# =============================================================================
class PathResolutionTests(unittest.TestCase):
    def test_defaults_are_derived_from_package_layout(self):
        # backend/app/core/paths.py 必须能推出 backend 与仓库根，且两者是父子关系。
        self.assertTrue((backend_dir() / "app" / "core" / "paths.py").is_file())
        self.assertEqual(backend_dir().parent, repo_root())
        self.assertEqual(llm_config_path(), backend_dir() / "llm_config.json")

    def test_every_path_is_absolute(self):
        for value in (backend_dir(), repo_root(), llm_config_path()):
            self.assertTrue(value.is_absolute(), f"{value} 不是绝对路径")

    def test_environment_overrides_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_resolved = Path(tmp).resolve()
            with patch.dict(os.environ, {ENV_REPO_ROOT: tmp}):
                self.assertEqual(repo_root(), tmp_resolved)
            with patch.dict(os.environ, {ENV_BACKEND_DIR: tmp}):
                self.assertEqual(backend_dir(), tmp_resolved)
                # llm_config_path 跟随 backend_dir，而不是各算各的。
                self.assertEqual(llm_config_path(), tmp_resolved / "llm_config.json")
            override = str(Path(tmp) / "custom" / "cfg.json")
            with patch.dict(os.environ, {ENV_LLM_CONFIG_PATH: override}):
                self.assertEqual(llm_config_path(), Path(override).resolve())

    def test_blank_environment_variable_is_not_an_override(self):
        # 容器里常见 `ENV DWH_REPO_ROOT=`，空值必须回落到推导值而不是 Path("")。
        for blank in ("", "   "):
            with patch.dict(os.environ, {ENV_REPO_ROOT: blank}):
                self.assertEqual(repo_root(), paths_mod._DEFAULT_REPO_ROOT)

    def test_paths_are_recomputed_per_call_not_frozen_at_import(self):
        # 若实现把值在 import 时算死，测试里的 patch.dict 就永远无效——显式钉住。
        with tempfile.TemporaryDirectory() as tmp:
            before = repo_root()
            with patch.dict(os.environ, {ENV_REPO_ROOT: tmp}):
                during = repo_root()
            after = repo_root()
        self.assertEqual(before, after)
        self.assertNotEqual(before, during)


class ResolveWithinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.base = self.root / "proj"
        (self.base / "init" / "doris").mkdir(parents=True)
        (self.base / "init" / "doris" / "t.sql").write_text("SELECT 1", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def test_accepts_paths_inside_base(self):
        got = resolve_within(self.base, "init/doris/t.sql")
        self.assertEqual(got, self.base / "init" / "doris" / "t.sql")

    def test_accepts_not_yet_existing_paths_inside_base(self):
        # POST /developer/file 会写还不存在的文件，不能因为「不存在」就拒。
        got = resolve_within(self.base, "etl/mysql/new.sql")
        self.assertEqual(got, self.base / "etl" / "mysql" / "new.sql")

    def test_rejects_parent_traversal(self):
        with self.assertRaises(PathEscapeError):
            resolve_within(self.base, "../outside.txt")
        with self.assertRaises(PathEscapeError):
            resolve_within(self.base, "init/../../outside.txt")

    def test_rejects_absolute_paths(self):
        with self.assertRaises(PathEscapeError):
            resolve_within(self.base, "/etc/passwd")

    def test_rejects_sibling_prefix_bypass(self):
        """旧实现用 ``normpath(join(...)).startswith(base)``——纯字符串前缀判断。

        ``<base>-evil/secret`` 归一化后确实以 ``<base>`` 开头，会被误放行。
        这是被替换掉的那条代码路径的真实缺陷，必须钉死。
        """
        evil = self.root / "proj-evil"
        evil.mkdir()
        (evil / "secret").write_text("leak", encoding="utf-8")

        escaped = os.path.normpath(os.path.join(str(self.base), "../proj-evil/secret"))
        self.assertTrue(
            escaped.startswith(str(self.base)),
            "前置条件不成立：这条路径本应能骗过 startswith 写法",
        )
        with self.assertRaises(PathEscapeError):
            resolve_within(self.base, "../proj-evil/secret")

    def test_rejects_symlink_pointing_outside_base(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_text("leak", encoding="utf-8")
        link = self.base / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("当前平台不支持创建符号链接")
        with self.assertRaises(PathEscapeError):
            resolve_within(self.base, "escape/secret")

    def test_base_itself_is_allowed(self):
        self.assertEqual(resolve_within(self.base, "."), self.base)


# =============================================================================
# 2. 调用点确实改掉了
# =============================================================================
class HardcodedPathCleanupTests(unittest.TestCase):
    def test_named_sources_have_no_dev_absolute_paths(self):
        offenders = []
        for rel in PATCHED_SOURCES:
            text = (backend_dir() / rel).read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if DEV_ABSOLUTE_PATH.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "这些行仍写死了开发机绝对路径：\n" + "\n".join(offenders))

    def test_whole_backend_app_tree_is_clean(self):
        offenders = []
        for path in sorted((backend_dir() / "app").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if DEV_ABSOLUTE_PATH.search(line):
                    rel = path.relative_to(backend_dir())
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "backend/app 下仍有硬编码绝对路径：\n" + "\n".join(offenders))

    def test_llm_config_path_constant_tracks_the_helper(self):
        from app.api import llm

        self.assertEqual(llm.CONFIG_PATH, str(llm_config_path()))
        self.assertIsInstance(llm.CONFIG_PATH, str, "契约不变：CONFIG_PATH 仍是 str")

    def test_llm_config_path_constant_follows_environment_override(self):
        """容器部署把配置挂到别处时，重新 import 必须拿到新路径。"""
        from app.api import llm

        original = llm.CONFIG_PATH
        with tempfile.TemporaryDirectory() as tmp:
            override = str(Path(tmp) / "cfg.json")
            try:
                with patch.dict(os.environ, {ENV_LLM_CONFIG_PATH: override}):
                    reloaded = importlib.reload(llm)
                    self.assertEqual(reloaded.CONFIG_PATH, str(Path(override).resolve()))
            finally:
                # 恢复模块状态，避免污染同进程里的其它用例。
                restored = importlib.reload(llm)
        self.assertEqual(restored.CONFIG_PATH, original)

    def test_dev_agent_coordinator_workspace_follows_environment_override(self):
        from app.service.dev_agent_coordinator import DevAgentCoordinator

        self.assertEqual(DevAgentCoordinator().workspace_dir, str(repo_root()))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {ENV_REPO_ROOT: tmp}):
                self.assertEqual(
                    DevAgentCoordinator().workspace_dir, str(Path(tmp).resolve())
                )

    def test_ask_agent_resolves_config_through_the_helper(self):
        source = (backend_dir() / "app" / "service" / "ask_agent.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("llm_config_path()", source)
        self.assertNotIn("llm_config.json\"", source.replace("example", ""))


class DeveloperEndpointSandboxTests(unittest.TestCase):
    """/developer/file 的越权拦截——收紧后仍必须拒掉原来能拒的一切。"""

    def _http_status(self, callable_, **kwargs):
        from fastapi import HTTPException

        try:
            callable_(**kwargs)
        except HTTPException as exc:
            return exc.status_code
        return None

    def test_read_rejects_traversal_and_absolute_paths(self):
        from app.api.developer import get_file_content

        for bad in ("../../etc/passwd", "/etc/passwd", "init/../../../etc/passwd"):
            with self.subTest(path=bad):
                self.assertEqual(
                    self._http_status(get_file_content, path=bad),
                    403,
                    f"{bad} 应被拒为 403",
                )

    def test_write_rejects_traversal_and_absolute_paths(self):
        from app.api.developer import update_file_content

        for bad in ("../../evil.sql", "/tmp/evil.sql"):
            with self.subTest(path=bad):
                self.assertEqual(
                    self._http_status(update_file_content, path=bad, content="x"),
                    403,
                    f"{bad} 应被拒为 403",
                )

    def test_read_of_missing_in_tree_file_is_404_not_403(self):
        """合法但不存在的仓库内路径要落到 404，否则说明沙箱判定过紧、把正常请求也拦了。"""
        from app.api.developer import get_file_content

        self.assertEqual(
            self._http_status(get_file_content, path="init/doris/__no_such_file__.sql"),
            404,
        )


# =============================================================================
# 3. CI 配置自洽
# =============================================================================
@unittest.skipUnless(WORKFLOW.is_file(), "未找到 .github/workflows/ci.yml")
class WorkflowContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_the_documented_backend_baseline_command(self):
        self.assertIn("python -m pytest tests/ -q", self.text)
        self.assertIn("PYTHONPATH", self.text)
        self.assertIn("working-directory: backend", self.text)

    def test_runs_adas_make_check(self):
        self.assertIn("make check", self.text)
        self.assertIn("working-directory: apps/adas-closed-loop-lakehouse", self.text)

    def test_runs_the_secret_scan(self):
        self.assertIn("scan_secrets.py", self.text)
        self.assertIn("--forbid-present", self.text)

    def test_guards_both_leaked_runtime_files(self):
        for denied in ("backend/llm_config.json", "backend/user_memory.json"):
            self.assertIn(denied, self.text, f"CI 未防护 {denied}")
        self.assertIn("git ls-files --error-unmatch", self.text)

    def test_is_valid_yaml_when_a_parser_is_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("未安装 PyYAML，跳过结构解析（文本断言已覆盖关键内容）")
        parsed = yaml.safe_load(self.text)
        self.assertIn("jobs", parsed)
        for job_name in ("backend", "adas", "secret-scan"):
            self.assertIn(job_name, parsed["jobs"], f"缺少 job: {job_name}")
        for job_name, job in parsed["jobs"].items():
            self.assertTrue(job.get("steps"), f"job {job_name} 没有任何 step")


@unittest.skipUnless(PYTEST_INI.is_file(), "未找到根 pytest.ini")
class RootPytestConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import configparser

        cls.parser = configparser.ConfigParser()
        cls.parser.read(PYTEST_INI, encoding="utf-8")

    def declared_markers(self):
        raw = self.parser.get("pytest", "markers", fallback="")
        return {
            line.split(":", 1)[0].strip()
            for line in raw.splitlines()
            if line.strip()
        }

    def test_declares_the_external_dependency_markers(self):
        declared = self.declared_markers()
        for marker in ("requires_db", "requires_docker", "requires_network"):
            self.assertIn(marker, declared, f"根 pytest.ini 未声明 marker: {marker}")

    def test_strict_markers_is_enabled(self):
        addopts = self.parser.get("pytest", "addopts", fallback="")
        self.assertIn("--strict-markers", addopts)

    def test_testpaths_cover_both_first_party_suites(self):
        testpaths = self.parser.get("pytest", "testpaths", fallback="")
        self.assertIn("backend/tests", testpaths)
        self.assertIn("tests/platform", testpaths)

    @unittest.skipUnless(WORKFLOW.is_file(), "未找到 CI workflow")
    def test_every_marker_used_by_ci_is_declared(self):
        """CI 的 -m 过滤里出现未声明的 marker，--strict-markers 会让整条 CI 直接报错。"""
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        used = set(re.findall(r"not\s+(requires_[a-z_]+)", workflow_text))
        self.assertTrue(used, "CI 里没找到任何 marker 过滤条件")
        undeclared = used - self.declared_markers()
        self.assertEqual(undeclared, set(), f"CI 用了未声明的 marker：{sorted(undeclared)}")


@unittest.skipUnless(ROOT_MAKEFILE.is_file(), "未找到根 Makefile")
class RootMakefileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = ROOT_MAKEFILE.read_text(encoding="utf-8")

    def test_exposes_the_expected_targets(self):
        for target in ("test-backend", "test-platform", "test-adas", "scan-secrets", "ci"):
            # (?m) 必须有：assertRegex 走 re.search，没有 MULTILINE 时 ^ 只锚文件开头。
            self.assertRegex(
                self.text, rf"(?m)^{re.escape(target)}:", f"Makefile 缺少目标 {target}"
            )

    @unittest.skipUnless(WORKFLOW.is_file(), "未找到 CI workflow")
    def test_marker_filter_matches_the_workflow(self):
        """两处各写一份过滤条件，最容易悄悄分叉——用集合比对钉住。"""
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        in_workflow = set(re.findall(r"not\s+(requires_[a-z_]+)", workflow_text))
        in_makefile = set(re.findall(r"not\s+(requires_[a-z_]+)", self.text))
        self.assertEqual(
            in_workflow,
            in_makefile,
            f"Makefile 与 workflow 的 marker 过滤不一致："
            f"workflow={sorted(in_workflow)} makefile={sorted(in_makefile)}",
        )


@unittest.skipUnless(SCANNER.is_file(), "未找到 .github/scripts/scan_secrets.py")
class SecretScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = _load_scanner()
        cls.secret = _synthetic_secret()
        # 夹具自校验：夹具若不再被判定为「像密钥」，下面几条检出测试就会空转通过，
        # 那比失败更危险——所以这里先把前提钉死。
        assert cls.scanner.is_probable_secret(cls.secret.removeprefix("sk-"), 20), (
            "合成夹具已不再满足检出条件，检出类测试会变成假阳性"
        )

    def _fixture_repo(self, tmp: str, files: dict, gitignore_extra=()):
        root = Path(tmp)
        lines = list(self.scanner.DENYLIST) + list(gitignore_extra)
        (root / ".gitignore").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for rel, body in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        return root

    def test_flags_a_planted_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture_repo(
                tmp, {"svc/client.py": f'API_KEY = "{self.secret}"\n'}
            )
            problems, _ = self.scanner.run(root, forbid_present=False)
            self.assertTrue(problems, "植入的密钥没有被发现")
            self.assertTrue(any("svc/client.py" in p for p in problems))

    def test_does_not_flag_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture_repo(
                tmp,
                {
                    "README.md": "api_key: sk-your-deepseek-api-key-here\n",
                    "cfg.example.json": '{"api_key": ""}\n',
                    "compose.yaml": 'API_KEY: "${OPENAI_API_KEY}"\n',
                    "doc.md": "api_key = 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx'\n",
                },
            )
            problems, _ = self.scanner.run(root, forbid_present=False)
            self.assertEqual(problems, [], f"占位符被误报：{problems}")

    def test_never_echoes_the_secret_in_full(self):
        secret = self.secret
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture_repo(tmp, {"svc/c.py": f'API_KEY = "{secret}"\n'})
            problems, _ = self.scanner.run(root, forbid_present=False)
            joined = "\n".join(problems)
            self.assertNotIn(secret, joined, "报告里回显了完整密钥，等于二次泄露")

    def test_forbid_present_catches_tracked_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture_repo(tmp, {"backend/llm_config.json": '{"a": 1}\n'})
            without, _ = self.scanner.run(root, forbid_present=False)
            self.assertEqual(without, [], "未开 --forbid-present 时不该因文件存在而失败")
            with_flag, _ = self.scanner.run(root, forbid_present=True)
            self.assertTrue(any("llm_config.json" in p for p in with_flag))

    def test_missing_gitignore_entry_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            problems = self.scanner.check_gitignore_coverage(root)
            self.assertEqual(len(problems), len(self.scanner.DENYLIST))

    def test_allowlist_downgrades_to_warning_and_flags_stale_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture_repo(
                tmp, {"legacy.py": f'API_KEY = "{self.secret}"\n'}
            )
            allow = root / self.scanner.ALLOWLIST_FILE
            allow.parent.mkdir(parents=True, exist_ok=True)

            allow.write_text("legacy.py  # 历史债\n", encoding="utf-8")
            problems, warnings = self.scanner.run(root, forbid_present=False)
            self.assertEqual(problems, [])
            self.assertTrue(any("legacy.py" in w for w in warnings))

            # 豁免了一个根本不命中的文件 -> 陈旧条目，必须报出来逼人删掉。
            allow.write_text("legacy.py  # x\nghost.py  # 早就修好了\n", encoding="utf-8")
            problems, _ = self.scanner.run(root, forbid_present=False)
            self.assertTrue(any("ghost.py" in p and "陈旧" in p for p in problems))

    def test_real_repository_passes_the_content_scan(self):
        """仓库当前状态必须通过（豁免清单生效）——这是 CI 的实际判据。"""
        result = subprocess.run(
            [sys.executable, str(SCANNER), "--root", str(REPO_ROOT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0, f"密钥扫描未通过：\n{result.stdout}\n{result.stderr}"
        )

    def test_gitignore_covers_the_leaked_runtime_files(self):
        problems = self.scanner.check_gitignore_coverage(REPO_ROOT)
        self.assertEqual(problems, [], f"真实 .gitignore 覆盖不全：{problems}")


if __name__ == "__main__":
    unittest.main()
