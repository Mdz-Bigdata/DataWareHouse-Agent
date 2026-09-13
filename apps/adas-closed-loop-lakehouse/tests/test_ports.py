"""端口一致性防回归：迁移表 → compose → config.py 默认值 → .env.example，四处必须同口径。

背景：宿主机发布端口统一迁到 186xx 独占号段，为的是与同仓平台
（``/DataWareHouse-Agent/compose.yaml``：8080/8000/3000/8020/8030/8040/6379/6333/9200）
共存。迁移只动「宿主机发布端口」这一侧——compose ports 映射的**左半边**，
以及宿主机上的客户端要连的 URL；**容器端口（右半边）一律不动**，compose 网络内
服务间互访继续走「服务名 + 容器端口」。

所以这里守四条线::

    1. compose 的发布端口 == 迁移表（含右半边，防止有人把 "18630:9030" 写成 "18630:18630"）
    2. compose 的发布端口 == config.py 等模块默认值里的宿主机端口 ∪ 纯人用端口白名单
    3. .env.example 的端口值 == 代码默认值（逐个变量比对，不只比端口号）
    4. 发布端口全在 186xx 内，且与平台 compose 零交集

测试**不连任何外部服务、也不需要 Docker**：compose 走 scripts/check_ports.py 的
纯标准库 YAML 退化路径。装了 Docker 时额外跑一条「两条解析路径结果必须一致」的断言，
这样退化路径本身也有人守。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_ports  # noqa: E402
from adas_lakehouse.config import (  # noqa: E402
    FlinkConfig,
    KafkaConfig,
    MinioConfig,
    Neo4jConfig,
    StarRocksConfig,
)
from adas_lakehouse.controlplane.store import ControlPlaneStoreConfig  # noqa: E402
from adas_lakehouse.ingest.channels import CdcSourceConfig  # noqa: E402

PROJECT_COMPOSE = _ROOT / "docker" / "compose.yaml"
PLATFORM_COMPOSE = _ROOT.parents[1] / "compose.yaml"
ENV_EXAMPLE = _ROOT / ".env.example"

#: 迁移表，本仓库唯一权威口径：宿主机发布端口 → 容器端口（右半边是组件原生默认值）。
#: 改这张表之前先改文档与 compose，别反过来。
MIGRATION_TABLE: dict[int, int] = {
    18600: 9000,  # MinIO S3
    18601: 9001,  # MinIO Console
    18606: 3306,  # MySQL（CDC 源 + 控制面本地库）
    18630: 9030,  # StarRocks 查询（MySQL 协议）
    18631: 8030,  # StarRocks FE HTTP（18630 已被查询口占用，故顺延 31）
    18640: 8040,  # StarRocks BE HTTP
    18674: 7474,  # Neo4j Browser
    18679: 6379,  # Redis（控制面）
    18681: 8081,  # Flink Web UI
    18683: 8083,  # Flink SQL Gateway
    18687: 7687,  # Neo4j Bolt
    18692: 9092,  # Kafka
}

#: 发布了、但 Python 侧不读的端口——纯给人（浏览器）或给别的工具用。
#: 这三个不出现在任何默认值里是**对的**，所以在这里显式登记，别让第 2 条断言误判。
HOST_ONLY_PORTS: dict[int, str] = {
    18601: "MinIO Console —— 浏览器用，config.py 不读",
    18640: "StarRocks BE HTTP —— Stream Load 用，config.py 不读",
    18674: "Neo4j Browser —— 浏览器用，config.py 不读",
}

#: 代码里带端口的环境变量（清空它们才能拿到真正的默认值）。
PORT_ENV_KEYS = (
    "MINIO_ENDPOINT",
    "FLINK_JOBMANAGER_URL",
    "FLINK_SQL_GATEWAY_URL",
    "STARROCKS_QUERY_PORT",
    "STARROCKS_HTTP_PORT",
    "NEO4J_URI",
    "KAFKA_BOOTSTRAP_SERVERS",
    "CDC_MYSQL_PORT",
    "CONTROL_PLANE_MYSQL_PORT",
    "CONTROL_PLANE_REDIS_PORT",
)


# --------------------------------------------------------------------------- 取数


@pytest.fixture()
def code_defaults(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """环境变量 → 代码默认值。先把这些键从环境里摘掉，否则读到的是本机覆盖值。"""
    for key in PORT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    minio, flink, starrocks = MinioConfig(), FlinkConfig(), StarRocksConfig()
    neo4j, kafka = Neo4jConfig(), KafkaConfig()
    cdc, control_plane = CdcSourceConfig(), ControlPlaneStoreConfig()
    return {
        "MINIO_ENDPOINT": minio.endpoint,
        "FLINK_JOBMANAGER_URL": flink.jobmanager_url,
        "FLINK_SQL_GATEWAY_URL": flink.sql_gateway_url,
        "STARROCKS_QUERY_PORT": str(starrocks.query_port),
        "STARROCKS_HTTP_PORT": str(starrocks.http_port),
        "NEO4J_URI": neo4j.uri,
        "KAFKA_BOOTSTRAP_SERVERS": kafka.bootstrap_servers,
        "CDC_MYSQL_PORT": str(cdc.port),
        "CONTROL_PLANE_MYSQL_PORT": str(control_plane.mysql_port),
        "CONTROL_PLANE_REDIS_PORT": str(control_plane.redis_port),
    }


def _port_of(value: str) -> int:
    """从 ``http://localhost:18600`` / ``bolt://localhost:18687`` / ``18630`` 里取端口。"""
    m = re.search(r":(\d+)(?:/|$)", value)
    return int(m.group(1)) if m else int(value)


