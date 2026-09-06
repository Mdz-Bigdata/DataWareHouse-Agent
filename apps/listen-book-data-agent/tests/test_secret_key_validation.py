"""JWT 签名密钥启动期校验的纯函数测试。

覆盖三种环境 × 两类弱密钥的组合，以及合规密钥的放行路径。
lifespan 集成由现有启动流程间接覆盖，这里聚焦校验逻辑本身。
"""

from __future__ import annotations

import pytest
from loguru import logger

from app.core.security import validate_secret_key

# 一条足够长且非默认值的合规密钥，供放行用例使用。
_STRONG_SECRET = "x" * 48


class _MemorySink:
    """loguru sink：把 format 后的日志消息收集到列表，便于断言。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message) -> None:
        self.messages.append(str(message))


@pytest.fixture
def captured_warnings():
    """临时挂一个 loguru sink 收集 WARNING 级别日志，用例结束后移除。"""
    sink = _MemorySink()
    handler_id = logger.add(sink, level="WARNING")
    try:
        yield sink
    finally:
        logger.remove(handler_id)


def test_strong_secret_passes_in_production():
    # 合规密钥在任何环境都不抛、不告警。
    validate_secret_key(_STRONG_SECRET, "production")


def test_weak_default_passes_in_dev_with_warning(captured_warnings):
    # 开发环境命中硬编码默认值：放行但记 warning。
    validate_secret_key("dev-only-secret-change-me", "development")
    assert any("AUTH_SECRET_KEY 校验失败" in msg for msg in captured_warnings.messages)


def test_weak_default_rejected_in_production():
    # 生产环境命中硬编码默认值：拒绝启动。
    with pytest.raises(RuntimeError, match="硬编码默认值"):
        validate_secret_key("dev-only-secret-change-me", "production")


def test_short_secret_rejected_in_production():
    # 生产环境短密钥：拒绝启动。
    with pytest.raises(RuntimeError, match="长度 10 字节"):
        validate_secret_key("0123456789", "production")


def test_short_secret_passes_in_dev_with_warning(captured_warnings):
    # 开发环境短密钥：放行但记 warning。
    validate_secret_key("short-key", "dev")
    assert any("AUTH_SECRET_KEY 校验失败" in msg for msg in captured_warnings.messages)


@pytest.mark.parametrize(
    "env",
    ["development", "DEV", "test", "testing", "local", "Development"],
)
def test_dev_environment_aliases_are_case_insensitive(env):
    # 开发环境别名大小写不敏感，均应放行短密钥。
    validate_secret_key("short-key", env)


def test_staging_treated_as_production():
    # 非开发环境别名（如 staging）按生产语义拒绝弱密钥。
    with pytest.raises(RuntimeError):
        validate_secret_key("short-key", "staging")
