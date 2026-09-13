# -*- coding: utf-8 -*-
"""工程路径的唯一解析入口。

历史代码里散落着 ``/Users/<开发者用户名>/DataWareHouse-Agent`` 这类**开发机绝对路径**，
换机器或进容器（镜像里代码在 ``/app``）后全部失效：``llm_config.json`` 读不到、
数仓开发 Agent 的产物目录建到不存在的盘符上、``/developer/file`` 接口一律 404。

这里按「包结构推导 + 环境变量可覆盖」两级解析：

1. 环境变量优先——容器化部署时用 ``DWH_REPO_ROOT`` / ``DWH_BACKEND_DIR`` /
   ``DWH_LLM_CONFIG_PATH`` 显式指定；
2. 未设置则回落到相对本文件的包结构推导，任何 checkout 位置都自洽。

返回值一律是已 ``resolve()`` 的绝对 :class:`~pathlib.Path`，可直接参与前缀比较。
"""
from __future__ import annotations

import os
from pathlib import Path

# 本文件位于 <repo>/backend/app/core/paths.py：
#   parents[0] = app/core   parents[1] = app   parents[2] = backend   parents[3] = <repo>
_THIS_FILE = Path(__file__).resolve()
_DEFAULT_BACKEND_DIR = _THIS_FILE.parents[2]
_DEFAULT_REPO_ROOT = _THIS_FILE.parents[3]

#: 仓库根目录覆盖项（数仓开发 Agent 的产物工作区、/developer/file 的沙箱根）
ENV_REPO_ROOT = "DWH_REPO_ROOT"
#: backend 包根目录覆盖项
ENV_BACKEND_DIR = "DWH_BACKEND_DIR"
#: 大模型供应商配置文件覆盖项（整条路径，不只是目录）
ENV_LLM_CONFIG_PATH = "DWH_LLM_CONFIG_PATH"


def _from_env(name: str) -> Path | None:
    """读取环境变量并规范化；未设置或空白视为未覆盖。"""
    raw = os.environ.get(name) or ""
    raw = raw.strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def backend_dir() -> Path:
    """backend 包所在目录（``<repo>/backend``）。"""
    return _from_env(ENV_BACKEND_DIR) or _DEFAULT_BACKEND_DIR


def repo_root() -> Path:
    """仓库根目录。数仓开发 Agent 的 init/ etl/ job/ docs/ 都挂在它下面。"""
    return _from_env(ENV_REPO_ROOT) or _DEFAULT_REPO_ROOT


def llm_config_path() -> Path:
    """大模型供应商配置文件路径（默认 ``<backend>/llm_config.json``）。

    该文件含 API Key，已在 .gitignore 里，首次使用需
    ``cp backend/llm_config.example.json backend/llm_config.json``。
    """
    return _from_env(ENV_LLM_CONFIG_PATH) or (backend_dir() / "llm_config.json")


class PathEscapeError(ValueError):
    """相对路径试图逃逸出允许的根目录。"""


def resolve_within(base: str | os.PathLike[str], relative: str) -> Path:
    """把 ``relative`` 解析到 ``base`` 之内，越权则抛 :class:`PathEscapeError`。

    比旧的 ``normpath(join(...)) + startswith(base)`` 写法严格：
    ``startswith`` 是**字符串**前缀判断，``../DataWareHouse-Agent-evil/x`` 归一化后
    仍以 ``/…/DataWareHouse-Agent`` 开头，会被误放行。这里改成逐级父目录比对，
    并且 ``resolve()`` 会跟随符号链接，指向根外的软链同样拦得住。

    只收紧、不放松：原先能拒的（绝对路径、``../`` 上跳）现在照样拒。
    """
    base_path = Path(base).expanduser().resolve()
    # 先用 normpath 处理 ".."，再 resolve 跟随符号链接；两步都做才既拦得住
    # 纯字符串上跳，也拦得住软链外指。
    joined = os.path.normpath(os.path.join(str(base_path), str(relative)))
    candidate = Path(joined).resolve()
    if candidate != base_path and base_path not in candidate.parents:
        raise PathEscapeError(f"路径 {relative!r} 超出允许的根目录 {base_path}")
    return candidate
