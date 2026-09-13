"""pytest 共享装配。

唯一职责：在未 `pip install -e .` 的裸仓库里也能 `python3 -m pytest tests/ -q`。
src layout 下 `adas_lakehouse` 在 src/ 里，这里把它顶到 sys.path 最前。

测试全程**不连任何外部服务**——没有 Flink、没有 StarRocks、没有 Neo4j、没有 Kafka、
没有 MinIO，也不装 pymysql / boto3 / neo4j 这些驱动。所有跨进程边界都用假对象顶替。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# --------------------------------------------------------------------------- 公共假数据

#: 一条固定的合法 clip 锚点。时间戳取自 ids 模块 docstring 的风格（yyyyMMddHHmmss）。
SAMPLE_DATA_ID = "COLLECT_BP_20260301123045_b7e2"

#: 固定时刻——所有涉及 now() 的断言都注入它，避免测试随挂钟漂移。
FROZEN_NOW = datetime(2026, 3, 1, 12, 30, 45)


@pytest.fixture()
def repo_root() -> Path:
    return _ROOT


@pytest.fixture()
def data_id() -> str:
    return SAMPLE_DATA_ID


@pytest.fixture()
def now() -> datetime:
    return FROZEN_NOW
