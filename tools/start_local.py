"""Own and supervise the local core processes and the complete NanZi Compose stack."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import time
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
STATE = RUNTIME / "local-start.json"
SERVICES = ("platform-gateway", "data-api", "agents")
MANAGED_SERVICES = frozenset((*SERVICES, "mysql", "data-api-redis", "agents-redis",
                              "data-api-init", "agents-init", "warehouse-postgres"))
BACKEND_IMPORT_PROBE = (
    "import fastapi, uvicorn, sqlglot, pydantic, pandas, numpy, jinja2, openai, requests; "
    "import psycopg2, qdrant_client, dotenv, sqlalchemy, pymysql, clickhouse_connect, lz4, zstandard, httpx; "
    "print('dependencies-ready')"
)
ENDPOINTS = {
    "核心问数 API": "http://127.0.0.1:8000/health",
    "主平台": "http://127.0.0.1:5173/",
    "平台网关": "http://127.0.0.1:8080/health",
    "NanZi 数据服务": "http://127.0.0.1:8020/health",
    "NanZi 智能体": "http://127.0.0.1:8030/health",
    "平台连接": "http://127.0.0.1:8080/api/platform/ready",
}


class StartupError(RuntimeError):
    """A dependency or an owned service could not become ready."""


def say(message: str) -> None:
    print(message, flush=True)


def read_url(url: str) -> bytes | None:
    try:
        with urlopen(url, timeout=3) as response:
            return response.read(1024 * 1024)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None


def json_url(url: str) -> dict:
    content = read_url(url)
    try:
        value = json.loads(content) if content else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def output(command: list[str], *, cwd: Path = ROOT, timeout: int = 15) -> str:
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def process_stamp(pid: int) -> str:
    return output(["ps", "-p", str(pid), "-o", "lstart="])


def listener_pids(port: int) -> set[int]:
    value = output(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    return {int(pid) for pid in value.splitlines() if pid.isdigit()}


def process_in_directory(pid: int, directory: Path) -> bool:
    value = output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    return f"n{directory.resolve()}" in value.splitlines()


def read_env_file(name: str) -> dict[str, str]:
    try:
        content = (ROOT / name).read_text()
    except OSError:
        return {}
    values = {}
    for line in content.splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        try:
            parsed = shlex.split(value, comments=True)
        except ValueError as exc:
            raise StartupError(f"{name} 格式无效，请检查配置引号。") from exc
        values[key.strip()] = parsed[0] if parsed else ""
    return values


def platform_config() -> dict[str, str]:
    values = read_env_file(".env.platform")
    if not values:
        raise StartupError("缺少私有平台配置，请先执行 ./start.sh 完成初始化。")
    return values


def business_data_source(parent: dict[str, str]) -> tuple[str, str] | None:
    """Return the (db_type, url) the operator configured, from the shell or .env."""
    if "CORE_DB_TYPE" in parent or "CORE_DB_URL" in parent:
        selected = parent.get("CORE_DB_TYPE", "postgresql").strip().lower()
        return selected, parent.get("CORE_DB_URL", "")
    settings = read_env_file(".env")
    selected = settings.get("DB_TYPE", "").strip().lower()
    url = (settings.get("DATABASE_URL") or settings.get("DB_URL") or "").strip()
    if not selected or selected == "sqlite" or not url:
        return None
    return selected, url


def uses_managed_warehouse(parent: dict[str, str]) -> bool:
    """The bundled PostgreSQL container is only used when no real source is configured."""
    return business_data_source(parent) is None


def warehouse_url(parent: dict[str, str], config: dict[str, str] | None = None) -> str:
    values = {**(platform_config() if config is None else config), **parent}
    password = values.get("WAREHOUSE_POSTGRES_PASSWORD")
    if not password:
        raise StartupError("缺少 PostgreSQL 数仓密码，请检查 .env.platform 的 WAREHOUSE_POSTGRES_PASSWORD。")
    port = values.get("WAREHOUSE_POSTGRES_PORT", "55432")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise StartupError("WAREHOUSE_POSTGRES_PORT 必须为 1–65535 之间的端口。")
    user = quote(values.get("WAREHOUSE_POSTGRES_USER", "warehouse"), safe="")
    database = quote(values.get("WAREHOUSE_POSTGRES_DB", "datawarehouse"), safe="")
    return f"postgresql+psycopg2://{user}:{quote(password, safe='')}@127.0.0.1:{port}/{database}"


def database_identity(url: str) -> str:
    parsed = urlsplit(url)
    dialect = parsed.scheme.split("+", 1)[0]
    if dialect == "postgres":
        dialect = "postgresql"
    defaults = {"postgresql": 5432, "mysql": 3306, "clickhouse": 8123}
    identity = "|".join(str(value) for value in (
        dialect, parsed.hostname or "", parsed.port or defaults.get(dialect) or "",
        unquote(parsed.path.lstrip("/")), unquote(parsed.username or ""),
    ))
    return hashlib.sha256(identity.encode()).hexdigest()


def backend_environment(parent: dict[str, str], config: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(parent)
    source = business_data_source(parent)
    if source is None:
        env["DB_TYPE"] = "postgresql"
        env["DATABASE_URL"] = env["DB_URL"] = warehouse_url(parent, config)
    else:
        selected_type, url = source
        env["DB_TYPE"] = "postgresql" if selected_type in {"postgres", "postgresql"} else selected_type
        if url:
            # DATABASE_URL is preferred by DBService, so replace both aliases.
            env["DATABASE_URL"] = env["DB_URL"] = url
    env["PYTHONPATH"] = str(ROOT / "backend")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def demo_schema_target(env: dict[str, str]) -> str | None:
    """PostgreSQL destinations receive the demonstration schema; others are left alone."""
    url = env.get("DATABASE_URL") or env.get("DB_URL") or ""
    return url if env.get("DB_TYPE") == "postgresql" and url else None


def backend_health_error(health: dict, env: dict[str, str]) -> str | None:
    if health.get("service") != "DataWareHouse-Agent Backend":
        return "服务不是预期的核心后端"
    desired = env["DB_TYPE"]
    actual = health.get("db_type")
    source = health.get("data_source")
    expected_source = "demo" if desired == "sqlite" else "configured"
    if actual != desired or source != expected_source:
        return f"已有后端使用 {actual or '未知数据库'} / {source or '未知数据模式'}，本次要求 {desired} / {expected_source}"
    expected_url = env.get("DATABASE_URL") or env.get("DB_URL")
    if desired != "sqlite" and expected_url:
        if health.get("database_identity") != database_identity(expected_url):
            return "已有后端连接的是不同数据库，或没有提供可核对的数据库身份"
    if desired == "postgresql" and health.get("data_origin") not in (
            "project_fixture", "business_with_fixture"):
        return "已有后端未连接迁移完成的 PostgreSQL 演示数仓 schema"
    return None


def compose_environment(parent: dict[str, str]) -> dict[str, str]:
    return {
        **parent,
        "PLATFORM_CORE_URL": "http://host.docker.internal:8000",
        "PLATFORM_CORE_UI_URL": "http://localhost:5173",
        "PLATFORM_DATA_API_ENABLED": "true",
        "PLATFORM_AGENTS_ENABLED": "true",
        "PLATFORM_AUDIO_ENABLED": "false",
    }


def find_docker() -> str:
    executable = shutil.which("docker")
    if executable:
        return executable
    for candidate in (
        "/Applications/Docker.app/Contents/Resources/bin/docker",
        "/Applications/OrbStack.app/Contents/MacOS/bin/docker",
    ):
        if Path(candidate).is_file():
            return candidate
    raise StartupError("未找到 Docker。请安装 Docker Desktop 或 OrbStack；以后 start.sh 会自动启动它。")


def docker_application(context: str, endpoint: str, available: set[str]) -> str | None:
    identity = f"{context} {endpoint}".lower()
    if "orbstack" in identity:
        return "OrbStack" if "OrbStack" in available else None
    if "desktop" in identity or ".docker/run/" in identity:
        return "Docker" if "Docker" in available else None
    # A remote or explicitly selected non-desktop engine must never be replaced.
    if context != "default" or not endpoint.startswith("unix:"):
        return None
    return next((app for app in ("Docker", "OrbStack") if app in available), None)


class Launcher:
    def __init__(self) -> None:
        self.processes: dict[str, subprocess.Popen] = {}
        self.pending: subprocess.Popen | None = None
        self.docker = ""
        self.compose: list[str] = []
        self.compose_env = compose_environment(dict(os.environ))
        self.containers_before: dict[str, str] | None = None
        self.stopping = False
        self.lock: TextIO | None = None

    def command(self, args: list[str], *, cwd: Path = ROOT, env: dict | None = None,
                timeout: int = 1800) -> None:
        self.pending = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True)
        deadline = time.monotonic() + timeout
        while self.pending.poll() is None:
            if time.monotonic() >= deadline:
                self.terminate(self.pending)
                raise StartupError(f"命令等待超时：{Path(args[0]).name}（{timeout} 秒）")
            time.sleep(0.3)
        code = self.pending.returncode
        self.pending = None
        if code:
            raise StartupError(f"{Path(args[0]).name} 执行失败（退出码 {code}），请查看上面的具体输出。")

    def acquire(self) -> None:
        RUNTIME.mkdir(mode=0o700, exist_ok=True)
        self.lock = (RUNTIME / "local-start.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StartupError("本项目已有 start.sh 正在运行。可执行 ./start.sh --check 查看状态。") from exc
        STATE.write_text(json.dumps({"pid": os.getpid(), "started": process_stamp(os.getpid())}))

    def ensure_docker(self) -> None:
        self.docker = find_docker()
        context = output([self.docker, "context", "show"])
        endpoint = output([
            self.docker, "context", "inspect", context,
            "--format", "{{.Endpoints.docker.Host}}",
        ])
        # Docker gives an explicit DOCKER_CONTEXT precedence over DOCKER_HOST.
        if not os.environ.get("DOCKER_CONTEXT"):
            endpoint = os.environ.get("DOCKER_HOST", endpoint)
        if not endpoint.startswith(("unix:", "npipe:", "tcp://127.0.0.1:", "tcp://localhost:")):
            raise StartupError("当前 Docker 指向远程或未知引擎，本地启动器不会在该引擎部署服务。请选择本地 Docker 上下文。")
        if not output([self.docker, "info", "--format", "{{.ServerVersion}}"]):
            available = {app for app in ("Docker", "OrbStack")
                         if Path(f"/Applications/{app}.app").exists()}
            application = docker_application(context, endpoint, available) if sys.platform == "darwin" else None
            if not application:
                raise StartupError(f"Docker 上下文 {context or '(未知)'} 不可用，无法自动启动对应引擎。")
            say(f"正在启动 {application}，等待 Docker 就绪……")
            self.command(["open", "-g", "-a", application], timeout=15)
            deadline = time.monotonic() + 180
            while not output([self.docker, "info", "--format", "{{.ServerVersion}}"]):
                if time.monotonic() >= deadline:
                    raise StartupError(f"{application} 在 180 秒内未就绪，请查看该应用的启动提示后重试。")
                time.sleep(2)
        if not output([self.docker, "compose", "version", "--short"]):
            raise StartupError("当前 Docker 未提供 Compose v2；请更新 Docker Desktop 或 OrbStack。")
        self.compose = [self.docker, "compose", "--env-file", ".env.platform", "--profile", "nanzi"]

    def running_containers(self) -> dict[str, str]:
        if not self.compose:
            return {}
        result = subprocess.run(
            self.compose + ["ps", "--all", "--format", "json", "--filter", "status=running"],
            cwd=ROOT, env=self.compose_env, capture_output=True, text=True, timeout=20,
        )
        if result.returncode:
            raise StartupError("无法读取本项目的 Docker 容器状态。")
        payload = result.stdout.strip()
        try:
            rows = json.loads(payload) if payload.startswith("[") else [
                json.loads(line) for line in payload.splitlines() if line.strip()
            ]
            return {row["ID"]: row["Service"] for row in rows
                    if row["Service"] in MANAGED_SERVICES}
        except (ValueError, KeyError, TypeError) as exc:
            raise StartupError("Docker 返回了无法识别的容器状态，未尝试清理未知容器。") from exc

    def check_native_port(self, name: str, port: int, directory: Path, health_url: str) -> bool:
        pids = listener_pids(port)
        if not pids:
            return False
        if not all(process_in_directory(pid, directory) for pid in pids):
            raise StartupError(f"端口 {port} 被其他程序占用，未终止该程序。请释放该端口后重试。")
        if not read_url(health_url):
            raise StartupError(f"本项目已有{name}占用 {port}，但未通过健康检查。请查看对应日志。")
        if name == "后端":
            health = json_url(health_url)
            parent = dict(os.environ)
            problem = backend_health_error(health, backend_environment(parent))
            if problem:
                raise StartupError(
                    f"{problem}；请先在原启动终端按 Ctrl+C 停止旧服务，再运行 start.sh。"
                )
        elif b"/src/main.tsx" not in (read_url(health_url) or b""):
            raise StartupError(f"端口 {port} 的页面不是本项目的 Vite 前端。")
        say(f"复用本项目已就绪的{name}（端口 {port}）。")
        return True

    def ensure_dependencies(self, need_backend: bool, need_frontend: bool) -> None:
        python = ROOT / "backend/venv/bin/python"
        if need_backend:
            if not python.exists():
                say("首次运行：创建后端 Python 环境……")
                self.command([sys.executable, "-m", "venv", str(python.parent.parent)])
            # A previous interrupted pip install can leave the interpreter present
            # with incomplete dependencies. Check imports on every fresh start.
            probe = [str(python), "-c", BACKEND_IMPORT_PROBE]
            if output(probe, cwd=ROOT / "backend", timeout=60) != "dependencies-ready":
                say("正在安装或修复后端 Python 依赖……")
                self.command([str(python), "-m", "pip", "install", "-r", "requirements.txt"], cwd=ROOT / "backend")
                self.command(probe, cwd=ROOT / "backend", timeout=60)
        if need_frontend and not (ROOT / "frontend/node_modules/vite/bin/vite.js").exists():
            if not shutil.which("npm"):
                raise StartupError("未找到 npm，请先安装 Node.js。")
            say("首次运行：安装前端依赖……")
            self.command(["npm", "ci"], cwd=ROOT / "frontend")
        if need_frontend and not shutil.which("node"):
            raise StartupError("未找到 node，请先安装 Node.js。")

    def start_process(self, name: str, command: list[str], directory: Path, env: dict) -> None:
        path = RUNTIME / f"{name}.log"
        with path.open("ab") as log:
            self.processes[name] = subprocess.Popen(
                command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        say(f"已启动 {name}，日志：{path}")

    def ensure_alive(self) -> None:
        for name, process in self.processes.items():
            if process.poll() is not None:
                raise StartupError(f"{name} 已退出（{process.returncode}），请查看 {RUNTIME / (name + '.log')}。")

    def wait_ready(self, endpoints: dict[str, str], timeout: int = 240) -> None:
        waiting = dict(endpoints)
        deadline = time.monotonic() + timeout
        last_notice = 0.0
        while waiting:
            self.ensure_alive()
            for name, url in list(waiting.items()):
                if read_url(url):
                    waiting.pop(name)
                    say(f"✓ {name}已就绪")
            if not waiting:
                break
            now = time.monotonic()
            if now >= deadline:
                raise StartupError(f"服务未在 {timeout} 秒内就绪：{'、'.join(waiting)}。请查看 .runtime 日志及 docker compose 日志。")
            if now - last_notice >= 20:
                say(f"正在等待：{'、'.join(waiting)}……")
                last_notice = now
            time.sleep(1)

    def start(self) -> None:
        if not shutil.which("lsof"):
            raise StartupError("未找到 lsof，无法安全确认端口归属。请先安装 lsof。")
        self.acquire()
        say("启动完整本地平台：核心问数 + NanZi 数据服务 + NanZi 智能体。")
        self.ensure_docker()
        self.command([sys.executable, "integrations/nanzi/configure.py", "--output", ".env.platform"], timeout=30)
        parent = dict(os.environ)
        backend_env = backend_environment(parent)
        managed = uses_managed_warehouse(parent)
        demo_target = demo_schema_target(backend_env)
        reuse_backend = self.check_native_port("后端", 8000, ROOT / "backend", ENDPOINTS["核心问数 API"])
        reuse_frontend = self.check_native_port("前端", 5173, ROOT / "frontend", ENDPOINTS["主平台"])
        self.containers_before = self.running_containers()
        # The project interpreter runs the schema migration even when a backend is reused.
        self.ensure_dependencies(bool(demo_target) or not reuse_backend, not reuse_frontend)
        if managed:
            say("正在启动本项目自带的持久化 PostgreSQL 数仓……")
            self.command(self.compose + ["up", "-d", "--wait", "--wait-timeout", "120", "warehouse-postgres"],
                         env=self.compose_env, timeout=600)
        if demo_target:
            say("正在检查 PostgreSQL 演示数据 schema（仅新建 warehouse schema，不改动既有业务表）……")
            self.command([str(ROOT / "backend/venv/bin/python"), "tools/migrate_warehouse.py"],
                         env={**parent, "WAREHOUSE_DATABASE_URL": demo_target}, timeout=300)
        if not reuse_backend:
            self.start_process("backend", [
                str(ROOT / "backend/venv/bin/python"), "-m", "uvicorn", "app.main:app",
                "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10",
            ], ROOT / "backend", backend_env)
        if not reuse_frontend:
            self.start_process("frontend", [
                "node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1",
                "--port", "5173", "--strictPort",
            ], ROOT / "frontend", dict(os.environ))
        self.wait_ready({name: ENDPOINTS[name] for name in ("核心问数 API", "主平台")})
        problem = backend_health_error(json_url(ENDPOINTS["核心问数 API"]), backend_env)
        if problem:
            raise StartupError(f"核心数仓验证失败：{problem}。")
        say("正在构建并启动完整 NanZi 应用及 MySQL / Redis，首次运行可能需要数分钟……")
        self.command(self.compose + ["up", "-d", "--build", *SERVICES], env=self.compose_env, timeout=3600)
        self.wait_ready(ENDPOINTS, timeout=300)
        say("\n全部服务已就绪：")
        say("  主平台             http://localhost:5173")
        say("  NanZi 数据服务      http://localhost:8020")
        say("  NanZi 智能体        http://localhost:8030")
        say("  API 文档           http://localhost:8000/docs")
        if managed:
            say("核心问数使用本项目自带的 PostgreSQL 持久化数仓；已迁移项目示例数据。")
        elif backend_env["DB_TYPE"] == "sqlite":
            say("核心问数当前使用明确标注的内置演示数据。")
        else:
            say(f"核心问数已连接 .env 配置的真实 {backend_env['DB_TYPE'].upper()} 数据源；"
                "演示用交易与听书数据位于独立的 warehouse schema，既有业务表未被改动。")
        say("NanZi 登录账号：admin；各自登录密钥保存在 .env.platform（详见 integrations/nanzi/README.md）。")
        say("按 Ctrl+C 停止本次启动的服务，已有服务和持久化数据保留。")
        while True:
            self.ensure_alive()
            time.sleep(2)

    @staticmethod
    def terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            # Never escalate to SIGKILL or kill a port owner that we did not start.
            say(f"进程 {process.pid} 尚未退出；已发送 SIGTERM，请查看日志。")

    def cleanup(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        if self.pending:
            self.terminate(self.pending)
        for process in self.processes.values():
            self.terminate(process)
        if self.containers_before is not None:
            try:
                # A rebuilt container may get a new ID. Preserve an already-running
                # service by its Compose service name, not just the previous ID.
                previous_services = set(self.containers_before.values())
                owned = {container for container, service in self.running_containers().items()
                         if service not in previous_services}
                if owned:
                    subprocess.run([self.docker, "stop", "--time", "15", *sorted(owned)],
                                   cwd=ROOT, timeout=90, check=False)
            except (OSError, subprocess.SubprocessError, StartupError) as exc:
                say(f"Docker 清理未完成：{type(exc).__name__}。持久化数据未删除。")
        if self.lock:
            # Only the process holding the lock may remove its state file.
            try:
                state = json.loads(STATE.read_text())
                if state.get("pid") == os.getpid():
                    STATE.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            self.lock.close()


def stop() -> int:
    try:
        state = json.loads(STATE.read_text())
        pid = int(state["pid"])
        stamp = state["started"]
    except (OSError, ValueError, KeyError, TypeError):
        say("没有本启动器管理中的服务。已有独立服务未作变更。")
        return 0
    if pid <= 1:
        say("启动器状态无效，未向任何进程发送信号。")
        return 1
    command = output(["ps", "-p", str(pid), "-o", "command="])
    if not stamp or process_stamp(pid) != stamp or str(ROOT / "tools/start_local.py") not in command:
        say("启动器状态已过期，未向任何进程发送信号。")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        say("启动器已退出；已有独立服务未作变更。")
        return 0
    say("已请求启动器优雅停止；日志和持久化数据保留。")
    return 0


def check() -> int:
    healthy = True
    for name, url in ENDPOINTS.items():
        ready = bool(read_url(url))
        say(f"{'✓' if ready else '✗'} {name}: {url}")
        healthy = healthy and ready
    try:
        parent = dict(os.environ)
        env = backend_environment(parent)
        managed = uses_managed_warehouse(parent)
        problem = backend_health_error(json_url(ENDPOINTS["核心问数 API"]), env)
        say(f"{'✗ ' + problem if problem else '✓ 核心数据库身份及数据来源匹配'}")
        healthy = healthy and problem is None
        if managed:
            docker = find_docker()
            # Inspect only this Compose project, without starting Docker or changing services.
            status = output([
                docker, "compose", "--env-file", ".env.platform", "ps", "--format", "json", "warehouse-postgres",
            ])
            rows = json.loads(status) if status.startswith("[") else [json.loads(line) for line in status.splitlines()]
            ready = any(row.get("Service") == "warehouse-postgres" and row.get("Health") == "healthy" for row in rows)
            say(f"{'✓' if ready else '✗'} PostgreSQL 持久化数仓")
            healthy = healthy and ready
    except (StartupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        say(f"✗ 数据源检查未通过：{exc}")
        healthy = False
    return 0 if healthy else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="只检查服务健康状态")
    group.add_argument("--stop", action="store_true", help="停止当前 start.sh 管理的服务，保留数据")
    args = parser.parse_args()
    os.umask(0o077)
    if args.check:
        return check()
    if args.stop:
        return stop()
    launcher = Launcher()

    def interrupted(_signal: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        launcher.start()
    except KeyboardInterrupt:
        say("\n正在停止本次启动的服务……")
        return 0
    except (StartupError, OSError, subprocess.SubprocessError) as exc:
        say(f"启动失败：{exc}")
        return 1
    finally:
        # A second Ctrl+C must not interrupt cleanup halfway through.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        launcher.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