@pytest.fixture(scope="module")
def project_ports() -> check_ports.ComposePorts:
    """本项目 compose 的发布端口。走 YAML 退化路径：裸环境（无 Docker）也要能跑。"""
    return check_ports.read_compose_ports(PROJECT_COMPOSE, use_docker=False)


@pytest.fixture(scope="module")
def platform_ports() -> check_ports.ComposePorts:
    return check_ports.read_compose_ports(PLATFORM_COMPOSE, use_docker=False)


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


# --------------------------------------------------------------------------- 1) compose ↔ 迁移表


def test_compose_publishes_exactly_the_migration_table(project_ports):
    """发布端口集合必须与迁移表一字不差——多一个少一个都说明有人绕过了迁移表。"""
    assert project_ports.host_ports == set(MIGRATION_TABLE), (
        f"发布端口与迁移表不一致：\n"
        f"  compose 多出来：{sorted(project_ports.host_ports - set(MIGRATION_TABLE))}\n"
        f"  compose 缺少　：{sorted(set(MIGRATION_TABLE) - project_ports.host_ports)}"
    )


def test_container_ports_are_untouched(project_ports):
    """★ 最关键的一条：只改左半边。右半边必须还是各组件的原生默认端口。

    典型回归是把 ``"18630:9030"`` 写成 ``"18630:18630"``——容器里 StarRocks 仍然
    监听 9030，这么一改宿主机连不上，compose 网络内的服务名访问也跟着断。
    """
    drift = {
        p.host_port: (p.container_port, MIGRATION_TABLE[p.host_port])
        for p in project_ports.ports
        if p.host_port in MIGRATION_TABLE and p.container_port != MIGRATION_TABLE[p.host_port]
    }
    assert not drift, "容器端口（ports 映射右半边）被改动了，实际 vs 迁移表：" + "、".join(
        f"{h}:{got}（应为 {h}:{want}）" for h, (got, want) in sorted(drift.items())
    )


def test_no_container_port_lands_in_the_host_band(project_ports):
    """186xx 是本项目自划的宿主机号段，没有任何组件原生监听它；右半边出现即是改错。"""
    offenders = [
        f"{p.service} {p.host_port}:{p.container_port}"
        for p in project_ports.ports
        if check_ports.BAND_START <= p.container_port <= check_ports.BAND_END
    ]
    assert not offenders, f"容器端口被改成了 186xx：{offenders}"


def test_no_duplicate_host_port(project_ports):
    counts: dict[int, list[str]] = {}
    for p in project_ports.ports:
        counts.setdefault(p.host_port, []).append(p.service)
    dupes = {port: svcs for port, svcs in counts.items() if len(svcs) > 1}
    assert not dupes, f"同一个宿主机端口被发布多次：{dupes}"


# --------------------------------------------------------------------------- 2) compose ↔ 代码默认值


def test_compose_ports_match_python_defaults(project_ports, code_defaults):
    """compose 发布端口集合 == 代码默认值里的宿主机端口 ∪ 纯人用端口白名单。"""
    from_code = {_port_of(v) for v in code_defaults.values()}
    expected = from_code | set(HOST_ONLY_PORTS)
    assert project_ports.host_ports == expected, (
        f"compose 与代码默认值对不上：\n"
        f"  compose 有、代码没有：{sorted(project_ports.host_ports - expected)}"
        f"（若确实是纯人用端口，请登记进 HOST_ONLY_PORTS 并写明理由）\n"
        f"  代码有、compose 没发布：{sorted(expected - project_ports.host_ports)}"
    )


