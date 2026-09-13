#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""密钥泄露扫描——防止 llm_config.json / user_memory.json 之类的文件再次进仓库。

背景：``backend/llm_config.json``（含各厂商 API Key）和 ``backend/user_memory.json``
（含个人问数历史）曾被提交进仓库，事后才加进 .gitignore。.gitignore 对**已跟踪**的
文件是无效的，所以必须有一道自动闸门，否则同样的事会再发生一次。

三道独立检查：

* ``gitignore``  —— DENYLIST 里的每个路径都必须在 .gitignore 里列出；
* ``present``    —— DENYLIST 里的文件不得存在于当前目录树（``--forbid-present``）。
                    只在 CI 里开：actions/checkout 只会落地**被跟踪**的文件，
                    文件存在即等价于「还在被跟踪」。本地开发机上这些文件本就该
                    存在（未跟踪），所以默认关闭。
* ``content``    —— 扫描明文里形似密钥的字符串。

退出码：0 全部通过；1 发现问题；2 用法错误。
"""
from __future__ import annotations

import argparse
import fnmatch
import math
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 绝不允许进仓库的文件（相对仓库根，用 / 分隔）
# --------------------------------------------------------------------------
DENYLIST = (
    "backend/llm_config.json",
    "backend/user_memory.json",
)

# 目录级跳过：构建产物、依赖、缓存、本地运行时状态。
SKIP_DIRS = {
    ".git", ".github/scripts/__pycache__", "node_modules", "venv", ".venv", "env",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".runtime",
    "dist", "build", ".idea", ".vscode", "htmlcov", ".egg-info",
}

# 文件级跳过：锁文件 / 数据样本 / 校验和 / 压缩产物——这些里面的 "sk-xxx" 只会是
# 包名、文件名或哈希，扫了纯属噪音。
SKIP_FILE_GLOBS = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "*.lock", "*.sha256", "*.csv", "*.tsv", "*.parquet", "*.db", "*.sqlite*",
    "*.min.js", "*.min.css", "*.map", "*.svg", "*.ico", "*.png", "*.jpg", "*.jpeg",
    "*.gif", "*.webp", "*.pdf", "*.zip", "*.gz", "*.tgz", "*.whl", "*.log",
)

MAX_FILE_BYTES = 2 * 1024 * 1024

# 已知历史债的豁免清单（按文件路径豁免 content 检查）。每条都会以 WARNING 打印，
# 不会被静默吞掉；条目失效（该文件已不再命中）则报「陈旧条目」，逼着清理。
ALLOWLIST_FILE = ".github/secret-scan-allowlist.txt"

# --------------------------------------------------------------------------
# 密钥形状
# --------------------------------------------------------------------------
PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    # (规则名, 正则——密钥体必须落在第 1 个捕获组, 密钥体最小长度)
    ("openai/anthropic-style", re.compile(r"\bsk-(?:ant-)?([A-Za-z0-9_.\-]{20,})"), 20),
    ("aws-access-key-id", re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), 20),
    ("google-api-key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b"), 39),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"), 38),
    ("slack-token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})\b"), 14),
    # 配置文件里的显式赋值：api_key / secret_key / access_token / password 等
    (
        "assigned-credential",
        re.compile(
            r"(?i)\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
            r"|client[_\-]?secret|private[_\-]?key)\b\s*[:=]\s*[\"']([^\"'\s]{24,})[\"']"
        ),
        24,
    ),
)

# 一眼就是占位符 / 测试夹具的，直接放行。大小写不敏感。
PLACEHOLDER_MARKERS = (
    "your", "xxx", "example", "placeholder", "changeme", "change-me", "dummy",
    "sample", "-here", "test", "fake", "mock", "redact", "mask", "sensitive",
    "todo", "replace", "insert", "abcdef", "deadbeef", "notreal", "invalid",
)

# 模板占位/环境变量引用，不是真密钥。
TEMPLATE_CHARS = ("$", "{", "}", "<", ">", "%")


def shannon_entropy(text: str) -> float:
    """香农熵（bit/字符）。真密钥通常 > 3.0，人写的单词串通常 < 3.0。"""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def looks_like_placeholder(secret: str) -> bool:
    lowered = secret.lower()
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return True
    if any(ch in secret for ch in TEMPLATE_CHARS):
        return True
    # 单一字符重复（xxxxxxxx / 00000000）
    if len(set(secret.replace("-", "").replace("_", ""))) <= 3:
        return True
    return False


def is_probable_secret(secret: str, min_len: int) -> bool:
    if len(secret) < min_len:
        return False
    if looks_like_placeholder(secret):
        return False
    if shannon_entropy(secret) < 3.0:
        return False
    # 至少要有两类字符（纯字母的长单词串基本是标识符而不是密钥）
    classes = sum(
        [
            any(c.islower() for c in secret),
            any(c.isupper() for c in secret),
            any(c.isdigit() for c in secret),
        ]
    )
    return classes >= 2


# --------------------------------------------------------------------------
# .gitignore（只解析够用的子集：目录前缀 + 简单 glob，不处理否定与 ** 深层语义）
# --------------------------------------------------------------------------
def load_gitignore_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def is_ignored(rel_posix: str, patterns: list[str]) -> bool:
    parts = rel_posix.split("/")
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, pat + "/*"):
            return True
        if "/" not in pat and any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
    return False


def should_skip_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, glob) for glob in SKIP_FILE_GLOBS)


# --------------------------------------------------------------------------
# 三道检查
# --------------------------------------------------------------------------
def load_allowlist(root: Path) -> dict[str, str]:
    """读取豁免清单：每行 ``<相对路径>  # 理由``，理由必填。"""
    path = root / ALLOWLIST_FILE
    if not path.is_file():
        return {}
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rel, _, reason = line.partition("#")
        rel = rel.strip()
        if rel:
            entries[rel] = reason.strip() or "(未填写理由)"
    return entries


