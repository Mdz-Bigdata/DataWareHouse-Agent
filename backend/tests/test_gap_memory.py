# -*- coding: utf-8 -*-
"""记忆资产缺口回归：可配置落盘路径、容器内持久化、不伪造历史、写盘失败可感知。

对应「智驾数据闭环湖仓 × DataWareHouse-Agent 对标技术报告」§5.2 #3′ 四条：
硬编码路径 / 容器内静默丢数 / 伪造演示历史 / 静默吞异常。
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.model import user_memory as memory_module
from app.model.user_memory import (
    DEFAULT_MEMORY_FILENAME,
    MemoryPersistenceError,
    UserMemory,
    demo_seed_records,
    resolve_storage_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class StoragePathResolutionTests(unittest.TestCase):
    """#1 硬编码开发机绝对路径 -> 可配置 + 合理默认。"""

    def test_module_source_has_no_hardcoded_developer_path(self):
        source = (BACKEND_ROOT / "app" / "model" / "user_memory.py").read_text(encoding="utf-8")
        self.assertNotIn("/Users/", source,
                         "记忆模块不得再出现开发机绝对路径，否则容器里必然写不进去")

    def test_default_path_is_backend_package_root(self):
        """默认值跟随代码所在目录：开发机上仍是 backend/user_memory.json，
        容器里（WORKDIR=/app）是 /app/user_memory.json —— 两边都是有效路径。"""
        resolved = resolve_storage_path(env={})
        self.assertEqual(Path(resolved), BACKEND_ROOT / DEFAULT_MEMORY_FILENAME)
        self.assertTrue(os.path.isabs(resolved))

    def test_explicit_path_env_wins(self):
        self.assertEqual(
            resolve_storage_path(env={"USER_MEMORY_PATH": "/app/data/user_memory.json"}),
            "/app/data/user_memory.json",
        )

    def test_directory_env_appends_default_filename(self):
        self.assertEqual(
            resolve_storage_path(env={"USER_MEMORY_DIR": "/var/lib/dwh"}),
            os.path.join("/var/lib/dwh", DEFAULT_MEMORY_FILENAME),
        )

    def test_explicit_path_beats_directory(self):
        resolved = resolve_storage_path(
            env={"USER_MEMORY_PATH": "/mnt/a.json", "USER_MEMORY_DIR": "/mnt/other"}
        )
        self.assertEqual(resolved, "/mnt/a.json")

    def test_blank_env_falls_back_to_default(self):
        """空字符串（compose 里很常见的 FOO=）不能把路径打成空。"""
        resolved = resolve_storage_path(env={"USER_MEMORY_PATH": "   ", "USER_MEMORY_DIR": ""})
        self.assertEqual(Path(resolved), BACKEND_ROOT / DEFAULT_MEMORY_FILENAME)

    def test_relative_and_user_paths_are_expanded(self):
        with patch.dict(os.environ, {"HOME": "/home/somebody"}):
            self.assertEqual(
                resolve_storage_path(env={"USER_MEMORY_PATH": "~/mem.json"}),
                "/home/somebody/mem.json",
            )

    def test_missing_parent_directory_is_created_on_save(self):
        """容器里挂载点之下可能还有层级；写盘要自己把目录建出来，不能报错了事。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "data", "nested", DEFAULT_MEMORY_FILENAME)
            memory = UserMemory(storage_path=target)
            memory.add_history("u", "问题", "SELECT 1", "postgres", "结论")
            self.assertTrue(os.path.exists(target))


class NoFabricatedHistoryTests(unittest.TestCase):
    """#3 文件不存在时塞伪造演示历史 -> 全新部署就是一段空历史。"""

    def test_fresh_deployment_starts_with_empty_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            self.assertEqual(memory.history, [])
            self.assertEqual(memory.custom_preferences, {})
            self.assertEqual(memory.error_corrections, [])

    def test_fabricated_gmv_figure_is_gone_from_default_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            blob = json.dumps(memory.history, ensure_ascii=False)
            for fabricated in ("1,234.50", "1,235", "张三"):
                self.assertNotIn(fabricated, blob,
                                 "编造的演示数字不得混进用户真实记忆")

    def test_fresh_deployment_writes_no_file_until_real_activity(self):
        """没有任何真实记忆时不该先落一个文件下去（import 期副作用）。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            UserMemory(storage_path=target)
            self.assertFalse(os.path.exists(target))

    def test_history_endpoint_view_is_empty_for_fresh_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            self.assertEqual(memory.get_history("张三"), [])
            self.assertEqual(memory.get_history("anonymous"), [])


class OptInDemoSeedTests(unittest.TestCase):
    """需要演示数据时必须显式开关 + 明确标记，且不冒充真实记忆。"""

    def test_demo_seed_is_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("USER_MEMORY_SEED_DEMO", None)
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            self.assertEqual(memory.history, [])

    def test_demo_seed_records_are_labelled_as_samples(self):
        for record in demo_seed_records():
            self.assertTrue(record["is_demo"])
            # schema 里没有 is_demo 字段，所以标记还必须出现在会透出到界面的文本里
            self.assertIn("示例", record["question"])
            self.assertIn("示例数据", record["result_summary"])
            self.assertIn("示例", record["user"])

    def test_demo_seed_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            memory = UserMemory(storage_path=target, seed_demo=True)
            self.assertEqual(len(memory.history), 2)
            self.assertTrue(all(r["is_demo"] for r in memory.history))

    def test_demo_seed_can_be_enabled_by_env(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"USER_MEMORY_SEED_DEMO": "true"}):
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            self.assertEqual(len(memory.history), 2)

    def test_real_records_are_never_marked_as_demo(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            record = memory.add_history("u", "真实问题", "SELECT 1", "postgres", "真实结论")
            self.assertNotIn("is_demo", record)


class SaveFailureIsVisibleTests(unittest.TestCase):
    """#4 print 吞异常 -> 真日志 + 调用方可感知。"""

    def _unwritable_path(self, tmp):
        """把父级做成一个普通文件，makedirs 必然失败 —— 与运行用户是不是 root 无关。"""
        blocker = os.path.join(tmp, "blocker")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        return os.path.join(blocker, DEFAULT_MEMORY_FILENAME)

    def test_save_failure_returns_false_and_records_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=self._unwritable_path(tmp))
            self.assertFalse(memory._save())
            self.assertIsNotNone(memory.last_save_error)
            self.assertIsNotNone(memory.last_save_error_at)
            self.assertEqual(memory.failed_save_count, 1)

    def test_save_failure_emits_real_log_not_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=self._unwritable_path(tmp))
            with self.assertLogs("app.model.user_memory", level="ERROR") as captured:
                memory._save()
        joined = "\n".join(captured.output)
        self.assertIn("用户记忆写入失败", joined)
        # exc_info=True 意味着堆栈也进了日志，而不是只有一行 message
        self.assertIn("Traceback", joined)

    def test_persistence_status_reports_unhealthy_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=self._unwritable_path(tmp))
            self.assertTrue(memory.persistence_status()["healthy"])
            memory.add_history("u", "问题", "SELECT 1", "postgres", "结论")
            status = memory.persistence_status()
            self.assertFalse(status["healthy"],
                             "写不进去却报告健康，就是在假装成功")
            self.assertEqual(status["failed_save_count"], 1)
            self.assertIn("storage_path", status)

    def test_status_recovers_after_a_successful_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=self._unwritable_path(tmp))
            memory.add_history("u", "问题", "SELECT 1", "postgres", "结论")
            self.assertFalse(memory.persistence_status()["healthy"])
            memory.storage_path = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            memory.add_history("u", "问题2", "SELECT 2", "postgres", "结论2")
            self.assertTrue(memory.persistence_status()["healthy"])
            self.assertIsNone(memory.last_save_error)

    def test_default_mode_does_not_break_the_ask_flow(self):
        """add_history 在问数主链路里是内联调用的：写盘失败不能把用户的答案也弄丢。"""
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=self._unwritable_path(tmp))
            record = memory.add_history("u", "问题", "SELECT 1", "postgres", "结论")
            self.assertEqual(record["question"], "问题")
            self.assertEqual(len(memory.history), 1)

    def test_strict_mode_raises_instead_of_pretending(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=self._unwritable_path(tmp), strict_persistence=True)
            with self.assertRaises(MemoryPersistenceError):
                memory.add_history("u", "问题", "SELECT 1", "postgres", "结论")

    def test_strict_mode_is_opt_in_via_env(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"USER_MEMORY_STRICT_PERSISTENCE": "true"}):
            memory = UserMemory(storage_path=self._unwritable_path(tmp))
            self.assertTrue(memory.strict_persistence)
            with self.assertRaises(MemoryPersistenceError):
                memory.add_error_correction("q", "err", "bad sql", "good sql")

    def test_strict_mode_defaults_to_off(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("USER_MEMORY_STRICT_PERSISTENCE", None)
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            self.assertFalse(memory.strict_persistence)


class DurableWriteTests(unittest.TestCase):
    """#2 的代码侧配套：写盘要原子，坏文件不能被悄悄覆盖。"""

    def test_round_trip_survives_a_fresh_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "data", DEFAULT_MEMORY_FILENAME)
            first = UserMemory(storage_path=target)
            first.add_history("u", "华东区昨天退款额", "SELECT 1", "postgres", "结论")
            first.add_error_correction("q", "boom", "bad", "good")
            first.update_preference_profile("u", {"common_metrics": [{"metric": "gmv", "count": 1}]})

            second = UserMemory(storage_path=target)
            self.assertEqual(len(second.history), 1)
            self.assertEqual(len(second.error_corrections), 1)
            self.assertEqual(second.get_history("u")[0]["question"], "华东区昨天退款额")
            self.assertIn("u", second.custom_preferences)

    def test_failed_write_leaves_previous_file_intact(self):
        """半截 JSON 比报错更难修：临时文件 + 原子替换，失败时原文件必须原样还在。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            memory = UserMemory(storage_path=target)
            memory.add_history("u", "第一条", "SELECT 1", "postgres", "结论")
            before = Path(target).read_text(encoding="utf-8")

            with patch.object(memory_module.json, "dump", side_effect=OSError("disk full")):
                self.assertFalse(memory._save())

            self.assertEqual(Path(target).read_text(encoding="utf-8"), before)
            self.assertEqual(json.loads(before)["history"][0]["question"], "第一条")

    def test_failed_write_leaves_no_temp_files_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UserMemory(storage_path=os.path.join(tmp, DEFAULT_MEMORY_FILENAME))
            with patch.object(memory_module.json, "dump", side_effect=OSError("disk full")):
                memory._save()
            leftovers = [n for n in os.listdir(tmp) if n.startswith(".user_memory-")]
            self.assertEqual(leftovers, [])

    def test_corrupt_file_is_quarantined_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            Path(target).write_text('{"history": [{"id": 1, ', encoding="utf-8")

            with self.assertLogs("app.model.user_memory", level="ERROR"):
                memory = UserMemory(storage_path=target)

            self.assertEqual(memory.history, [])
            self.assertIsNotNone(memory.load_error)
            self.assertFalse(memory.persistence_status()["healthy"])
            backups = [n for n in os.listdir(tmp) if ".corrupt-" in n]
            self.assertEqual(len(backups), 1, "损坏的记忆文件必须先存档再重来")
            self.assertIn('{"history"', Path(tmp, backups[0]).read_text(encoding="utf-8"))

    def test_legacy_list_shaped_file_still_loads(self):
        """旧格式（顶层是 list）是既有契约，不能因为这次改动读不出来。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            Path(target).write_text(
                json.dumps([{"id": 1, "user": "u", "question": "q", "sql": "SELECT 1",
                             "dialect": "postgres", "execution_time": "0.01s",
                             "result_summary": "s", "created_at": "2026-01-01 00:00:00"}],
                           ensure_ascii=False),
                encoding="utf-8")
            memory = UserMemory(storage_path=target)
            self.assertEqual(len(memory.history), 1)
            self.assertEqual(memory.get_history("u")[0]["question"], "q")

    def test_records_missing_fields_do_not_crash_history_or_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, DEFAULT_MEMORY_FILENAME)
            Path(target).write_text(
                json.dumps({"history": [{"id": 1}, {"id": 2, "user": "u"}],
                            "custom_preferences": {}, "error_corrections": []},
                           ensure_ascii=False),
                encoding="utf-8")
            memory = UserMemory(storage_path=target)
            self.assertEqual(len(memory.get_history("u")), 1)
            self.assertEqual(memory.get_preference_profile("u")["user"], "u")