def test_every_python_default_port_is_published(project_ports, code_defaults):
    """逐个变量报，别让上一条的集合差集掩盖了到底是哪个配置漂了。"""
    missing = {
        key: value
        for key, value in code_defaults.items()
        if _port_of(value) not in project_ports.host_ports
    }
    assert not missing, f"这些默认值指向的端口 compose 根本没发布：{missing}"


def test_host_only_ports_are_really_not_read_by_python(code_defaults):
    """白名单要保持诚实：一旦某个端口进了代码默认值，就该从白名单里摘掉。"""
    from_code = {_port_of(v) for v in code_defaults.values()}
    leaked = sorted(set(HOST_ONLY_PORTS) & from_code)
    assert not leaked, f"这些端口已经被代码读了，请从 HOST_ONLY_PORTS 移除：{leaked}"


# --------------------------------------------------------------------------- 3) .env.example ↔ 代码默认值


@pytest.mark.parametrize("key", PORT_ENV_KEYS)
def test_env_example_matches_code_default(key, code_defaults):
    """.env.example 里的端口值必须与代码默认值逐字一致（连 scheme/host 一起比）。"""
    values = _env_example_values()
    assert key in values, f".env.example 缺少 {key}"
    assert values[key] == code_defaults[key], (
        f"{key} 漂了：.env.example={values[key]!r}，代码默认值={code_defaults[key]!r}"
    )


def test_env_example_has_no_stray_186xx_port(project_ports):
    """.env.example 赋值里出现的 186xx 必须都是 compose 真发布了的端口（防打错字）。"""
    stray: dict[str, list[str]] = {}
    for key, value in _env_example_values().items():
        found = [
            p for p in re.findall(r"\b186\d{2}\b", value) if int(p) not in project_ports.host_ports
        ]
        if found:
            stray[key] = found
    assert not stray, f".env.example 里这些 186xx 端口没有对应的发布端口：{stray}"


# --------------------------------------------------------------------------- 4) 号段与平台隔离


def test_all_published_ports_in_the_186xx_band(project_ports):
    outside = sorted(p for p in project_ports.host_ports if not 18600 <= p <= 18699)
    assert not outside, f"这些发布端口不在 186xx 独占号段内：{outside}"
    assert (check_ports.BAND_START, check_ports.BAND_END) == (18600, 18699)


def test_disjoint_from_the_bundled_platform(project_ports, platform_ports):
    """与同仓平台零交集——平台是既有的、不动它，本项目让路。"""
    assert platform_ports.host_ports, (
        f"平台 compose（{PLATFORM_COMPOSE}）一个发布端口都没解析出来，隔离断言会形同虚设"
    )
    clash = sorted(project_ports.host_ports & platform_ports.host_ports)
    detail = {
        port: (
            [p.service for p in project_ports.by_host_port(port)],
            [p.service for p in platform_ports.by_host_port(port)],
        )
        for port in clash
    }
    assert not clash, f"与平台撞车（端口: [本项目服务], [平台服务]）：{detail}"


def test_platform_still_holds_the_ports_we_gave_up():
    """迁移的理由不能凭空消失：平台仍占着这些口，才需要本项目让路。"""
    platform = check_ports.read_compose_ports(PLATFORM_COMPOSE, use_docker=False)
    for port in (8030, 8040, 6379):
        assert port in platform.host_ports, (
            f"平台不再发布 {port} 了？迁移表的前提变了，请复核 docs 与本测试"
        )


# --------------------------------------------------------------------------- 解析器自身


def test_yaml_fallback_agrees_with_docker():
    """两条解析路径必须给出同一个答案，否则退化路径就是个哑弹。"""
    if shutil.which("docker") is None:
        pytest.skip("没装 docker，跳过双路径比对")
    via_docker = check_ports.read_compose_ports(PROJECT_COMPOSE, use_docker=True)
    if not via_docker.method.startswith("docker"):
        pytest.skip(f"docker 路径没跑成：{via_docker.notes}")
    via_yaml = check_ports.read_compose_ports(PROJECT_COMPOSE, use_docker=False)
    assert via_docker.mapping == via_yaml.mapping


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ('"18600:9000"', [(18600, 9000)]),
        ("18606:3306", [(18606, 3306)]),
        ('"127.0.0.1:55432:5432"', [(55432, 5432)]),
        ('"[::1]:18600:9000"', [(18600, 9000)]),
        ('"18692:9092/tcp"', [(18692, 9092)]),
        ('"18600-18602:9000-9002"', [(18600, 9000), (18601, 9001), (18602, 9002)]),
        ('"9000"', []),  # 只写容器端口：宿主机端口随机，不占号段
        ("${SR_PORT:-18630}:9030", [(18630, 9030)]),
    ],
)
def test_parse_port_spec_forms(entry, expected):
    got = [(p.host_port, p.container_port) for p in check_ports.parse_port_spec("svc", entry, {})]
    assert got == expected