def check_gitignore_coverage(root: Path) -> list[str]:
    """DENYLIST 的每个路径都必须在 .gitignore 里显式列出。"""
    problems = []
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return [f"缺少 .gitignore：{gitignore}"]
    listed = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for denied in DENYLIST:
        if denied not in listed and f"/{denied}" not in listed:
            problems.append(f".gitignore 未覆盖必须忽略的文件：{denied}")
    return problems


def check_denylist_absent(root: Path) -> list[str]:
    """CI 专用：文件存在 == 仍被 git 跟踪 == 密钥还在仓库里。"""
    problems = []
    for denied in DENYLIST:
        if (root / denied).exists():
            problems.append(
                f"{denied} 出现在检出的工作区里，说明它仍被 git 跟踪。"
                f"请执行：git rm --cached {denied} 并提交（本地文件会保留）。"
            )
    return problems


def iter_scannable_files(root: Path, patterns: list[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIRS
            and not d.endswith(".egg-info")
            and not is_ignored(
                (f"{rel_dir}/{d}" if rel_dir != "." else d), patterns
            )
        ]
        for name in sorted(filenames):
            if should_skip_file(name):
                continue
            rel = (f"{rel_dir}/{name}" if rel_dir != "." else name)
            if is_ignored(rel, patterns):
                continue
            full = root / rel
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield rel, full


def scan_content(root: Path, allowlist: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """返回 ``(problems, warnings)``；warnings 是被豁免清单放行的命中。"""
    allowlist = allowlist or {}
    patterns = load_gitignore_patterns(root)
    problems: list[str] = []
    warnings: list[str] = []
    hit_paths: set[str] = set()
    for rel, full in iter_scannable_files(root, patterns):
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制或不可读，跳过
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 4000:
                continue
            for rule_name, regex, min_len in PATTERNS:
                for match in regex.finditer(line):
                    secret = match.group(1)
                    if not is_probable_secret(secret, min_len):
                        continue
                    masked = secret[:4] + "…" + secret[-2:]
                    message = (
                        f"{rel}:{lineno} 疑似泄露密钥[{rule_name}]：{masked}"
                        f"（{len(secret)} 字符）"
                    )
                    if rel in allowlist:
                        hit_paths.add(rel)
                        warnings.append(f"{message} —— 已豁免：{allowlist[rel]}")
                    else:
                        problems.append(message)
    for rel in sorted(set(allowlist) - hit_paths):
        problems.append(
            f"{ALLOWLIST_FILE} 存在陈旧条目：{rel} 已不再命中任何规则，请删除该行。"
        )
    return problems, warnings


def run(root: Path, forbid_present: bool, use_allowlist: bool = True) -> tuple[list[str], list[str]]:
    problems = check_gitignore_coverage(root)
    if forbid_present:
        problems += check_denylist_absent(root)
    allowlist = load_allowlist(root) if use_allowlist else {}
    content_problems, warnings = scan_content(root, allowlist)
    problems += content_problems
    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仓库密钥泄露扫描")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[2]),
        help="仓库根目录，默认按本脚本位置推导",
    )
    parser.add_argument(
        "--forbid-present",
        action="store_true",
        help="DENYLIST 文件一旦存在即失败（CI 用；本地开发机上这些文件本就存在）",
    )
    parser.add_argument(
        "--no-allowlist",
        action="store_true",
        help="忽略豁免清单，把历史债也算成失败（审计用）",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"仓库根目录不存在：{root}", file=sys.stderr)
        return 2

    problems, warnings = run(
        root, forbid_present=args.forbid_present, use_allowlist=not args.no_allowlist
    )
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if problems:
        print(f"密钥扫描未通过，共 {len(problems)} 项：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"密钥扫描通过（{len(warnings)} 项已豁免的历史债）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
