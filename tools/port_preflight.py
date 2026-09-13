#!/usr/bin/env python3
"""Host-port preflight for ./platform.sh.

Resolves the host ports a given compose target will publish (always by parsing
``docker compose config``, never from a hardcoded list) and reports, per port,
whether it is free, already held by this compose project (a normal restart), or
blocked by something else.

Exit codes:
    0  every port is usable
    1  at least one port is blocked, or occupancy could not be determined
    2  the compose configuration itself could not be resolved
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import socket
import unicodedata
import subprocess
import sys

FREE = "free"
SELF = "self"
BLOCKED = "blocked"
UNKNOWN = "unknown"

STATUS_LABEL = {
    FREE: "空闲",
    SELF: "本项目",
    BLOCKED: "冲突",
    UNKNOWN: "无法检测",
}

PORT_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")
DOCKER_PORT_RE = re.compile(r"(?:([0-9a-fA-F:.\[\]]+):)?(\d+)->\d+/(tcp|udp)")

# A published range wider than this is almost certainly a config mistake; probing
# thousands of sockets would hang the launch instead of protecting it.
MAX_RANGE = 64


class ConfigError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# compose config
# --------------------------------------------------------------------------


def compose_config(env_file, profiles, no_interpolate=False):
    argv = ["docker", "compose", "--env-file", env_file]
    for profile in profiles:
        argv += ["--profile", profile]
    argv += ["config", "--format", "json"]
    if no_interpolate:
        argv.append("--no-interpolate")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        raise ConfigError("找不到 docker 可执行文件，无法解析 compose 端口配置")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ConfigError(
            "docker compose config 失败（退出码 %d）:\n%s" % (proc.returncode, detail)
        )
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ConfigError("无法解析 docker compose config 输出: %s" % exc)


def expand_services(config, requested):
    """Expand an explicit service list through depends_on, like `compose up` does."""
    services = config.get("services") or {}
    if not requested:
        return sorted(services)
    seen = []
    stack = list(requested)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        if name not in services:
            # Unknown service: `compose up` would fail on it anyway. Skip quietly
            # so the preflight never invents ports.
            continue
        seen.append(name)
        depends = (services[name] or {}).get("depends_on") or {}
        if isinstance(depends, dict):
            stack.extend(depends.keys())
        elif isinstance(depends, list):
            stack.extend(depends)
    return sorted(seen)


def _published_values(published):
    """`published` may be an int, "8080", or a "8000-8005" range."""
    if published is None:
        return []
    text = str(published).strip()
    if not text:
        return []
    if "-" in text:
        low, _, high = text.partition("-")
        try:
            low_i, high_i = int(low), int(high)
        except ValueError:
            return []
        if high_i < low_i or (high_i - low_i + 1) > MAX_RANGE:
            return []
        return list(range(low_i, high_i + 1))
    try:
        return [int(text)]
    except ValueError:
        return []


def port_var_hints(env_file, profiles):
    """service -> [env var name per port entry], from the un-interpolated config."""
    hints = {}
    try:
        raw = compose_config(env_file, profiles, no_interpolate=True)
    except ConfigError:
        return hints  # best effort only; never fail the preflight over a hint
    for name, service in (raw.get("services") or {}).items():
        names = []
        for entry in (service or {}).get("ports") or []:
            text = entry if isinstance(entry, str) else str(
                (entry or {}).get("published", "")
            )
            candidates = PORT_VAR_RE.findall(text)
            picked = None
            for candidate in candidates:
                if candidate.endswith("_PORT"):
                    picked = candidate
                    break
            names.append(picked or (candidates[0] if candidates else None))
        hints[name] = names
    return hints


def collect_targets(config, services, hints):
    """-> list of dicts: port, proto, host_ip, service, var"""
    targets = []
    all_services = config.get("services") or {}
    for name in services:
        service = all_services.get(name) or {}
        for index, entry in enumerate(service.get("ports") or []):
            if not isinstance(entry, dict):
                continue
            proto = (entry.get("protocol") or "tcp").lower()
            host_ip = entry.get("host_ip") or None
            service_hints = hints.get(name) or []
            var = service_hints[index] if index < len(service_hints) else None
            for port in _published_values(entry.get("published")):
                targets.append(
                    {
                        "port": port,
                        "proto": proto,
                        "host_ip": host_ip,
                        "service": name,
                        "var": var,
                    }
                )
    targets.sort(key=lambda t: (t["port"], t["proto"], t["service"]))
    # Same host port claimed twice inside one config: keep both rows so the
    # operator sees the internal clash too.
    return targets


# --------------------------------------------------------------------------
# occupancy probes
# --------------------------------------------------------------------------


def probe_bind(port, proto, host_ip):
    """-> (occupied, probed_ok). Never reports "free" on an inconclusive probe."""
    addr = host_ip or "0.0.0.0"
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    kind = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
    sock = None
    try:
        sock = socket.socket(family, kind)
        # Deliberately no SO_REUSEADDR: we want EADDRINUSE from a live listener.
        sock.bind((addr, port))
        return False, True
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return True, True
        # EACCES/EPERM (privileged port) and EADDRNOTAVAIL say nothing about
        # occupancy, so fall through to the other probes rather than guess.
        return False, False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def probe_connect(port, proto):
    """TCP corroboration: something answering on loopback means occupied."""
    if proto != "tcp":
        return False, False
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        sock.connect(("127.0.0.1", port))
        return True, True
    except socket.timeout:
        return False, True
    except OSError:
        return False, True
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def probe_nc(port, proto):
    """Degraded path when socket probing is unavailable."""
    if proto != "tcp" or not shutil.which("nc"):
        return False, False
    try:
        proc = subprocess.run(
            ["nc", "-z", "-G", "1", "-w", "1", "127.0.0.1", str(port)],
            capture_output=True,
            text=True,
        )
    except Exception:
        return False, False
    if proc.returncode == 0:
        return True, True
    if proc.returncode == 1:
        return False, True
    return False, False


def probe_port(port, proto, host_ip):
    occupied, ok = probe_bind(port, proto, host_ip)
    if ok and occupied:
        return True, True
    connected, connect_ok = probe_connect(port, proto)
    if connect_ok and connected:
        return True, True
    if ok:
        return False, True
    if connect_ok:
        return False, True
    nc_occupied, nc_ok = probe_nc(port, proto)
    if nc_ok:
        return nc_occupied, True
    return False, False


# --------------------------------------------------------------------------
# owner identification
# --------------------------------------------------------------------------


def docker_port_owners():
    """host port -> {"container","project","service"} for RUNNING containers."""
    owners = {}
    if not shutil.which("docker"):
        return owners
    try:
        listing = subprocess.run(
            ["docker", "ps", "-q"], capture_output=True, text=True
        )
    except Exception:
        return owners
    ids = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
    if not ids:
        return owners
    try:
        inspected = subprocess.run(
            ["docker", "inspect"] + ids, capture_output=True, text=True
        )
        containers = json.loads(inspected.stdout or "[]")
    except Exception:
        return owners
    for container in containers:
        name = (container.get("Name") or "").lstrip("/")
        labels = ((container.get("Config") or {}).get("Labels")) or {}
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")
        bindings = ((container.get("NetworkSettings") or {}).get("Ports")) or {}
        for spec, hosts in bindings.items():
            proto = spec.split("/")[-1] if "/" in spec else "tcp"
            for host in hosts or []:
                host_port = (host or {}).get("HostPort")
                if not host_port:
                    continue
                try:
                    key = (int(host_port), proto)
                except ValueError:
                    continue
                owners.setdefault(
                    key,
                    {"container": name, "project": project, "service": service},
                )
    return owners


def lsof_owner(port, proto):
    if not shutil.which("lsof"):
        return None
    selector = "-iTCP:%d" % port if proto == "tcp" else "-iUDP:%d" % port
    argv = ["lsof", "-nP", selector, "-F", "pcLn"]
    if proto == "tcp":
        argv.append("-sTCP:LISTEN")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except Exception:
        return None
    entries = []
    current = {}
    for line in (proc.stdout or "").splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            if current.get("pid"):
                entries.append(current)
            current = {"pid": value}
        elif tag == "c":
            current["command"] = value
        elif tag == "L":
            current["user"] = value
    if current.get("pid"):
        entries.append(current)
    if not entries:
        return None
    first = entries[0]
    parts = [first.get("command") or "?"]
    detail = ["pid %s" % first.get("pid", "?")]
    if first.get("user"):
        detail.append("user %s" % first["user"])
    return "%s (%s)" % (parts[0], ", ".join(detail))


def identify_owner(port, proto, docker_owners, self_project):
    owner = docker_owners.get((port, proto))
    if owner:
        if owner.get("project") and owner["project"] == self_project:
            return SELF, "本项目容器 %s（服务 %s）" % (
                owner["container"],
                owner.get("service") or "?",
            )
        if owner.get("project"):
            return BLOCKED, "其他 compose 项目 %s 的容器 %s" % (
                owner["project"],
                owner["container"],
            )
        return BLOCKED, "容器 %s" % owner["container"]
    described = lsof_owner(port, proto)
    if described:
        return BLOCKED, described
    return BLOCKED, "未知进程（lsof 无法定位，可能属于其他用户）"


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def remedy(row):
    lines = []
    owner = row["owner"] or ""
    if "本项目容器" in owner:
        return lines
    lines.append("      · 停掉占用方后重试")
    if row.get("var"):
        lines.append(
            "      · 或改用别的宿主机端口：export %s=<新端口> 后重新执行本命令"
            % row["var"]
        )
    else:
        lines.append(
            "      · 或在 compose.yaml 中把 %s 的宿主机端口改成未占用的值"
            % row["service"]
        )
    return lines


def _width(text):
    """Display width: CJK glyphs occupy two terminal columns, not one."""
    total = 0
    for char in str(text):
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


def _pad(text, width):
    text = str(text)
    return text + " " * max(0, width - _width(text))


def render(rows, target, project, stream):
    print(
        "[preflight] 目标 %s | compose 项目 %s" % (target, project or "?"),
        file=stream,
    )
    if not rows:
        print("  该目标没有发布任何宿主机端口。", file=stream)
        return

    columns = ["端口", "服务", "协议", "状态", "占用者"]
    table = [columns] + [
        [
            str(r["port"]),
            r["service"],
            r["proto"],
            STATUS_LABEL[r["status"]],
            r["owner"] or "-",
        ]
        for r in rows
    ]
    widths = [
        max(_width(row[index]) for row in table) for index in range(len(columns))
    ]
    for position, row in enumerate(table):
        cells = [_pad(row[i], widths[i]) for i in range(len(columns))]
        print("  " + "  ".join(cells).rstrip(), file=stream)
        if position == 0:
            print("  " + "-" * (sum(widths) + 2 * (len(widths) - 1)), file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description="platform.sh host port preflight")
    parser.add_argument("--env-file", default=".env.platform")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--service", action="append", default=[])
    parser.add_argument("--target", default="?", help="label used in the report only")
    parser.add_argument("--mode", choices=["gate", "report"], default="gate")
    args = parser.parse_args(argv)

    stream = sys.stderr if args.mode == "gate" else sys.stdout

    if not os.path.exists(args.env_file):
        print(
            "[preflight] 未能检查端口占用：缺少 %s，先执行 ./platform.sh init"
            % args.env_file,
            file=sys.stderr,
        )
        return 2

    try:
        config = compose_config(args.env_file, args.profile)
    except ConfigError as exc:
        print("[preflight] 未能检查端口占用：%s" % exc, file=sys.stderr)
        return 2

    project = config.get("name")
    services = expand_services(config, args.service)
    hints = port_var_hints(args.env_file, args.profile)
    targets = collect_targets(config, services, hints)

    docker_owners = docker_port_owners()

    rows = []
    for target in targets:
        occupied, probed = probe_port(target["port"], target["proto"], target["host_ip"])
        if not probed:
            status, owner = UNKNOWN, "未能检查端口占用（socket/lsof/nc 均不可用）"
        elif not occupied:
            status, owner = FREE, None
        else:
            status, owner = identify_owner(
                target["port"], target["proto"], docker_owners, project
            )
        row = dict(target)
        row["status"] = status
        row["owner"] = owner
        rows.append(row)

    render(rows, args.target, project, stream)

    blocked = [r for r in rows if r["status"] == BLOCKED]
    undecided = [r for r in rows if r["status"] == UNKNOWN]

    if blocked:
        print("", file=stream)
        print("[preflight] 以下端口被占用，已中止启动（不会起任何容器）：", file=stream)
        for row in blocked:
            print(
                "  ✗ %s/%s  服务 %s  ←  %s"
                % (row["port"], row["proto"], row["service"], row["owner"]),
                file=stream,
            )
            for line in remedy(row):
                print(line, file=stream)
    if undecided:
        print("", file=stream)
        print(
            "[preflight] 以下端口未能检查占用情况，出于安全已中止（不做静默放行）：",
            file=stream,
        )
        for row in undecided:
            print(
                "  ? %s/%s  服务 %s" % (row["port"], row["proto"], row["service"]),
                file=stream,
            )

    if blocked or undecided:
        return 1
    if args.mode == "gate":
        print("[preflight] 端口检查通过，开始启动。", file=stream)
    else:
        print("", file=stream)
        print("[preflight] 全部端口可用。", file=stream)
    return 0


if __name__ == "__main__":
    sys.exit(main())
