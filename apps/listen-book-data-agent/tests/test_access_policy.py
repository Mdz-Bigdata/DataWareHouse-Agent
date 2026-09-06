"""Fail-closed access policy resolution and audit metadata tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.models.mysql.user_mysql import UserMySQL
from app.services.access_policy import AccessPolicyError, resolve_access_policy


def _user(*, role: str = "user", data_scope: str | None = None) -> UserMySQL:
    return UserMySQL(
        id="user-1",
        username="tester",
        password_hash="",
        role=role,
        must_change_password=False,
        data_scope=data_scope,
    )


def _structured_policy(**overrides) -> str:
    payload = {
        "policy_version": "policy-7",
        "domain": "audio",
        "datasource": "audio_full",
        "table_acl": {"audio_album": ["id", "title", "region"]},
        "row_predicates": [{"table": "audio_album", "column": "region", "variable": "region"}],
        "variables": {"region": {"value": "华东", "expires_at": "2030-01-01T00:00:00Z"}},
        "function_whitelist": ["count", "sum"],
        "expires_at": "2030-01-01T00:00:00Z",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_resolves_structured_policy_and_runtime_variable():
    policy = resolve_access_policy(
        _user(data_scope=_structured_policy()),
        domain="audio",
        datasource="audio_full",
        now=datetime(2029, 1, 1, tzinfo=UTC),
    )

    assert policy.policy_version == "policy-7"
    assert policy.table_acl == {"audio_album": ("id", "title", "region")}
    assert policy.function_whitelist == ("COUNT", "SUM")
    assert policy.row_level_scope() == [
        {
            "table": "audio_album",
            "column": "region",
            "operator": "eq",
            "value": "华东",
        }
    ]
    assert len(policy.policy_hash) == 64
    assert policy.admin_bypass is False


@pytest.mark.parametrize("data_scope", [None, "", "not-json", "{}", "[]"])
def test_rejects_missing_or_invalid_ordinary_user_policy(data_scope):
    with pytest.raises(AccessPolicyError):
        resolve_access_policy(_user(data_scope=data_scope), domain="audio", datasource="audio_full")


def test_rejects_expired_policy_and_variable():
    with pytest.raises(AccessPolicyError, match="访问策略已过期"):
        resolve_access_policy(
            _user(data_scope=_structured_policy(expires_at="2028-01-01T00:00:00Z")),
            domain="audio",
            datasource="audio_full",
            now=datetime(2029, 1, 1, tzinfo=UTC),
        )

    variables = {"region": {"value": "华东", "expires_at": "2028-01-01T00:00:00Z"}}
    with pytest.raises(AccessPolicyError, match="变量 region 已过期"):
        resolve_access_policy(
            _user(data_scope=_structured_policy(variables=variables)),
            domain="audio",
            datasource="audio_full",
            now=datetime(2029, 1, 1, tzinfo=UTC),
        )


def test_rejects_missing_or_unused_variables():
    with pytest.raises(AccessPolicyError, match="缺失或引用冲突"):
        resolve_access_policy(
            _user(data_scope=_structured_policy(variables={})),
            domain="audio",
            datasource="audio_full",
        )

    variables = {
        "region": {"value": "华东", "expires_at": "2030-01-01T00:00:00Z"},
        "tenant": {"value": "t-1", "expires_at": "2030-01-01T00:00:00Z"},
    }
    with pytest.raises(AccessPolicyError, match="未使用变量"):
        resolve_access_policy(
            _user(data_scope=_structured_policy(variables=variables)),
            domain="audio",
            datasource="audio_full",
            now=datetime(2029, 1, 1, tzinfo=UTC),
        )


def test_rejects_wildcard_acl_and_predicate_outside_acl():
    with pytest.raises(AccessPolicyError, match="不允许通配符"):
        resolve_access_policy(
            _user(data_scope=_structured_policy(table_acl={"*": ["*"]})),
            domain="audio",
            datasource="audio_full",
        )

    predicates = [{"table": "audio_album", "column": "tenant_id", "value": "tenant-1"}]
    with pytest.raises(AccessPolicyError, match="未授权字段"):
        resolve_access_policy(
            _user(
                data_scope=_structured_policy(
                    row_predicates=predicates,
                    variables={},
                )
            ),
            domain="audio",
            datasource="audio_full",
        )


def test_admin_bypass_is_explicit_stable_and_publicly_auditable():
    first = resolve_access_policy(_user(role="admin"), domain="audio", datasource="audio_full")
    second = resolve_access_policy(_user(role="admin"), domain="audio", datasource="audio_full")

    assert first.admin_bypass is True
    assert first.policy_version == "admin-bypass-v1"
    assert first.policy_hash == second.policy_hash
    assert first.public_metadata()["policy_admin_bypass"] is True


def test_non_empty_legacy_scope_remains_restricted_and_auditable():
    policy = resolve_access_policy(
        _user(data_scope='[{"column":"region","value":"华东"}]'),
        domain="audio",
        datasource="audio_full",
    )

    assert policy.policy_version == "legacy-row-scope-v1"
    assert policy.row_level_scope()[0]["column"] == "region"
    assert policy.admin_bypass is False
