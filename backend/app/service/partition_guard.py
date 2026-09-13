# -*- coding: utf-8 -*-
"""Doris/StarRocks RANGE 分区体检：静态分区写到头了没有。

为什么要有这个文件
------------------
`init/doris/dws_trade_order_summary_daily.sql` 曾经只有三档静态分区，
末档上界 "2026-08-01"，既没有 MAXVALUE 兜底也没有 dynamic_partition。
2026-08-01 之后每天 02:00 的跑批全部失败——而失败是**静默**的：
没人对着 DDL 算“最后一档分区什么时候写满”。

这个模块就是把那次人工推算变成一条可以在 CI 里跑的命令：
扫描 SQL 建表脚本，找出「末档分区边界已经过期 / 即将过期，且没有开动态分区」
的表，在跑批开始失败**之前**报出来。

用法
----
    python -m app.service.partition_guard init/doris docs/data-model
    python -m app.service.partition_guard --strict --warn-days 45 init

退出码：0 = 没有 error 级发现；1 = 有；2 = 用法错误。

只读：本模块只解析文本，不连接任何数据库，也不改任何文件。
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "PartitionFinding",
    "TablePartitionSpec",
    "parse_partitioned_tables",
    "scan_sql_text",
    "scan_paths",
    "main",
]

# 末档分区距今不足这么多天就预警（默认一个月，够留出人工处理窗口）
DEFAULT_WARN_DAYS = 31

_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+(?:EXTERNAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"\w.]+)",
    re.IGNORECASE,
)
_RANGE_PARTITION_RE = re.compile(r"\bPARTITION\s+BY\s+RANGE\b", re.IGNORECASE)
_LESS_THAN_RE = re.compile(
    r"\bVALUES\s+LESS\s+THAN\s*\(?\s*(['\"])?\s*([0-9][0-9\-/]{5,9})\s*(?(1)\1)\s*\)?",
    re.IGNORECASE,
)
# Doris 的另一种固定范围写法：VALUES [("2026-07-01"), ("2026-08-01"))
# 第二个值是开区间上界，语义等同 LESS THAN，必须一起认，否则会误报「解析不出上界」
_VALUES_RANGE_RE = re.compile(
    r"\bVALUES\s*\[\s*\(\s*['\"]?[^)'\"]+['\"]?\s*\)\s*,\s*\(\s*['\"]?\s*"
    r"([0-9][0-9\-/]{5,9})\s*['\"]?\s*\)",
    re.IGNORECASE)
_MAXVALUE_RE = re.compile(
    r"\bVALUES\s+LESS\s+THAN\s*\(?\s*MAXVALUE\s*\)?|\bVALUES\s*\[[^]]*MAXVALUE",
    re.IGNORECASE)
_PROP_RE = re.compile(r"""["']dynamic_partition\.(\w+)["']\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


@dataclass(frozen=True)
class TablePartitionSpec:
    """一张 RANGE 分区表从 DDL 文本里解出来的分区事实。"""

    table: str
    source: str
    line: int
    boundaries: tuple[str, ...]          # 归一化成 YYYY-MM-DD 的静态分区上界
    has_maxvalue: bool                   # 有没有 MAXVALUE 兜底档
    dynamic: dict[str, str]              # dynamic_partition.* 属性（键已去前缀）

    @property
    def dynamic_enabled(self) -> bool:
        return self.dynamic.get("enable", "").strip().lower() == "true"

    @property
    def last_boundary(self) -> str | None:
        return max(self.boundaries) if self.boundaries else None


@dataclass(frozen=True)
class PartitionFinding:
    """一条体检发现。severity 只有 error / warn / info 三级。"""

    severity: str
    kind: str
    table: str
    source: str
    line: int
    detail: str
    last_boundary: str | None = None
    dynamic_enabled: bool = False

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:  # 给 CLI 用的一行式输出
        where = f"{self.source}:{self.line}"
        return f"[{self.severity}] {where} {self.table} — {self.detail}"


# ── SQL 文本预处理 ────────────────────────────────────────────────────
def _blank_comments(text: str) -> str:
    """把注释替换成等长空白：偏移量与行号保持不变，方便报准行号。"""
    out = list(text)
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if ch == "-" and text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _split_statements(text: str) -> list[tuple[int, str]]:
    """按分号切语句，返回 [(起始偏移, 语句文本)]；引号内的分号不算。"""
    statements: list[tuple[int, str]] = []
    start = 0
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            continue
        if ch == ";":
            statements.append((start, text[start:i]))
            start = i + 1
    if text[start:].strip():
        statements.append((start, text[start:]))
    return statements


def _normalize_boundary(raw: str) -> str | None:
    """'2026-08-01' / '20260801' / '2026/08/01' → '2026-08-01'；解析不了返回 None。"""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
        except ValueError:
            return None
    return None


def _clean_name(raw: str) -> str:
    return raw.strip().strip("`").strip('"')


# ── 解析 ──────────────────────────────────────────────────────────────
def parse_partitioned_tables(text: str, *, source: str = "<string>") -> list[TablePartitionSpec]:
    """从 SQL 文本里解出所有 RANGE 分区建表语句的分区事实。

    只看 CREATE TABLE：ALTER TABLE ... ADD PARTITION 是补分区的动作，
    不是表的分区策略声明，扫描时会跳过（不然补分区脚本会被误判成一张新表）。
    """
    blanked = _blank_comments(text)
    specs: list[TablePartitionSpec] = []
    for offset, statement in _split_statements(blanked):
        create = _CREATE_TABLE_RE.search(statement)
        if not create or not _RANGE_PARTITION_RE.search(statement):
            continue
        boundaries: list[str] = []
        for pattern, group in ((_LESS_THAN_RE, 2), (_VALUES_RANGE_RE, 1)):
            for match in pattern.finditer(statement):
                normalized = _normalize_boundary(match.group(group))
                if normalized:
                    boundaries.append(normalized)
        dynamic = {key.lower(): value for key, value in _PROP_RE.findall(statement)}
        line = blanked.count("\n", 0, offset + create.start()) + 1
        specs.append(TablePartitionSpec(
            table=_clean_name(create.group(1)),
            source=source,
            line=line,
            boundaries=tuple(sorted(set(boundaries))),
            has_maxvalue=bool(_MAXVALUE_RE.search(statement)),
            dynamic=dynamic,
        ))
    return specs


def _dynamic_end_days(spec: TablePartitionSpec) -> int | None:
    """dynamic_partition.end 解析成 int；没配或配错返回 None。"""
    raw = spec.dynamic.get("end", "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _check_spec(spec: TablePartitionSpec, today: date, warn_days: int) -> list[PartitionFinding]:
    findings: list[PartitionFinding] = []
    last = spec.last_boundary
    enabled = spec.dynamic_enabled

    def finding(severity: str, kind: str, detail: str) -> PartitionFinding:
        return PartitionFinding(severity=severity, kind=kind, table=spec.table,
                                source=spec.source, line=spec.line, detail=detail,
                                last_boundary=last, dynamic_enabled=enabled)

    # 动态分区开着，但 end <= 0 等于没往前预建，照样会写满
    if enabled:
        end = _dynamic_end_days(spec)
        if end is None:
            findings.append(finding(
                "error", "dynamic_partition_no_end",
                "dynamic_partition.enable=true 但没有可解析的 dynamic_partition.end，"
                "调度器不会预建未来分区"))
        elif end <= 0:
            findings.append(finding(
                "error", "dynamic_partition_no_end",
                f"dynamic_partition.end={end} 不会预建任何未来分区，跑批仍会写满分区"))

    # start 配成有限负数 = 每天自动 DROP 历史分区（不可逆删数据），必须显式知情
    start_raw = spec.dynamic.get("start", "").strip()
    if start_raw:
        try:
            start = int(start_raw)
        except ValueError:
            start = None
        if start is not None and -2_000_000_000 < start < 0:
            findings.append(finding(
                "warn", "dynamic_partition_drops_history",
                f"dynamic_partition.start={start}：调度器会自动 DROP 超窗口的历史分区"
                "（不可逆删数据）。确认这是有意为之，否则删掉该属性用默认值"))

    # 兜底档或动态分区任意一个到位，末档就不会写满
    if spec.has_maxvalue or enabled:
        return findings

    if last is None:
        findings.append(finding(
            "error", "unparsable_partition_range",
            "RANGE 分区表既没有 MAXVALUE 兜底档，也解析不出任何分区上界，"
            "无法判断分区是否够用——请人工确认"))
        return findings

    last_date = date.fromisoformat(last)
    if last_date <= today:
        findings.append(finding(
            "error", "partition_range_expired",
            f"末档分区上界 {last}（< 该日期）在 {today.isoformat()} 已过期："
            f"dt >= {last} 的写入无分区可落，跑批会直接失败。"
            "补齐缺口分区并打开 dynamic_partition"))
    elif last_date <= today + timedelta(days=warn_days):
        remaining = (last_date - today).days
        findings.append(finding(
            "warn", "partition_range_expiring",
            f"末档分区上界 {last}，只剩 {remaining} 天就写满，"
            "且未开 dynamic_partition——现在处理，别等跑批失败"))
    return findings


def scan_sql_text(text: str, *, source: str = "<string>", today: date | None = None,
                  warn_days: int = DEFAULT_WARN_DAYS) -> list[PartitionFinding]:
    """体检一段 SQL 文本，返回全部发现（按严重度无序，调用方自行排序）。"""
    today = today or date.today()
    findings: list[PartitionFinding] = []
    for spec in parse_partitioned_tables(text, source=source):
        findings.extend(_check_spec(spec, today, warn_days))
    return findings


def scan_paths(roots: Iterable[str | os.PathLike[str]], *, today: date | None = None,
               warn_days: int = DEFAULT_WARN_DAYS,
               patterns: Sequence[str] = ("*.sql",),
               skip_dirs: Sequence[str] = ("node_modules", "venv", ".venv", ".git",
                                           "__pycache__", "dist", "build")) -> list[PartitionFinding]:
    """递归扫描目录/文件，对每个匹配 patterns 的文件跑 scan_sql_text。"""
    today = today or date.today()
    findings: list[PartitionFinding] = []
    for root in roots:
        base = Path(root)
        files: Iterable[Path]
        if base.is_file():
            files = [base]
        else:
            files = sorted(p for p in base.rglob("*")
                           if p.is_file()
                           and not any(part in skip_dirs for part in p.parts)
                           and any(fnmatch.fnmatch(p.name, pat) for pat in patterns))
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # 读不到就说读不到，别静默跳过
                findings.append(PartitionFinding(
                    severity="warn", kind="unreadable", table="", source=str(path),
                    line=1, detail=f"读取失败: {exc}"))
                continue
            findings.extend(scan_sql_text(text, source=str(path), today=today,
                                          warn_days=warn_days))
    return findings


# ── CLI ───────────────────────────────────────────────────────────────
_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.service.partition_guard",
        description="扫描 SQL 建表脚本，报出静态 RANGE 分区已过期/即将过期的表")
    parser.add_argument("paths", nargs="*", default=["init"],
                        help="要扫描的目录或文件（默认 init）")
    parser.add_argument("--warn-days", type=int, default=DEFAULT_WARN_DAYS,
                        help=f"末档距今不足多少天开始预警（默认 {DEFAULT_WARN_DAYS}）")
    parser.add_argument("--pattern", action="append", default=None,
                        help="文件名匹配（可重复，默认 *.sql）")
    parser.add_argument("--today", default=None, help="覆盖“今天”（YYYY-MM-DD），便于演练")
    parser.add_argument("--strict", action="store_true", help="warn 也算失败")
    args = parser.parse_args(argv)

    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        print(f"--today 不是合法日期: {args.today}", file=sys.stderr)
        return 2

    findings = scan_paths(args.paths, today=today, warn_days=args.warn_days,
                          patterns=tuple(args.pattern or ("*.sql",)))
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.source, f.line))

    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity == "warn"]
    if not findings:
        print(f"✅ 分区体检通过（基准日 {today.isoformat()}）：没有过期或即将写满的静态分区表")
        return 0
    for f in findings:
        print(str(f))
    print(f"\n合计 {len(errors)} 个 error / {len(warns)} 个 warn（基准日 {today.isoformat()}）")
    if errors or (args.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