def _compose_block(text, header, indent):
    """按缩进抠出 compose.yaml 里的一段。

    故意不依赖 pyyaml：它不在 backend/requirements.txt 里，为了一条断言
    给生产后端加依赖不划算，而这里只需要认出固定形状的几行。
    """
    lines = text.splitlines()
    pad = " " * indent
    out = []
    collecting = False
    for line in lines:
        if not collecting:
            if line == f"{pad}{header}:":
                collecting = True
            continue
        if line.strip() and not line.startswith(pad + " "):
            break
        out.append(line)
    return out


class ComposePersistenceTests(unittest.TestCase):
    """#2 容器内静默丢数：core-backend 必须有持久化卷。"""

    @classmethod
    def setUpClass(cls):
        cls.compose_path = REPO_ROOT / "compose.yaml"
        cls.text = cls.compose_path.read_text(encoding="utf-8")
        cls.service = _compose_block(cls.text, "core-backend", 2)
        mounts = _compose_block("\n".join(cls.service), "volumes", 4)
        cls.mounts = [m.strip().lstrip("- ").strip() for m in mounts if m.strip().startswith("- ")]
        env = _compose_block("\n".join(cls.service), "environment", 4)
        cls.env = dict(
            (k.strip(), v.strip())
            for k, _, v in (line.partition(":") for line in env if ":" in line)
        )

    def test_compose_file_is_where_expected(self):
        self.assertTrue(self.compose_path.exists())
        self.assertTrue(self.service, "compose.yaml 里找不到 core-backend 段")

    def test_core_backend_has_a_persistent_volume(self):
        self.assertTrue(self.mounts, "core-backend 没有 volumes，容器重建=用户记忆全丢")
        self.assertTrue(any(m.startswith("core-backend-data:") for m in self.mounts),
                        f"未挂载命名卷 core-backend-data：{self.mounts}")

    def test_named_volume_is_declared(self):
        declared = _compose_block(self.text, "volumes", 0)
        names = [line.strip().rstrip(":") for line in declared if line.strip()]
        self.assertIn("core-backend-data", names)

    def test_memory_path_points_into_the_mounted_volume(self):
        configured = self.env.get("USER_MEMORY_PATH", "")
        self.assertIn("/app/data/user_memory.json", configured)
        targets = [m.split(":")[1] for m in self.mounts]
        # 形如 ${CORE_USER_MEMORY_PATH:-/app/data/user_memory.json}，取其默认值
        match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-(?P<default>[^}]*)\}", configured)
        resolved = match.group("default") if match else configured
        self.assertTrue(any(resolved.startswith(t.rstrip("/") + "/") for t in targets),
                        f"记忆文件 {resolved} 不在挂载卷 {targets} 之下，挂了卷也白挂")
        self.assertEqual(resolved, "/app/data/user_memory.json")

    def test_volume_does_not_shadow_application_code(self):
        """挂在 /app 会盖掉 Dockerfile COPY 进去的代码，服务直接起不来。"""
        for mount in self.mounts:
            self.assertNotIn(mount.split(":")[1], ("/app", "/app/app"))

    def test_backend_dockerfile_does_not_bake_memory_into_the_image(self):
        dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("user_memory.json", dockerfile,
                         "运行时数据不应被 COPY 进镜像层")


if __name__ == "__main__":
    unittest.main()
