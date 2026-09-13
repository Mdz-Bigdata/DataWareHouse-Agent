"""冒烟：数据质量门禁——拦得住、找得回、修得好。

主流程：规则中心 → 逐条检查 → 拒绝的进隔离表 → 复验重入湖。
门禁自身不能成为瓶颈：跨源查询、文件探针这类旁路依赖缺席时只跳过，不阻断。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.quality import (
    BUILTIN_RULES,
    Channel,
    Disposition,
    InMemoryIssueStore,
    IssueLevel,
    QualityGate,
    Severity,
    default_rule_center,
    format_quality_flag,
    unregistered_tables,
    verify_id_patterns,
)

pytestmark = pytest.mark.smoke

TABLE = "ods_vehicle_trigger_event"
NOW = datetime(2026, 3, 1, 12, 30, 45)
DATA_ID = "COLLECT_BP_20260301123045_b7e2"


def _record(**overrides) -> dict:
    row = {
        "data_id": DATA_ID,
        "event_id": "EVT-0001",
        "trigger_type": "aeb",
        # 回传域该表的时间列在 catalog 里叫 trigger_time；event_time 是跨表通用
        # 新鲜度规则（QG-COM-009，table="*"）的逻辑字段名，两者不是同一个东西
        "trigger_time": NOW,
        "event_time": NOW,
        "vehicle_manufacture_time": datetime(2024, 1, 1),
        "pre_trigger_seconds": 10.0,
        "post_trigger_seconds": 10.0,
    }
    row.update(overrides)
    return row


def _gate(store: InMemoryIssueStore | None = None) -> QualityGate:
    return QualityGate(
        store=store if store is not None else InMemoryIssueStore(),
        source_system="kafka:vehicle.trigger.event",
        clock=lambda: NOW,
    )


# --------------------------------------------------------------------------- 主流程


def test_clean_record_passes_without_any_hit():
    decision = _gate().check(TABLE, _record(), channel=Channel.KAFKA)
    assert decision.accepted
    assert not decision.rejected
    assert not decision.flagged
    assert decision.hits == ()


def test_malformed_data_id_is_rejected_and_isolated():
    """P1 硬规则：ID 格式非法一律拒绝入湖，并落隔离表等处置——拦得住 + 找得回。"""
    store = InMemoryIssueStore()
    gate = _gate(store)

    decision = gate.check(TABLE, _record(data_id="NOT_AN_ID"), channel=Channel.KAFKA)

    assert decision.rejected
    assert not decision.accepted
    assert any(h.rule_id == "QG-COM-002-data-id-format" for h in decision.hits)
    assert decision.worst_level is IssueLevel.P1
    assert store.count() == 1


def test_null_primary_key_is_a_p0_rejection():
    decision = _gate().check(TABLE, _record(data_id=None), channel=Channel.KAFKA)
    assert decision.rejected
    assert any(h.rule_id == "QG-COM-001-data-id-not-null" for h in decision.hits)
    assert decision.worst_level is IssueLevel.P0


def test_warning_level_hit_flags_the_row_but_lets_it_in():
    """告警标记 ≠ 拦截：片段截断只打标放行，下游自己决定要不要用。"""
    decision = _gate().check(TABLE, _record(pre_trigger_seconds=1.0), channel=Channel.KAFKA)

    assert decision.accepted
    assert decision.flagged
    assert any(h.rule_id == "QG-KFK-003-clip-truncated-pre" for h in decision.hits)

    row = _record(pre_trigger_seconds=1.0)
    flagged = decision.apply_flag(row)
    assert flagged["_quality_flag"]
    assert row is not flagged  # 不就地改调用方的记录


def test_out_of_dictionary_enum_is_rejected_to_protect_the_partition():
    """trigger_type 是分区字段，非法值会产生脏分区——必须在门禁挡住。"""
    decision = _gate().check(TABLE, _record(trigger_type="ufo"), channel=Channel.KAFKA)
    assert decision.rejected
    assert any(h.rule_id == "QG-KFK-007-trigger-type-enum" for h in decision.hits)


def test_isolate_false_skips_writing_a_second_issue_row():
    """复验重入湖走同一套规则，但不该再刷一条新的隔离记录。"""
    store = InMemoryIssueStore()
    gate = _gate(store)
    bad = _record(data_id="NOT_AN_ID")

    gate.check(TABLE, bad, channel=Channel.KAFKA)
    assert store.count() == 1
    gate.check(TABLE, bad, channel=Channel.KAFKA, isolate=False)
    assert store.count() == 1


# --------------------------------------------------------------------------- 批量


def test_check_batch_splits_accepted_from_rejected():
    gate = _gate()
    records = [
        _record(event_id="EVT-1"),
        _record(event_id="EVT-2", data_id="NOT_AN_ID"),
        _record(event_id="EVT-3"),
    ]

    batch = gate.check_batch(TABLE, records, channel=Channel.KAFKA)

    assert len(batch.accepted) == 2
    assert len(batch.rejected) == 1
    assert len(batch.issues) == 1
    assert len(batch.accepted_rows(records)) == 2


# --------------------------------------------------------------------------- 规则中心


def test_builtin_rules_are_registered_and_wellformed():
    center = default_rule_center()
    assert BUILTIN_RULES
    seen = set()
    for rule in BUILTIN_RULES:
        assert rule.rule_id not in seen, f"规则 ID 重复: {rule.rule_id}"
        seen.add(rule.rule_id)
        assert rule.table and rule.check and rule.source
        assert isinstance(rule.severity, Severity)
        assert isinstance(rule.issue_level, IssueLevel)

    applicable = center.rules_for(TABLE, Channel.KAFKA)
    assert applicable, "内置规则集里没有一条适用于 " + TABLE
    assert all(r.table in ("*", TABLE) for r in applicable)


def test_error_rules_reject_and_warning_rules_flag():
    """严重度与处置动作必须一致：ERROR → REJECT，WARNING → ALLOW_WITH_FLAG。

    这条如果松了，「硬规则拦截 / 软规则打标」的两分法就名存实亡。
    """
    assert set(Disposition) == {
        Disposition.REJECT,
        Disposition.ALLOW_WITH_FLAG,
        Disposition.ACCEPTED,
    }
    for rule in BUILTIN_RULES:
        if rule.severity is Severity.ERROR:
            assert rule.disposition_hint == Disposition.REJECT, rule.rule_id
        else:
            assert rule.severity is Severity.WARNING
            assert rule.disposition_hint == Disposition.ALLOW_WITH_FLAG, rule.rule_id


def test_id_pattern_rules_agree_with_the_ids_module():
    """门禁里的 ID 正则必须跟 ids 模块同源，否则两边会各判各的。"""
    result = verify_id_patterns()
    assert result
    assert all(result.values()), f"ID 正则与 ids 模块不一致: {result}"


def test_every_rule_table_is_registered_in_the_catalog():
    """规则挂载点必须逐字落在 catalog.registry 上。

    门禁规则挂在一张不存在的表上，不会在装配期报错，只会在跑规则时静默不命中——
    这是最难发现的一类失效，所以这里断言为空而不是维护一张「已知缺口」名单。
    """
    assert unregistered_tables() == []


#: 规则挂载点已登记、但**字段**还没进 catalog 的缺口（表名 → 字段）。
#:
#: 这些字段是门禁自己算出来的质量指标 / 跨表冗余结论，原文的门禁章节要求它们存在，
#: 但 catalog 的 ODS 表定义里还没有——规则跑起来会恒取 NULL，即静默不生效，
#: 是最难发现的一类失效。当时列的两条修法里，最终走的是 A：
#:   A. 把这些指标列补进对应 ODS 表（入湖时由门禁算好落列，与 parent_artifact_id
#:      「冗余落表」同一路数）；
#:   B. 把规则改挂到真正持有该字段的表上（如 qc_conclusion 本在 ods_qc_result）。
#: 全部缺口已按 A 补进 catalog/tables/_*.py，故这张表现在是空的——
#: 它只能变短不能变长，空了就该一直空着。
KNOWN_MISSING_RULE_FIELDS: dict[str, set[str]] = {}


def _rule_column_refs(rule) -> set[tuple[str, str]]:
    """一条规则实际会去读的 (表, 列)。

    ``RuleSpec.field`` 只是其中最显眼的一个。规则还会通过 ``when`` 的前置条件字段、
    以及 ``params`` 里那些「值是列名」的键去读记录——它们同样是列不存在就恒取 NULL。
    早期这个断言只看 field，于是 QG-OSS-001 的双合规标记、QG-OSS-006 的 decodable_flag、
    QG-KFK-002 的出厂时间下界、QG-COM-007 的血缘对账目标列一起从网眼里漏了过去。
    """
    #: params 里「值是一个列名」的键（取自 quality/rules.py 各检查器的读法）
    single = (
        "not_before_field",
        "from_field",
        "checksum_field",
        "payload_field",
        "flag_field",
        "group_field",
    )
    #: params 里「值是一串列名」的键
    plural = ("required_fields", "key_fields", "flags")

    refs: set[tuple[str, str]] = set()
    params = dict(rule.params or {})

    def add(table: str, col: str) -> None:
        if table and table != "*" and col:
            refs.add((table, str(col)))

    add(rule.table, rule.field or "")
    if rule.when and rule.when.get("field"):
        add(rule.table, str(rule.when["field"]))
    for key in single:
        if params.get(key):
            add(rule.table, str(params[key]))
    for key in plural:
        for col in params.get(key) or ():
            add(rule.table, str(col))
    # 跨表引用：目标列在目标表上，不在规则挂载的表上
    if params.get("target_table") and params.get("target_field"):
        add(str(params["target_table"]), str(params["target_field"]))
    return refs


def test_every_rule_field_exists_on_its_table_or_is_a_pinned_gap():
    """规则读的每一列都要真的在那张表上，否则规则永远取到 NULL、静默不生效。

    覆盖 field / when.field / params 里的列名引用三类（见 :func:`_rule_column_refs`）。
    已知缺口见 :data:`KNOWN_MISSING_RULE_FIELDS`；这里同时断言两件事：
    没有新的缺口冒出来，且名单里的每一条都还真的是缺口（修好了就要从名单里删）。
    """
    from adas_lakehouse.catalog import registry
    from adas_lakehouse.quality.builtin import default_rule_center

    cols = {t.name: {c.name for c in t.all_columns()} for t in registry.all_tables()}
    missing: dict[str, set[str]] = {}
    for r in default_rule_center():
        for table, col in _rule_column_refs(r):
            if col not in cols.get(table, set()):
                missing.setdefault(table, set()).add(col)

    new = {t: sorted(f - KNOWN_MISSING_RULE_FIELDS.get(t, set())) for t, f in missing.items()}
    new = {t: f for t, f in new.items() if f}
    assert not new, f"出现了新的「规则字段不在注册表里」缺口: {new}"

    stale = {t: sorted(f - missing.get(t, set())) for t, f in KNOWN_MISSING_RULE_FIELDS.items()}
    stale = {t: f for t, f in stale.items() if f}
    assert not stale, f"这些缺口已经补上了，请从 KNOWN_MISSING_RULE_FIELDS 删掉: {stale}"


def test_describe_rules_lists_the_rules_that_apply_to_a_table():
    described = _gate().describe_rules(TABLE)
    ids = {r["rule_id"] for r in described}
    assert "QG-COM-002-data-id-format" in ids
    assert "QG-KFK-007-trigger-type-enum" in ids


# --------------------------------------------------------------------------- 隔离表与标记


def test_issue_store_round_trip():
    store = InMemoryIssueStore()
    gate = _gate(store)
    gate.check(TABLE, _record(data_id="NOT_AN_ID"), channel=Channel.KAFKA)

    issues = list(store.iter_all())
    assert len(issues) == 1
    issue = issues[0]
    assert store.get(issue.issue_id) is not None

    store.clear()
    assert store.count() == 0


def test_quality_flag_formatting_is_stable():
    assert format_quality_flag([]) == ""
    decision = _gate().check(TABLE, _record(pre_trigger_seconds=1.0), channel=Channel.KAFKA)
    flag = format_quality_flag(decision.hits)
    assert "QG-KFK-003-clip-truncated-pre" in flag
