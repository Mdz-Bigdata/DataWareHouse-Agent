"""物理 SQL 层的密级执法：人工显式登记为 L3 的列必须硬拦，而不是只观察。

背景：guardrail 的 ``sql.sensitive_column`` 默认 severity=warning（观察期），
理由是系统内部调用（画像/剖析）也走这条路，一刀切会打断元数据发现。
但这导致 ``config/sensitive_columns.json`` 里被**人工显式登记**为 L3 的支付账户列
（users.bank_card_info / wechat_info / alipay_info）对非 admin 角色实际是放行的——
显式登记代表有人明确认定这是金融级数据，只观察不拦截等于把这条安全声明作废。

修复方式是用 guardrail 自己文档化的按表灰度机制，把含显式 L3 列的表登记为 error。
本文件钉住修复不被回退，并防止目录新增 L3 表却漏登记执法。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.service.guardrail import Guardrail, GuardrailException

_CONFIG = Path(__file__).resolve().parents[1] / "config"
_RULE = "sql.sensitive_column"


def _catalog() -> dict:
    return json.loads((_CONFIG / "sensitive_columns.json").read_text(encoding="utf-8"))


def _l3_tables() -> set[str]:
    """目录里被人工显式登记为 L3 的列所在的表。"""
    return {
        entry["column"].split(".")[0]
        for entry in _catalog()["columns"]
        if entry.get("level") == "L3" and "." in entry.get("column", "")
    }


def _rules() -> dict:
    return json.loads((_CONFIG / "guardrail_rules.json").read_text(encoding="utf-8"))["rules"]


@pytest.fixture(scope="module")
def guard() -> Guardrail:
    return Guardrail()


def test_every_curated_l3_table_is_enforced():
    """★防漂移★ 目录里新增 L3 表却忘了登记执法，这条会红。"""
    enforced = _rules().get(_RULE, {}).get("tables", {})
    missing = sorted(t for t in _l3_tables() if enforced.get(t) != "error")
    assert missing == [], (
        f"这些表含人工显式登记的 L3 列，但 {_RULE} 没把它们登记成 error：{missing}。"
        f"只观察不拦截 = 安全声明作废。"
    )


def test_star_severity_is_error_not_warning():
    """SELECT * 会把整表 L3 列一并带出，比显式点名单列更该拦。"""
    assert _rules().get(_RULE, {}).get("star_severity") == "error", (
        "star_severity 留在 warning 等于开了一道比正门更宽的后门"
    )


@pytest.mark.parametrize("role", ["user", "analyst"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT bank_card_info FROM users",
        "SELECT wechat_info, alipay_info FROM users",
        "SELECT * FROM users",
        "SELECT account_info FROM withdrawal_requests",
    ],
)
def test_unauthorized_roles_are_blocked(guard: Guardrail, role: str, sql: str):
    with pytest.raises(GuardrailException):
        guard.check_sql(sql, dialect="postgres", user_role=role)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT bank_card_info FROM users",
        "SELECT * FROM users",
        "SELECT account_info FROM withdrawal_requests",
    ],
)
def test_admin_still_passes(guard: Guardrail, sql: str):
    """L3 对 admin 开放——执法不能把有权限的人也挡住。"""
    assert guard.check_sql(sql, dialect="postgres", user_role="admin")["ok"] is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, username FROM users",          # 同表非敏感列
        "SELECT * FROM articles",                  # 无敏感列的表
        "SELECT title FROM articles",
    ],
)
def test_no_false_positives(guard: Guardrail, sql: str):
    """不能过度拦截：同表的非敏感列、无敏感列的表都要照常放行。"""
    assert guard.check_sql(sql, dialect="postgres", user_role="user")["ok"] is True


def test_profiling_degrades_gracefully_instead_of_crashing():
    """内部画像读 L3 表会被拦，这是预期行为，但必须降级而不是崩。

    不依赖全局活动数据源：本用例自己确认 ``users`` 在当前源里可见，
    不可见就明确 skip 而不是假装通过。（全量跑时前置用例可能切过源——
    见 test_active_source_is_not_leaked_between_tests。）
    """
    from app.service.metadata_enricher import MetadataEnricher

    enricher = MetadataEnricher()
    try:
        result = enricher.profile_table("users")
    except Exception as exc:  # noqa: BLE001 - 要按类型分诊
        if type(exc).__name__ == "UnsafeTableNameError":
            pytest.skip(f"当前活动数据源里没有 users 表，本用例不适用：{exc}")
        raise AssertionError(f"画像被拦后应降级，却抛出了未捕获异常：{type(exc).__name__}: {exc}") from exc
    assert isinstance(result, dict), "被拦之后应返回降级结果，不应抛出未捕获异常"


def test_the_block_itself_is_a_guardrail_rejection_not_a_crash():
    """与上一条互补：直接验「网闸拒绝」这条路径本身是受控的。

    不经过画像器的表名白名单，直接把一条必然命中 L3 的 SQL 交给统一安全出口，
    断言它抛的是 GuardrailException（可被调用方识别并降级），
    而不是 KeyError / AttributeError 这类会把调用栈炸穿的意外异常。
    """
    guard = Guardrail()
    with pytest.raises(GuardrailException):
        guard.check_sql("SELECT * FROM users", dialect="mysql", user_role="user")
