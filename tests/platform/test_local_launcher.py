"""Launcher orchestration checks; no Docker engines or application processes are started."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "start_local", Path(__file__).resolve().parents[2] / "tools/start_local.py",
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
WAREHOUSE_CONFIG = {"WAREHOUSE_POSTGRES_PASSWORD": "warehouse-test-secret"}
WAREHOUSE_URL = launcher.warehouse_url({}, WAREHOUSE_CONFIG)
WAREHOUSE_HEALTH = {
    "service": "DataWareHouse-Agent Backend", "db_type": "postgresql", "data_source": "configured",
    "data_origin": "project_fixture", "database_identity": launcher.database_identity(WAREHOUSE_URL),
}


class ConfiguredLauncherTest(unittest.TestCase):
    def setUp(self):
        # The repository .env selects a real business database on developer
        # machines; launcher behaviour is asserted against declared inputs only.
        for patcher in (patch.object(launcher, "platform_config", return_value=WAREHOUSE_CONFIG),
                        patch.object(launcher, "read_env_file", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)


class LauncherConfigurationTests(ConfiguredLauncherTest):
    def test_postgres_default_does_not_modify_existing_business_environment(self):
        original = {"DB_TYPE": "postgres", "DB_URL": "existing-business-url", "DATABASE_URL": "other-business-url"}
        result = launcher.backend_environment(original)
        self.assertEqual(result["DB_TYPE"], "postgresql")
        self.assertEqual(result["DB_URL"], WAREHOUSE_URL)
        self.assertEqual(result["DATABASE_URL"], WAREHOUSE_URL)
        self.assertEqual(original["DB_TYPE"], "postgres")
        self.assertEqual(original["DATABASE_URL"], "other-business-url")

    def test_explicit_business_override_is_honored(self):
        result = launcher.backend_environment({
            "CORE_DB_TYPE": "postgres", "CORE_DB_URL": "selected-business-url",
            "DB_URL": "old-url", "DATABASE_URL": "stale-prioritized-url",
        })
        self.assertEqual(result["DB_TYPE"], "postgresql")
        self.assertEqual(result["DB_URL"], "selected-business-url")
        self.assertEqual(result["DATABASE_URL"], "selected-business-url")
        self.assertFalse(launcher.uses_managed_warehouse({"CORE_DB_TYPE": "postgres"}))

    def test_core_url_only_is_a_business_override_and_never_requests_seed_migration(self):
        original = {"CORE_DB_URL": "postgresql://user@business:5432/articles"}
        self.assertFalse(launcher.uses_managed_warehouse(original))
        self.assertEqual(launcher.backend_environment(original)["DATABASE_URL"], original["CORE_DB_URL"])

    def test_env_file_business_source_replaces_the_managed_warehouse(self):
        settings = {"DB_TYPE": "postgresql",
                    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/blog_converter"}
        with patch.object(launcher, "read_env_file", return_value=settings):
            self.assertFalse(launcher.uses_managed_warehouse({}))
            env = launcher.backend_environment({})
            self.assertEqual(env["DB_TYPE"], "postgresql")
            self.assertEqual(env["DATABASE_URL"], settings["DATABASE_URL"])
            self.assertEqual(env["DB_URL"], settings["DATABASE_URL"])
            self.assertEqual(launcher.demo_schema_target(env), settings["DATABASE_URL"])
            self.assertEqual(
                launcher.backend_environment({"CORE_DB_TYPE": "sqlite"})["DB_TYPE"], "sqlite")

    def test_env_file_without_a_usable_source_keeps_the_managed_warehouse(self):
        for settings in ({}, {"DB_TYPE": "sqlite", "DATABASE_URL": "x"}, {"DB_TYPE": "postgresql"}):
            with self.subTest(settings=settings), patch.object(launcher, "read_env_file", return_value=settings):
                self.assertTrue(launcher.uses_managed_warehouse({}))
                self.assertEqual(launcher.backend_environment({})["DATABASE_URL"], WAREHOUSE_URL)

    def test_demo_schema_is_never_targeted_at_a_non_postgres_source(self):
        self.assertIsNone(launcher.demo_schema_target(
            {"DB_TYPE": "mysql", "DATABASE_URL": "mysql+pymysql://user@host:3306/db"}))
        self.assertIsNone(launcher.demo_schema_target({"DB_TYPE": "sqlite"}))

    def test_warehouse_url_escapes_credentials_and_checks_local_port(self):
        value = launcher.warehouse_url({"WAREHOUSE_POSTGRES_PASSWORD": "p@ss:/# ?"})
        self.assertIn("p%40ss%3A%2F%23%20%3F@127.0.0.1:55432/datawarehouse", value)
        with self.assertRaisesRegex(launcher.StartupError, "端口"):
            launcher.warehouse_url({"WAREHOUSE_POSTGRES_PORT": "0"})

    def test_database_identity_ignores_password_but_distinguishes_other_databases(self):
        self.assertEqual(launcher.database_identity("postgresql://warehouse:a@localhost/datawarehouse"),
                         launcher.database_identity("postgresql+psycopg2://warehouse:b@localhost:5432/datawarehouse"))
        self.assertNotEqual(launcher.database_identity(WAREHOUSE_URL),
                            launcher.database_identity(WAREHOUSE_URL.replace("datawarehouse", "articles")))

    def test_gateway_reaches_host_backend_and_enables_both_native_apps(self):
        result = launcher.compose_environment({"UNRELATED": "retained"})
        self.assertEqual(result["PLATFORM_CORE_URL"], "http://host.docker.internal:8000")
        self.assertEqual(result["PLATFORM_DATA_API_ENABLED"], "true")
        self.assertEqual(result["PLATFORM_AGENTS_ENABLED"], "true")
        self.assertEqual(result["PLATFORM_AUDIO_ENABLED"], "false")
        self.assertEqual(result["UNRELATED"], "retained")
        self.assertEqual(launcher.SERVICES, ("platform-gateway", "data-api", "agents"))

    def test_only_the_selected_desktop_engine_is_started(self):
        available = {"Docker", "OrbStack"}
        self.assertEqual(launcher.docker_application("orbstack", "unix:///sock", available), "OrbStack")
        self.assertEqual(launcher.docker_application("desktop-linux", "unix:///sock", available), "Docker")
        self.assertIsNone(launcher.docker_application("production", "ssh://remote", available))
        self.assertIsNone(launcher.docker_application("default", "tcp://remote:2375", available))
        self.assertIsNone(launcher.docker_application("orbstack", "unix:///sock", {"Docker"}))


class ExistingServiceTests(ConfiguredLauncherTest):
    @patch.object(launcher, "listener_pids", return_value={123})
    @patch.object(launcher, "process_in_directory", return_value=False)
    @patch.object(launcher.os, "kill")
    def test_foreign_port_owner_is_not_killed(self, kill, *_):
        with self.assertRaisesRegex(launcher.StartupError, "被其他程序占用"):
            launcher.Launcher().check_native_port("后端", 8000, launcher.ROOT / "backend", "unused")
        kill.assert_not_called()

    @patch.object(launcher, "listener_pids", return_value={123})
    @patch.object(launcher, "process_in_directory", return_value=True)
    @patch.object(launcher, "read_url", return_value=b'{"status":"healthy"}')
    @patch.object(launcher, "json_url", return_value=WAREHOUSE_HEALTH)
    def test_healthy_same_project_backend_is_reused(self, *_):
        with patch.dict(launcher.os.environ, {}, clear=True):
            self.assertTrue(launcher.Launcher().check_native_port(
                "后端", 8000, launcher.ROOT / "backend", "unused",
            ))

    @patch.object(launcher, "listener_pids", return_value={123})
    @patch.object(launcher, "process_in_directory", return_value=True)
    @patch.object(launcher, "read_url", return_value=b'{"status":"healthy"}')
    @patch.object(launcher, "json_url", return_value={
        "service": "DataWareHouse-Agent Backend", "db_type": "postgres", "data_source": "configured",
    })
    def test_existing_wrong_datasource_cannot_be_reported_as_demo(self, *_):
        with patch.dict(launcher.os.environ, {}, clear=True):
            with self.assertRaisesRegex(launcher.StartupError, "本次要求 postgresql"):
                launcher.Launcher().check_native_port("后端", 8000, launcher.ROOT / "backend", "unused")


    @patch.object(launcher, "listener_pids", return_value={123})
    @patch.object(launcher, "process_in_directory", return_value=True)
    @patch.object(launcher, "read_url", return_value=b'{"status":"healthy"}')
    def test_legacy_or_ambiguous_source_health_is_never_reused(self, *_):
        for fields in ({}, {"db_type": "sqlite"}, {"db_type": "sqlite", "data_source": "configured"}):
            with self.subTest(fields=fields), patch.dict(launcher.os.environ, {}, clear=True):
                with patch.object(launcher, "json_url", return_value={
                    "service": "DataWareHouse-Agent Backend", **fields,
                }):
                    with self.assertRaisesRegex(launcher.StartupError, r"原启动终端按 Ctrl\+C"):
                        launcher.Launcher().check_native_port("后端", 8000, launcher.ROOT / "backend", "unused")

    def test_same_postgres_type_cannot_reuse_an_articles_only_business_database(self):
        wrong = {**WAREHOUSE_HEALTH, "database_identity": launcher.database_identity(
            "postgresql://warehouse@localhost:5432/articles"), "data_origin": "business"}
        problem = launcher.backend_health_error(wrong, launcher.backend_environment({}))
        self.assertIn("不同数据库", problem)

    def test_correct_database_requires_successful_seed_provenance(self):
        wrong = {**WAREHOUSE_HEALTH, "data_origin": "business"}
        problem = launcher.backend_health_error(wrong, launcher.backend_environment({}))
        self.assertIn("迁移完成", problem)


class DependencyTests(ConfiguredLauncherTest):
    def test_failed_initial_install_is_repaired_on_the_next_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "backend/venv/bin/python"
            instance = launcher.Launcher()
            installs = []

            def command(args, **_kwargs):
                if args[1:3] == ["-m", "venv"]:
                    python.parent.mkdir(parents=True)
                    python.touch()
                elif args[1:3] == ["-m", "pip"]:
                    installs.append(args)
                    if len(installs) == 1:
                        raise launcher.StartupError("interrupted pip install")

            instance.command = Mock(side_effect=command)
            with patch.object(launcher, "ROOT", root), patch.object(launcher, "output", return_value=""):
                with self.assertRaisesRegex(launcher.StartupError, "interrupted pip"):
                    instance.ensure_dependencies(True, False)
                self.assertTrue(python.exists())
                instance.ensure_dependencies(True, False)
            self.assertEqual(len(installs), 2)
            self.assertEqual(instance.command.call_args.args[0], [str(python), "-c", launcher.BACKEND_IMPORT_PROBE])
            instance.command.reset_mock()
            with patch.object(launcher, "ROOT", root), patch.object(launcher, "output", return_value="dependencies-ready"):
                instance.ensure_dependencies(True, False)
            instance.command.assert_not_called()


class LifecycleTests(ConfiguredLauncherTest):
    @patch.object(launcher.subprocess, "run")
    def test_compose_json_formats_are_supported_and_other_profiles_are_ignored(self, run):
        instance = launcher.Launcher()
        instance.compose = ["docker", "compose"]
        run.return_value = Mock(returncode=0, stdout='[{"ID":"data","Service":"data-api"},{"ID":"audio","Service":"audio"}]')
        self.assertEqual(instance.running_containers(), {"data": "data-api"})
        run.return_value.stdout = '{"ID":"agents","Service":"agents"}\n{"ID":"mysql","Service":"mysql"}\n'
        self.assertEqual(instance.running_containers(), {"agents": "agents", "mysql": "mysql"})
        run.return_value.stdout = '{"unknown":"container"}'
        with self.assertRaises(launcher.StartupError):
            instance.running_containers()

    @patch.object(launcher, "find_docker", return_value="docker")
    @patch.object(launcher, "output", side_effect=["production", "ssh://production"])
    def test_remote_docker_is_not_modified(self, *_):
        instance = launcher.Launcher()
        instance.command = Mock()
        with patch.dict(launcher.os.environ, {}, clear=True):
            with self.assertRaisesRegex(launcher.StartupError, "远程或未知引擎"):
                instance.ensure_docker()
        instance.command.assert_not_called()

    @patch.object(launcher.shutil, "which", return_value="/usr/bin/lsof")
    @patch.object(launcher.time, "sleep", side_effect=KeyboardInterrupt)
    @patch.object(launcher, "json_url", return_value=WAREHOUSE_HEALTH)
    def test_start_orchestrates_all_services_without_containerized_core(self, *_):
        instance = launcher.Launcher()
        instance.compose = ["docker", "compose", "--profile", "nanzi"]
        for method in ("acquire", "ensure_docker", "ensure_dependencies", "command", "start_process", "wait_ready", "ensure_alive"):
            setattr(instance, method, Mock())
        instance.check_native_port = Mock(return_value=False)
        instance.running_containers = Mock(return_value={})
        with self.assertRaises(KeyboardInterrupt):
            instance.start()
        command = instance.command.call_args.args[0]
        self.assertEqual(command[-3:], ["platform-gateway", "data-api", "agents"])
        self.assertIn("--build", command)
        self.assertNotIn("core-backend", command)
        self.assertNotIn("core-web", command)
        native_commands = [call.args[1] for call in instance.start_process.call_args_list]
        self.assertNotIn("--reload", native_commands[0])
        self.assertIn("--strictPort", native_commands[1])
        self.assertEqual(instance.wait_ready.call_args.args[0], launcher.ENDPOINTS)
        commands = instance.command.call_args_list
        warehouse_start = commands[1]
        migration = commands[2]
        self.assertEqual(warehouse_start.args[0][-1], "warehouse-postgres")
        self.assertIn("--wait", warehouse_start.args[0])
        self.assertEqual(migration.args[0][-1], "tools/migrate_warehouse.py")
        self.assertEqual(migration.kwargs["env"]["WAREHOUSE_DATABASE_URL"], WAREHOUSE_URL)
        self.assertNotIn("warehouse-test-secret", " ".join(migration.args[0]))

    @patch.object(launcher.shutil, "which", return_value="/usr/bin/lsof")
    def test_failed_warehouse_migration_prevents_backend_start(self, *_):
        instance = launcher.Launcher()
        instance.compose = ["docker", "compose"]
        for method in ("acquire", "ensure_docker", "ensure_dependencies", "start_process"):
            setattr(instance, method, Mock())
        instance.check_native_port = Mock(return_value=False)
        instance.running_containers = Mock(return_value={})
        instance.command = Mock(side_effect=[None, None, launcher.StartupError("migration refused")])
        with self.assertRaisesRegex(launcher.StartupError, "migration refused"):
            instance.start()
        instance.start_process.assert_not_called()

    @patch.object(launcher.shutil, "which", return_value="/usr/bin/lsof")
    @patch.object(launcher.time, "sleep", side_effect=KeyboardInterrupt)
    def test_business_database_seeds_only_the_demo_schema_and_no_managed_container(self, *_):
        parent = {"CORE_DB_TYPE": "postgresql", "CORE_DB_URL": "postgresql://business@localhost:5432/articles"}
        health = {**WAREHOUSE_HEALTH, "data_origin": "business_with_fixture",
                  "database_identity": launcher.database_identity(parent["CORE_DB_URL"])}
        instance = launcher.Launcher()
        instance.compose = ["docker", "compose"]
        for method in ("acquire", "ensure_docker", "ensure_dependencies", "command", "start_process", "wait_ready", "ensure_alive"):
            setattr(instance, method, Mock())
        instance.check_native_port = Mock(return_value=False)
        instance.running_containers = Mock(return_value={})
        with patch.dict(launcher.os.environ, parent, clear=True), patch.object(launcher, "json_url", return_value=health):
            with self.assertRaises(KeyboardInterrupt):
                instance.start()
        self.assertFalse(any("warehouse-postgres" in call.args[0] for call in instance.command.call_args_list))
        migrations = [call for call in instance.command.call_args_list
                      if "tools/migrate_warehouse.py" in call.args[0]]
        self.assertEqual(len(migrations), 1)
        self.assertEqual(migrations[0].kwargs["env"]["WAREHOUSE_DATABASE_URL"], parent["CORE_DB_URL"])

    @patch.object(launcher.shutil, "which", return_value="/usr/bin/lsof")
    @patch.object(launcher.time, "sleep", side_effect=KeyboardInterrupt)
    def test_non_postgres_business_database_is_never_written_to(self, *_):
        parent = {"CORE_DB_TYPE": "mysql", "CORE_DB_URL": "mysql+pymysql://business@localhost:3306/warehouse"}
        health = {"service": "DataWareHouse-Agent Backend", "db_type": "mysql", "data_source": "configured",
                  "data_origin": "business", "database_identity": launcher.database_identity(parent["CORE_DB_URL"])}
        instance = launcher.Launcher()
        instance.compose = ["docker", "compose"]
        for method in ("acquire", "ensure_docker", "ensure_dependencies", "command", "start_process", "wait_ready", "ensure_alive"):
            setattr(instance, method, Mock())
        instance.check_native_port = Mock(return_value=False)
        instance.running_containers = Mock(return_value={})
        with patch.dict(launcher.os.environ, parent, clear=True), patch.object(launcher, "json_url", return_value=health):
            with self.assertRaises(KeyboardInterrupt):
                instance.start()
        self.assertFalse(any("warehouse-postgres" in call.args[0] or "tools/migrate_warehouse.py" in call.args[0]
                             for call in instance.command.call_args_list))

    @patch.object(launcher, "read_url", return_value=b"healthy")
    @patch.object(launcher, "find_docker", return_value="docker")
    @patch.object(launcher, "output", return_value='{"Service":"warehouse-postgres","Health":"healthy"}')
    def test_health_check_requires_real_matching_warehouse(self, *_):
        with patch.dict(launcher.os.environ, {}, clear=True), patch.object(launcher, "json_url", return_value=WAREHOUSE_HEALTH):
            self.assertEqual(launcher.check(), 0)
        with patch.dict(launcher.os.environ, {}, clear=True), patch.object(launcher, "json_url", return_value={**WAREHOUSE_HEALTH, "database_identity": "wrong"}):
            self.assertEqual(launcher.check(), 1)

    @patch.object(launcher, "read_url", return_value=None)
    def test_unhealthy_service_prevents_success(self, _):
        instance = launcher.Launcher()
        with self.assertRaisesRegex(launcher.StartupError, "未在 0 秒内就绪"):
            instance.wait_ready({"NanZi": "http://unused"}, timeout=0)

    @patch.object(launcher.subprocess, "run")
    def test_cleanup_preserves_preexisting_containers_and_targets_only_owned_process(self, run):
        instance = launcher.Launcher()
        instance.docker = "docker"
        instance.containers_before = {"old-container": "data-api"}
        instance.running_containers = Mock(return_value={"recreated-old-container": "data-api", "new-container": "agents"})
        own_process = Mock()
        instance.processes = {"backend": own_process}
        instance.terminate = Mock()
        instance.cleanup()
        instance.terminate.assert_called_once_with(own_process)
        self.assertEqual(run.call_args.args[0], ["docker", "stop", "--time", "15", "new-container"])
        instance.cleanup()
        self.assertEqual(run.call_count, 1)

    @patch.object(launcher.os, "killpg")
    def test_shutdown_uses_graceful_signal_and_never_kills_an_exited_process(self, killpg):
        completed = Mock()
        completed.poll.return_value = 0
        launcher.Launcher.terminate(completed)
        killpg.assert_not_called()
        running = Mock(pid=42)
        running.poll.return_value = None
        running.wait.side_effect = subprocess.TimeoutExpired("owned", 15)
        launcher.Launcher.terminate(running)
        killpg.assert_called_once_with(42, launcher.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