def test_parse_port_spec_refuses_to_guess_a_required_variable():
    with pytest.raises(check_ports.UnresolvedSpec):
        check_ports.parse_port_spec("svc", "${MUST_SET:?给个值}:9030", {})


def test_yaml_parser_handles_long_syntax_and_comments():
    text = """
services:
  minio:
    image: minio/minio
    ports:
      - "18600:9000"   # 带 # 的行尾注释不能把端口串截断
      - target: 9001
        published: "18601"
        protocol: tcp
  # 整行注释
  redis:
    ports:
      - "18679:6379"
volumes:
  data:
"""
    parsed = check_ports.parse_ports_from_yaml(text, env={})
    assert parsed.mapping == {18600: 9000, 18601: 9001, 18679: 6379}
    assert not parsed.unresolved


# --------------------------------------------------------------------------- 脚本端到端


def test_check_ports_script_passes_on_the_real_files():
    """裸口径（不连 Docker、不查 lsof）跑 scripts/check_ports.py 必须是 0。"""
    assert (
        check_ports.check(
            project_compose=PROJECT_COMPOSE,
            platform_compose=PLATFORM_COMPOSE,
            use_docker=False,
            skip_listen=True,
            quiet=True,
        )
        == 0
    )


def test_check_ports_script_catches_a_reintroduced_conflict(tmp_path):
    """回滚一个端口到旧值，脚本必须红——不然这层守卫是摆设。"""
    broken = tmp_path / "compose.yaml"
    broken.write_text(
        'services:\n  redis:\n    ports:\n      - "6379:6379"\n',
        encoding="utf-8",
    )
    assert (
        check_ports.check(
            project_compose=broken,
            platform_compose=PLATFORM_COMPOSE,
            use_docker=False,
            skip_listen=True,
            quiet=True,
        )
        == 1
    )


def _check_with_listen(monkeypatch, *, owners, mine, **kwargs) -> int:
    """把 lsof 与 `docker compose ps` 两个外部依赖替掉，只测占用判定这段逻辑。"""
    monkeypatch.setattr(
        check_ports, "scan_listening_ports", lambda: check_ports.ListenScan(True, owners)
    )
    monkeypatch.setattr(check_ports, "own_published_ports", lambda _path: mine)
    return check_ports.check(
        project_compose=PROJECT_COMPOSE,
        platform_compose=PLATFORM_COMPOSE,
        use_docker=False,
        skip_listen=False,
        quiet=False,  # 占用判定的结论在明细里，不在小结里，所以别关输出
        **kwargs,
    )


def test_port_held_by_our_own_container_is_not_a_conflict(monkeypatch, capsys):
    """`make up` 之后 18679 当然是占着的——那是本项目自己的 redis，不该判红。"""
    code = _check_with_listen(
        monkeypatch,
        owners={18679: [(4321, "com.docker.backend")]},
        mine={18679: "redis(adas-redis)"},
    )
    assert code == 0
    assert "本项目自己的容器" in capsys.readouterr().out


def test_port_held_by_a_foreign_process_is_a_conflict(monkeypatch, capsys):
    code = _check_with_listen(
        monkeypatch,
        owners={18679: [(4321, "some-other-redis")]},
        mine={},  # docker 查得到，但这个端口不在本项目的容器里
    )
    assert code == 1
    assert "18679" in capsys.readouterr().out


def test_unknown_ownership_is_a_warning_not_a_silent_pass(monkeypatch, capsys):
    """Docker 不可用 → 判不出归属。既不能判红，也不能装作没看见。"""
    code = _check_with_listen(
        monkeypatch,
        owners={18679: [(4321, "unknown")]},
        mine=None,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "判不了" in out or "判不出" in out


def test_unavailable_lsof_is_reported_and_can_be_made_fatal(monkeypatch, capsys):
    """lsof 查不了时必须明说「未检查」，绝不静默通过；--strict 下直接算失败。"""
    monkeypatch.setattr(
        check_ports,
        "scan_listening_ports",
        lambda: check_ports.ListenScan(False, reason="找不到 lsof 可执行文件"),
    )
    common = {
        "project_compose": PROJECT_COMPOSE,
        "platform_compose": PLATFORM_COMPOSE,
        "use_docker": False,
        "skip_listen": False,
        "quiet": False,
    }
    assert check_ports.check(**common) == 0
    assert "未检查" in capsys.readouterr().out
    assert check_ports.check(**common, strict=True) == 1


def test_cli_entrypoint_runs_green():
    assert check_ports.main(["--no-docker", "--skip-listen", "--quiet"]) == 0
