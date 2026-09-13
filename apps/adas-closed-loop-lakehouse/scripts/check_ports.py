#!/usr/bin/env python3
"""端口冲突体检：本项目 186xx 号段 vs 同仓平台 vs 宿主机实际占用。

背景：仓库根目录那套既有平台（``/DataWareHouse-Agent/compose.yaml``）已经发布了
8080/8000/3000/8020/8030/8040/6379/6333/9200。平台是既有的、不动它；**本项目让路**，
把所有宿主机发布端口迁到 186xx 独占号段。迁移只发生在「宿主机发布端口」这一侧，
也就是 compose ports 映射的**左半边**；容器端口（右半边）一律保持组件原生默认值，
compose 网络内服务间互访走「服务名 + 容器端口」，与本脚本无关。

三件事::

    [1] 解析本项目 docker/compose.yaml 的发布端口
        优先 `docker compose config --format json`（权威口径，做完变量插值与 profile 展开）；
        Docker 不可用 / 报错时退化为纯标准库的 YAML 扫描（不依赖 pyyaml，裸环境可跑）。
    [2] 解析同仓平台 compose.yaml 的发布端口（同样两条路径；平台 compose 里有
        `${VAR:?...}` 必填变量，没 init 过时 docker 路径会失败，退化路径是常态）。
    [3] 读宿主机当前 LISTEN 端口（lsof）。lsof 不可用时**明确报告「未检查」**，
        绝不静默当通过；`--strict` 下「未检查」直接算失败。

判定（任一项不过 → 退出码 1）::

    E1  本项目端口与平台端口有交集             —— 硬冲突，迁移表白改了
    E2  本项目端口被**非本项目**的进程占着     —— 起栈会 bind 失败
    E3  本项目内部同一个宿主机端口发布两次     —— compose 自己就起不来
    E4  本项目端口不在 186xx 号段内            —— 越过迁移表，号段不再独占

「端口被本项目自己的容器占着」（`make up` 之后的正常状态）不算冲突，会被识别出来并放行。

用法::

    python3 scripts/check_ports.py              # 全量体检
    python3 scripts/check_ports.py --no-docker  # 强制走 YAML 解析路径（验证退化路径）
    python3 scripts/check_ports.py --skip-listen
    python3 scripts/check_ports.py --strict     # lsof 查不了也算失败
    python3 scripts/check_ports.py --quiet      # 只打小结
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 本项目的 compose 文件。
PROJECT_COMPOSE = _REPO_ROOT / "docker" / "compose.yaml"

#: 同仓平台的 compose 文件（apps/<本项目>/ 往上两级）。
PLATFORM_COMPOSE = _REPO_ROOT.parents[1] / "compose.yaml"

#: 独占号段：所有宿主机发布端口都必须落在 [BAND_START, BAND_END] 内。
BAND_START = 18600
BAND_END = 18699

#: 调 docker 的超时（秒）。Docker Desktop 冷启动时 config 会慢。
DOCKER_TIMEOUT = 90


# --------------------------------------------------------------------------- 数据结构


@dataclass(frozen=True, slots=True)
class PublishedPort:
    """一条「宿主机发布端口 → 容器端口」映射。"""

    service: str
    host_port: int
    container_port: int
    host_ip: str | None = None
    protocol: str = "tcp"

    def __str__(self) -> str:
        left = f"{self.host_ip}:{self.host_port}" if self.host_ip else str(self.host_port)
        return f"{left} → {self.container_port}"


@dataclass(slots=True)
class ComposePorts:
    """一个 compose 文件的发布端口清单。"""

    path: Path
    ports: list[PublishedPort]
    method: str
    notes: list[str] = field(default_factory=list)
    #: 变量没解析出来、没法判断端口号的原始条目（宁可报出来也不装作没有）
    unresolved: list[str] = field(default_factory=list)

    @property
    def host_ports(self) -> set[int]:
        return {p.host_port for p in self.ports}

    @property
    def mapping(self) -> dict[int, int]:
        """宿主机端口 → 容器端口。重复发布时保留第一条，重复本身由 E3 报。"""
        out: dict[int, int] = {}
        for p in self.ports:
            out.setdefault(p.host_port, p.container_port)
        return out

    def by_host_port(self, port: int) -> list[PublishedPort]:
        return [p for p in self.ports if p.host_port == port]


@dataclass(slots=True)
class ListenScan:
    """宿主机 LISTEN 端口扫描结果。"""

    available: bool
    #: 端口 → [(pid, 进程名), ...]
    owners: dict[int, list[tuple[int, str]]] = field(default_factory=dict)
    reason: str = ""


# --------------------------------------------------------------------------- 端口串解析

#: `${VAR}` / `${VAR:-默认}` / `${VAR-默认}` / `${VAR:?报错}` / `$VAR`
_VAR_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?+])?(?P<arg>[^}]*)\}"
    r"|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)

#: `[::1]:8080:80` / `127.0.0.1:55432:5432` / `18600:9000` / `8000-8005:8000-8005` / `9000`
_PORT_SPEC_RE = re.compile(
    r"""^
    (?:\[(?P<ip6>[^\]]*)\]:|(?P<ip4>\d{1,3}(?:\.\d{1,3}){3}):)?
    (?:(?P<host>\d+(?:-\d+)?):)?
    (?P<container>\d+(?:-\d+)?)
    $""",
    re.VERBOSE,
)


class UnresolvedSpec(ValueError):
    """端口串里有没法解析的变量引用。"""


def expand_vars(text: str, env: Mapping[str, str]) -> str:
    """按 compose 的语义展开 ``${VAR}`` / ``${VAR:-default}``。

    ``${VAR:?msg}`` 在变量缺失时是**必填报错**，绝不能把报错文案当值用——
    这里直接抛 :class:`UnresolvedSpec`，由调用方计入 ``unresolved`` 并报出来。
    """

    def _sub(m: re.Match[str]) -> str:
        name = m.group("braced") or m.group("bare")
        op = m.group("op") or ""
        arg = m.group("arg") or ""
        value = env.get(name)
        if m.group("bare") is not None:
            if value is None:
                raise UnresolvedSpec(f"缺少变量 ${name}")
            return value
        empty_counts_as_unset = op.startswith(":")
        unset = value is None or (empty_counts_as_unset and value == "")
        if op.endswith("+"):
            return arg if not unset else ""
        if not unset:
            return value or ""
        if op.endswith("-"):
            return arg
        if op.endswith("?"):
            raise UnresolvedSpec(f"缺少必填变量 ${{{name}}}（compose 会直接报错）")
        raise UnresolvedSpec(f"缺少变量 ${{{name}}}")

    return _VAR_RE.sub(_sub, text)


def _expand_port_range(spec: str) -> list[int]:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(spec)]


def parse_port_spec(service: str, raw: str, env: Mapping[str, str]) -> list[PublishedPort]:
    """解析短语法端口串。没有宿主机发布端口（如 ``"9000"``）时返回空列表。"""
    spec = expand_vars(raw.strip().strip("\"'"), env).strip()
    protocol = "tcp"
    if "/" in spec:
        spec, protocol = spec.rsplit("/", 1)
    m = _PORT_SPEC_RE.match(spec)
    if not m:
        raise UnresolvedSpec(f"看不懂的端口串：{raw!r}")
    if m.group("host") is None:
        # "9000" 这种只写容器端口的写法：宿主机端口由 Docker 随机分配，不占号段
        return []
    host_ip = m.group("ip6") or m.group("ip4")
    hosts = _expand_port_range(m.group("host"))
    containers = _expand_port_range(m.group("container"))
    if len(hosts) != len(containers):
        raise UnresolvedSpec(f"端口区间两侧长度不一致：{raw!r}")
    return [
        PublishedPort(service, h, c, host_ip=host_ip, protocol=protocol or "tcp")
        for h, c in zip(hosts, containers, strict=True)
    ]


def _parse_long_syntax(
    service: str, item: Mapping[str, str], env: Mapping[str, str]
) -> list[PublishedPort]:
    """解析长语法条目 ``{target: 9000, published: "18600", ...}``。"""
    published = item.get("published")
    target = item.get("target")
    if target is None:
        raise UnresolvedSpec(f"长语法缺少 target：{dict(item)!r}")
    if published is None:
        return []
    published_text = expand_vars(str(published).strip().strip("\"'"), env)
    ports = parse_port_spec(service, f"{published_text}:{target}", env)
    host_ip = item.get("host_ip")
    protocol = item.get("protocol", "tcp") or "tcp"
    return [
        PublishedPort(p.service, p.host_port, p.container_port, host_ip=host_ip, protocol=protocol)
        for p in ports
    ]


# --------------------------------------------------------------------------- YAML 退化解析
#
# 只认 compose 文件里 `services: → <服务> → ports:` 这一条路径，纯标准库实现：
# 本项目承诺「裸环境（无 Docker、无第三方库）也能跑通检查」，所以这里不 import pyyaml。


def _strip_comment(line: str) -> str:
    """去掉行尾注释，但不动引号里的 ``#``。"""
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (not out or out[-1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    """(缩进, 去注释后的内容)，跳过空行与整行注释。"""
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if not line.strip():
            continue
        rows.append((len(line) - len(line.lstrip(" ")), line.strip()))
    return rows


def _children(rows: Sequence[tuple[int, str]], i: int) -> tuple[int, int]:
    """rows[i] 的子块下标区间 [start, end)：紧随其后、缩进更深的连续行。"""
    base = rows[i][0]
    j = i + 1
    while j < len(rows) and rows[j][0] > base:
        j += 1
    return i + 1, j


def _direct_children(rows: Sequence[tuple[int, str]], start: int, end: int) -> Iterator[int]:
    """块内的直接子项下标（缩进等于块内最小缩进的那些行）。"""
    if start >= end:
        return
    base = min(rows[k][0] for k in range(start, end))
    for k in range(start, end):
        if rows[k][0] == base:
            yield k


def parse_ports_from_yaml(text: str, *, env: Mapping[str, str] | None = None) -> ComposePorts:
    """从 compose YAML 文本里抠出发布端口（退化路径）。"""
    environ = dict(env if env is not None else os.environ)
    rows = _yaml_lines(text)
    ports: list[PublishedPort] = []
    unresolved: list[str] = []

    services_idx = next(
        (i for i, (indent, content) in enumerate(rows) if indent == 0 and content == "services:"),
        None,
    )
    if services_idx is None:
        return ComposePorts(Path("<text>"), [], "yaml", unresolved=unresolved)

    svc_start, svc_end = _children(rows, services_idx)
    for si in _direct_children(rows, svc_start, svc_end):
        header = rows[si][1]
        if not header.endswith(":"):
            continue
        service = header[:-1].strip()
        body_start, body_end = _children(rows, si)
        for ki in _direct_children(rows, body_start, body_end):
            if rows[ki][1] != "ports:":
                continue
            p_start, p_end = _children(rows, ki)
            for raw_entry, long_item in _iter_port_items(rows, p_start, p_end):
                try:
                    if long_item is not None:
                        ports.extend(_parse_long_syntax(service, long_item, environ))
                    else:
                        ports.extend(parse_port_spec(service, raw_entry, environ))
                except (UnresolvedSpec, ValueError) as exc:
                    unresolved.append(f"{service}: {raw_entry or long_item} —— {exc}")
    return ComposePorts(Path("<text>"), ports, "yaml", unresolved=unresolved)


def _iter_port_items(
    rows: Sequence[tuple[int, str]], start: int, end: int
) -> Iterator[tuple[str, dict[str, str] | None]]:
    """把 ports 块拆成条目：短语法给 (端口串, None)，长语法给 ("", {键: 值})。"""
    current: dict[str, str] | None = None
    current_raw = ""
    for k in range(start, end):
        _, content = rows[k]
        if content.startswith("- "):
            if current is not None or current_raw:
                yield current_raw, current
            current, current_raw = None, ""
            body = content[2:].strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*:", body):  # 长语法首行
                key, _, value = body.partition(":")
                current = {key.strip(): value.strip().strip("\"'")}
            else:
                current_raw = body
        elif current is not None and ":" in content:  # 长语法后续行
            key, _, value = content.partition(":")
            current[key.strip()] = value.strip().strip("\"'")
    if current is not None or current_raw:
        yield current_raw, current


# --------------------------------------------------------------------------- Docker 路径


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run(cmd: Sequence[str], *, timeout: int = DOCKER_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def parse_ports_from_docker_json(payload: Mapping[str, object]) -> list[PublishedPort]:
    """从 ``docker compose config --format json`` 的输出里取发布端口。"""
    ports: list[PublishedPort] = []
    services = payload.get("services") or {}
    if not isinstance(services, dict):
        return ports
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        for entry in svc.get("ports") or []:
            if not isinstance(entry, dict):
                continue
            published = entry.get("published")
            target = entry.get("target")
            if published in (None, "", 0) or target is None:
                continue
            # published 可能是 "18600" 或 "18600-18605"
            hosts = _expand_port_range(str(published))
            containers = _expand_port_range(str(target))
            if len(hosts) != len(containers):
                containers = containers * len(hosts)
            ports.append(
                PublishedPort(
                    service=str(name),
                    host_port=hosts[0],
                    container_port=containers[0],
                    host_ip=(entry.get("host_ip") or None),
                    protocol=str(entry.get("protocol") or "tcp"),
                )
            )
            for h, c in zip(hosts[1:], containers[1:], strict=False):
                ports.append(PublishedPort(str(name), h, c))
    return ports


def read_compose_ports(
    path: Path,
    *,
    use_docker: bool = True,
    profiles: Sequence[str] = ("*",),
) -> ComposePorts:
    """读一个 compose 文件的发布端口：先 docker，失败退 YAML。两条路径都必须能用。"""
    if not path.is_file():
        raise FileNotFoundError(f"找不到 compose 文件：{path}")
    notes: list[str] = []

    if use_docker and _docker_available():
        cmd = ["docker", "compose"]
        for prof in profiles:
            cmd += ["--profile", prof]
        cmd += ["-f", str(path), "config", "--format", "json"]
        try:
            proc = _run(cmd)
        except (OSError, subprocess.SubprocessError) as exc:
            notes.append(f"docker compose config 调不起来（{exc}），已退化为 YAML 解析")
        else:
            if proc.returncode == 0:
                try:
                    payload = json.loads(proc.stdout)
                except json.JSONDecodeError as exc:
                    notes.append(
                        f"docker compose config 输出不是 JSON（{exc}），已退化为 YAML 解析"
                    )
                else:
                    return ComposePorts(
                        path, parse_ports_from_docker_json(payload), "docker compose config", notes
                    )
            else:
                first = (proc.stderr or "").strip().splitlines()
                notes.append(
                    "docker compose config 失败"
                    + (f"：{first[0]}" if first else "")
                    + "，已退化为 YAML 解析"
                )
    elif use_docker:
        notes.append("找不到 docker 可执行文件，已退化为 YAML 解析")
    else:
        notes.append("--no-docker：强制走 YAML 解析路径")

    parsed = parse_ports_from_yaml(path.read_text(encoding="utf-8"), env=_compose_env(path))
    return ComposePorts(path, parsed.ports, "YAML 解析（退化路径）", notes, parsed.unresolved)


def _compose_env(path: Path) -> dict[str, str]:
    """docker compose 的变量来源：进程环境 + compose 文件同级的 .env。"""
    env: dict[str, str] = {}
    dotenv = path.parent / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip("\"'")
    env.update(os.environ)
    return env


def own_published_ports(path: Path) -> dict[int, str] | None:
    """本项目**正在运行**的容器占用的宿主机端口 → "服务(容器)"。

    Docker 不可用或查询失败时返回 None（= 判不了归属），调用方据此降级为警告，
    不会把「判不了」当成「没冲突」。
    """
    if not _docker_available():
        return None
    try:
        proc = _run(["docker", "compose", "-f", str(path), "ps", "--format", "json"])
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out: dict[int, str] = {}
    for chunk in _iter_json_documents(proc.stdout):
        for item in chunk if isinstance(chunk, list) else [chunk]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("Service") or item.get("Name") or "?")
            name = str(item.get("Name") or "")
            tag = f"{label}({name})" if name and name != label else label
            for pub in item.get("Publishers") or []:
                if isinstance(pub, dict) and pub.get("PublishedPort"):
                    out[int(pub["PublishedPort"])] = tag
    return out


def _iter_json_documents(text: str) -> Iterator[object]:
    """`docker compose ps --format json` 各版本要么给一个数组、要么给每行一个对象。"""
    stripped = text.strip()
    if not stripped:
        return
    try:
        yield json.loads(stripped)
        return
    except json.JSONDecodeError:
        pass
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


# --------------------------------------------------------------------------- 宿主机 LISTEN


def scan_listening_ports() -> ListenScan:
    """用 lsof 读宿主机当前 LISTEN 的 TCP 端口。查不了就明说「未检查」。"""
    lsof = shutil.which("lsof") or ("/usr/sbin/lsof" if Path("/usr/sbin/lsof").exists() else None)
    if not lsof:
        return ListenScan(False, reason="找不到 lsof 可执行文件")
    try:
        proc = _run([lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn"], timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return ListenScan(False, reason=f"lsof 调用失败：{exc}")

    owners: dict[int, list[tuple[int, str]]] = {}
    pid, command = 0, "?"
    for line in proc.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            pid = int(value) if value.isdigit() else 0
            command = "?"
        elif tag == "c":
            command = value
        elif tag == "n":
            port = _port_from_lsof_name(value)
            if port is not None:
                owners.setdefault(port, [])
                if (pid, command) not in owners[port]:
                    owners[port].append((pid, command))
    if not owners:
        # 一台在跑的机器不可能一个 LISTEN 都没有：这说明 lsof 没真的查成
        reason = (proc.stderr or "").strip().splitlines()
        return ListenScan(
            False,
            reason="lsof 没返回任何 LISTEN 记录"
            + (
                f"（退出码 {proc.returncode}：{reason[0]}）"
                if reason
                else f"（退出码 {proc.returncode}）"
            ),
        )
    return ListenScan(True, owners)


def _port_from_lsof_name(name: str) -> int | None:
    """``*:8000`` / ``127.0.0.1:6379`` / ``[::1]:18600`` → 端口号。"""
    name = name.split(" ", 1)[0]
    if "->" in name:  # 已建立的连接，不是监听
        return None
    _, _, tail = name.rpartition(":")
    return int(tail) if tail.isdigit() else None


# --------------------------------------------------------------------------- 体检


@dataclass(slots=True)
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def _fmt_owner(owners: Iterable[tuple[int, str]]) -> str:
    return "、".join(f"{cmd}(pid {pid})" for pid, cmd in owners)


def check(
    *,
    project_compose: Path = PROJECT_COMPOSE,
    platform_compose: Path = PLATFORM_COMPOSE,
    use_docker: bool = True,
    skip_listen: bool = False,
    strict: bool = False,
    quiet: bool = False,
) -> int:
    report = Report()
    out = (lambda *a: None) if quiet else print

    out("=" * 72)
    out("端口冲突体检 —— 智驾数据闭环湖仓（宿主机 186xx 独占号段）")
    out("=" * 72)

    # [1] 本项目 ------------------------------------------------------------
    project = read_compose_ports(project_compose, use_docker=use_docker)
    out(f"\n[1] 本项目发布端口  {project.path}")
    out(f"    解析方式：{project.method}")
    for note in project.notes:
        out(f"    · {note}")
    seen: dict[int, PublishedPort] = {}
    for p in sorted(project.ports, key=lambda x: x.host_port):
        band = "✓" if BAND_START <= p.host_port <= BAND_END else "✗ 不在 186xx 号段"
        out(f"    {p.host_port:>6} → {p.container_port:<6} {p.service:<14} {band}")
        if not (BAND_START <= p.host_port <= BAND_END):
            report.error(
                f"E4 号段越界：{p.service} 发布 {p.host_port}，"
                f"不在 {BAND_START}-{BAND_END} 独占号段内"
            )
        if BAND_START <= p.container_port <= BAND_END:
            # 186xx 是本项目自己划的宿主机号段，没有任何组件原生监听它。
            # 右半边出现 186xx，基本可以断定是把「左改右不动」改成了「两边一起改」。
            report.error(
                f"E5 容器端口被改成了 186xx：{p.service} 写成 {p.host_port}:{p.container_port}——"
                "容器里的进程仍监听组件原生端口，右半边不许动"
            )
        if p.host_port in seen:
            report.error(
                f"E3 端口重复发布：{p.host_port} 同时被 {seen[p.host_port].service} "
                f"(→{seen[p.host_port].container_port}) 和 {p.service} (→{p.container_port}) 占用"
            )
        else:
            seen[p.host_port] = p
    for bad in project.unresolved:
        report.error(f"E0 本项目端口条目解析不了：{bad}")
    if not project.ports:
        report.error("E0 本项目 compose 里一个发布端口都没解析出来——解析器或文件有问题")
    out(f"    共 {len(project.host_ports)} 个宿主机端口")

    # [2] 同仓平台 ----------------------------------------------------------
    out(f"\n[2] 同仓平台发布端口  {platform_compose}")
    try:
        platform = read_compose_ports(platform_compose, use_docker=use_docker)
    except FileNotFoundError as exc:
        platform = ComposePorts(platform_compose, [], "未解析")
        report.warn(f"W 平台 compose 读不到：{exc}——无法核对两边是否撞车")
    out(f"    解析方式：{platform.method}")
    for note in platform.notes:
        out(f"    · {note}")
    if platform.ports:
        out("    " + " ".join(str(p) for p in sorted(platform.host_ports)))
    for bad in platform.unresolved:
        report.warn(f"W 平台端口条目解析不了（该端口未纳入比对）：{bad}")

    clash = sorted(project.host_ports & platform.host_ports)
    if clash:
        for port in clash:
            mine = "、".join(
                f"{p.service}(→{p.container_port})" for p in project.by_host_port(port)
            )
            theirs = "、".join(
                f"{p.service}(→{p.container_port})" for p in platform.by_host_port(port)
            )
            report.error(f"E1 撞车：宿主机 {port} —— 本项目 {mine}  ✗  平台 {theirs}")
    elif platform.ports:
        out(
            f"    与本项目交集：空 ✓（平台 {len(platform.host_ports)} 个 vs 本项目 "
            f"{len(project.host_ports)} 个）"
        )

    # [3] 宿主机实际占用 ----------------------------------------------------
    out("\n[3] 宿主机当前 LISTEN 端口")
    if skip_listen:
        out("    跳过（--skip-listen）：宿主机占用情况**未检查**")
        report.note("宿主机 LISTEN 占用未检查（--skip-listen）")
        if strict:
            report.error("E2 --strict 下不允许跳过宿主机占用检查")
    else:
        scan = scan_listening_ports()
        if not scan.available:
            out(f"    ⚠️ 宿主机占用情况**未检查**：{scan.reason}")
            out("       （这不等于没冲突——请自行确认 186xx 空闲，或装上 lsof 重跑）")
            report.note(f"宿主机 LISTEN 占用未检查：{scan.reason}")
            if strict:
                report.error(f"E2 --strict 下「未检查」算失败：{scan.reason}")
        else:
            mine = own_published_ports(project_compose)
            if mine is None:
                report.note("Docker 不可用，无法判定占用端口是否属于本项目自己的容器")
            busy = sorted(project.host_ports & set(scan.owners))
            if not busy:
                out(f"    本项目 {len(project.host_ports)} 个端口全部空闲 ✓")
            for port in busy:
                who = _fmt_owner(scan.owners[port])
                svc = "、".join(
                    f"{p.service}(→{p.container_port})" for p in project.by_host_port(port)
                )
                if mine and port in mine:
                    out(f"    {port:>6} 已占用 ← 本项目自己的容器 {mine[port]}  ✓ 不算冲突")
                elif mine is not None:
                    out(f"    {port:>6} 已占用 ← {who}  ✗ 不是本项目的容器")
                    report.error(
                        f"E2 端口被别人占着：宿主机 {port}（本项目 {svc}）正被 {who} 监听，"
                        f"`make up` 会 bind 失败"
                    )
                else:
                    out(f"    {port:>6} 已占用 ← {who}  ⚠️ 归属判不了（Docker 不可用）")
                    report.warn(
                        f"W 宿主机 {port}（本项目 {svc}）已被 {who} 占用，"
                        "但 Docker 不可用、判不出是不是本项目自己的容器"
                    )
            plat_busy = sorted(platform.host_ports & set(scan.owners))
            if plat_busy:
                out(
                    "    （平台侧在跑："
                    + " ".join(str(p) for p in plat_busy)
                    + "，与本项目无交集即可）"
                )

    # 小结 ------------------------------------------------------------------
    print("\n" + "-" * 72)
    for note in report.notes:
        print(f"ℹ️  {note}")
    for warn in report.warnings:
        print(f"⚠️  {warn}")
    for err in report.errors:
        print(f"❌ {err}")
    if report.errors:
        print(f"\n端口体检未通过：{len(report.errors)} 处冲突。")
        return 1
    tail = "（含未检查项，见上）" if report.notes else ""
    print(
        f"\n端口体检通过 ✓ 本项目 {len(project.host_ports)} 个宿主机端口全在 "
        f"{BAND_START}-{BAND_END}，与平台零交集{tail}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="端口冲突体检：本项目 186xx vs 同仓平台 vs 宿主机实际占用"
    )
    parser.add_argument("--project-compose", type=Path, default=PROJECT_COMPOSE)
    parser.add_argument("--platform-compose", type=Path, default=PLATFORM_COMPOSE)
    parser.add_argument(
        "--no-docker", action="store_true", help="不调 docker，强制走 YAML 解析退化路径"
    )
    parser.add_argument("--skip-listen", action="store_true", help="不查宿主机 LISTEN 端口")
    parser.add_argument("--strict", action="store_true", help="宿主机占用「未检查」也算失败")
    parser.add_argument("--quiet", action="store_true", help="只打小结")
    args = parser.parse_args(argv)

    try:
        return check(
            project_compose=args.project_compose,
            platform_compose=args.platform_compose,
            use_docker=not args.no_docker,
            skip_listen=args.skip_listen,
            strict=args.strict,
            quiet=args.quiet,
        )
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
