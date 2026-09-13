"""§7 收口：执行验证的回归守护 + 口径选边的钉子 + 四项遗留缺口的闸门。

本文件守的是 2026-09-13 那一轮「真的把 SQL 跑到引擎上」之后补的东西。分五组：

``TestVariantEngineGap``
    §7.7 实测暴露的唯一硬缺口：``VARIANT`` 列要 Flink 2.1+，本仓 Dockerfile 钉的是
    1.20.1，88 张表在真实 Flink/Paimon 上只能建成 87 张。守两件事——降级开关默认关闭
    （契约产物逐字节不变），以及 Dockerfile 与 ``VARIANT_MIN_ENGINE`` 的版本关系
    没有被人悄悄改掉。

``TestControlPlaneRebuild``
    §7.7 控制面自检：原先的 ``rebuild_check`` 是自我指涉的恒真断言。这里守新加的
    模式层自检**真的会因为控制面多一张表 / 多一列主数据而失败**——一条恒真的断言
    守不住任何东西，所以必须有反例。

``TestLineageNodeLabels`` / ``TestRetentionConflict``
    §7.4 两处口径选边的钉子：选了边就要钉住，否则下次改回去没人知道。

``TestTagAliasArbitration`` / ``TestCoverageDailyColumns`` / ``TestReleaseGate``
    / ``TestSystemFieldMount``
    §7.2 四项遗留的闸门。前者守的是一个真实存在过的 bug：别名冲突被拒时，
    字典的倒排索引已经被改了一半。
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from adas_lakehouse.catalog import registry
from adas_lakehouse.catalog.spec import (
    SYSTEM_COLUMNS,
    SYSTEM_FIELD_SPEC,
    VARIANT_MIN_ENGINE,
    VARIANT_TYPE,
    Column,
)
from adas_lakehouse.controlplane.audit import (
    CONTROL_PLANE_TABLES,
    audit_control_plane_schema,
    parse_control_plane_schema,
)
from adas_lakehouse.controlplane.scheduler import ControlPlane
from adas_lakehouse.controlplane.store import CONTROL_PLANE_MYSQL_DDL
from adas_lakehouse.domains import Layer
from adas_lakehouse.lifecycle.policy import (
    COLD_TO_ARCHIVE_NO_ACCESS_DAYS,
    DEEP_ARCHIVE_NO_ACCESS_DAYS,
    RETENTION_SCHEDULE,
    TIER_MODEL_THRESHOLDS,
    DataType,
)
from adas_lakehouse.lifecycle.tiers import LifecycleStage, StorageMedia
from adas_lakehouse.lineage.constants import NODE_LABEL_COUNT
from adas_lakehouse.lineage.model import NodeLabel
from adas_lakehouse.tags.coverage import (
    compute_daily_coverage,
    fill_coverage_trend,
)
from adas_lakehouse.tags.dictionary import default_dictionary
from adas_lakehouse.vector.params import SEARCH_P95_SLA_SECONDS
from adas_lakehouse.vector.schema import VectorStatus
from adas_lakehouse.vector.versioning import (
    EmbeddingVersion,
    ReleaseGateBlocked,
    VersionRegistry,
    evaluate_release_gate,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VECTOR_TABLE = "dwd_mining_image_vector_detail"


# =========================================================================== §7.7 VARIANT


class TestVariantEngineGap:
    """§7.7：88 张表在 Flink 1.20.1 + Paimon 1.0.1 上只能建成 87 张，见 A-12。"""

    def test_exactly_one_table_uses_variant(self) -> None:
        """实测结论的前提：VARIANT 是**单点**缺口，不是全局不可用。

        这一条一旦变成两张表，A-12 里「87 / 88」那个数字就过期了。
        """
        users = [
            t.name
            for t in registry.all_tables()
            if any(c.type.upper() == VARIANT_TYPE for c in t.all_columns())
        ]
        assert users == [_VECTOR_TABLE]

    def test_default_export_still_emits_variant(self) -> None:
        """开关默认关闭 = 契约产物逐字节不变。降级不能是「悄悄生效」的。"""
        spec = next(t for t in registry.all_tables() if t.name == _VECTOR_TABLE)
        assert f"`vector_meta` {VARIANT_TYPE}" in spec.render_ddl()

    def test_fallback_replaces_only_the_variant_column(self) -> None:
        """降级只动 VARIANT 列的类型，别的列一个字都不许变。"""
        spec = next(t for t in registry.all_tables() if t.name == _VECTOR_TABLE)
        plain = spec.render_ddl()
        downgraded = spec.render_ddl(variant_fallback_type="STRING")

        assert f"`vector_meta` {VARIANT_TYPE}" not in downgraded
        assert "`vector_meta` STRING" in downgraded
        # 逐行比对：除了 vector_meta 那一行，其余必须完全一致
        differing = [
            (a, b)
            for a, b in zip(plain.splitlines(), downgraded.splitlines(), strict=True)
            if a != b
        ]
        assert len(differing) == 1
        assert "vector_meta" in differing[0][0]

    def test_fallback_leaves_a_visible_trace(self) -> None:
        """降级产物必须看得出是降级产物，否则会被人当契约提交回仓库。"""
        spec = next(t for t in registry.all_tables() if t.name == _VECTOR_TABLE)
        line = next(
            ln
            for ln in spec.render_ddl(variant_fallback_type="STRING").splitlines()
            if "`vector_meta`" in ln
        )
        assert "降级" in line and VARIANT_TYPE in line

    def test_fallback_is_a_noop_for_every_other_table(self) -> None:
        """87 张不含 VARIANT 的表，开不开降级都必须渲染出同一份 DDL。"""
        for spec in registry.all_tables():
            if spec.name == _VECTOR_TABLE:
                continue
            assert spec.render_ddl() == spec.render_ddl(variant_fallback_type="STRING")

    def test_pinned_flink_version_vs_variant_requirement(self) -> None:
        """Dockerfile 钉的 Flink 版本 vs VARIANT 的最低要求——版本关系不许无声漂移。

        今天的事实是 1.20.1 < 2.1，所以降级开关必须存在。哪天有人把 Flink 升上去，
        这条会失败并提示：降级开关和 source-deviations A-12 可以一起撤了。
        """
        dockerfile = (_REPO_ROOT / "docker" / "flink" / "Dockerfile").read_text(encoding="utf-8")
        match = re.search(r"^ARG FLINK_VERSION=(\d+)\.(\d+)", dockerfile, re.MULTILINE)
        assert match is not None, "docker/flink/Dockerfile 里找不到 ARG FLINK_VERSION"
        pinned = (int(match.group(1)), int(match.group(2)))

        required_major, required_minor = VARIANT_MIN_ENGINE["flink"].split(".")
        required = (int(required_major), int(required_minor))

        assert pinned < required, (
            f"Dockerfile 已把 Flink 钉到 {pinned}，达到/超过 VARIANT 要求的 {required}："
            "请重跑一次 88 张表的 DDL 确认 VARIANT 可建表，然后撤掉 --variant-fallback "
            "开关并更新 source-deviations A-12"
        )

    def test_requirement_agrees_with_vector_subsystem(self) -> None:
        """catalog 与 vector 两处各写了一份引擎要求，不许对不上。

        两边字面量差一个 ``+``：vector 侧写给人看（``2.1+``），catalog 侧要被
        ``test_pinned_flink_version_vs_variant_requirement`` 拿去做数值比较，
        只能是裸版本号。比较时归一化掉这个后缀，版本数字本身必须一致。
        """
        from adas_lakehouse.vector.variant import SQL_ENGINE_REQUIREMENT

        for key in ("flink", "spark"):
            assert VARIANT_MIN_ENGINE[key] == SQL_ENGINE_REQUIREMENT[key].rstrip("+")
        assert VARIANT_MIN_ENGINE["file_format"] == SQL_ENGINE_REQUIREMENT["file_format"]


# =================================================================== §7.7 控制面自检


class TestControlPlaneRebuild:
    """§7.7：a4 的硬判据「清空平台库重建，业务数据是否完好」。"""

    def test_schema_parse_covers_every_declared_table(self) -> None:
        parsed = parse_control_plane_schema()
        assert set(parsed) == set(CONTROL_PLANE_TABLES)
        assert all(cols for cols in parsed.values()), "解析出空列的表说明解析器坏了"

    def test_indexes_are_not_mistaken_for_columns(self) -> None:
        """PRIMARY KEY / KEY / UNIQUE KEY 行里的反引号是列引用，不是列定义。"""
        parsed = parse_control_plane_schema()
        assert "idx_state_priority" not in parsed["cp_task"]
        assert "uk_idempotency" not in parsed["cp_task"]
        assert "task_id" in parsed["cp_task"]

    def test_current_schema_passes(self) -> None:
        audit = audit_control_plane_schema()
        assert audit.passed, [f.detail for f in audit.findings]

    def test_every_data_plane_asset_anchors_to_a_real_lakehouse_table(self) -> None:
        """「业务数据完好」要能指得出数据在哪张表——指不出就是句空话。"""
        from adas_lakehouse.controlplane.contracts import DATA_PLANE_ASSETS

        audit = audit_control_plane_schema()
        assert set(audit.data_plane_anchors) == set(DATA_PLANE_ASSETS)
        names = {t.name for t in registry.all_tables()}
        assert set(audit.data_plane_anchors.values()) <= names

    # ---- 反例：一条永远为真的断言守不住任何东西，所以必须证明它会失败 ----

    def test_unregistered_control_plane_table_fails(self) -> None:
        """控制面顺手多建一张表 —— 原文点名的「数据副本」第一步。"""
        ddl = CONTROL_PLANE_MYSQL_DDL + (
            "\nCREATE TABLE IF NOT EXISTS `cp_clip_cache` (\n"
            "  `data_id` VARCHAR(96) NOT NULL,\n"
            "  `clip_meta` TEXT,\n"
            "  PRIMARY KEY (`data_id`)\n"
            ") ENGINE=InnoDB;\n"
        )
        audit = audit_control_plane_schema(ddl)
        assert not audit.passed
        assert any(f.rule == "table_registered" for f in audit.findings)

    def test_master_data_column_fails(self) -> None:
        """已登记的表里多一列向量本体——比多一张表更难看见。"""
        ddl = CONTROL_PLANE_MYSQL_DDL.replace(
            "  `rows_written`   BIGINT       NOT NULL DEFAULT 0,",
            "  `image_embedding` BLOB,\n  `rows_written`   BIGINT       NOT NULL DEFAULT 0,",
        )
        audit = audit_control_plane_schema(ddl)
        assert not audit.passed
        finding = next(f for f in audit.findings if f.rule == "no_master_data_column")
        assert finding.column == "image_embedding"

    def test_pointer_columns_are_not_false_positives(self) -> None:
        """指针列必须放行：``artifacts_json`` 只存 ID，拦掉它等于拦掉正确设计。"""
        audit = audit_control_plane_schema()
        assert "artifacts_json" in audit.tables["cp_task"]
        assert audit.passed

    def test_unanchored_data_plane_asset_fails(self) -> None:
        """★ 反例：某类主数据说不出落在哪张表，「业务数据完好」就没法验证。"""
        audit = audit_control_plane_schema(lakehouse_tables=frozenset({"ods_collect_task"}))
        assert not audit.passed
        rules = {f.rule for f in audit.findings}
        assert "data_plane_anchored" in rules

    def test_lakehouse_shadow_table_fails(self) -> None:
        """控制面表影射湖仓表 = 口径分裂的起点。"""
        ddl = CONTROL_PLANE_MYSQL_DDL + (
            "\nCREATE TABLE IF NOT EXISTS `dwd_mining_task_detail` (\n"
            "  `task_id` VARCHAR(96) NOT NULL,\n"
            "  PRIMARY KEY (`task_id`)\n"
            ") ENGINE=InnoDB;\n"
        )
        audit = audit_control_plane_schema(ddl)
        assert not audit.passed
        assert any(f.rule == "no_lakehouse_shadow" for f in audit.findings)

    def test_rebuild_check_carries_the_schema_verdict(self) -> None:
        """rebuild_check 的结论必须带上模式层证据，而不只是那句恒真的资产断言。"""
        check = ControlPlane().rebuild_check()
        assert check.passed
        assert check.schema_audit is not None
        assert check.schema_audit.table_count == len(CONTROL_PLANE_TABLES)
        assert check.as_dict()["schema_audit"]["passed"] is True
        # 结论里要写得出「落盘到底有多少」，否则无从复核
        assert str(check.schema_audit.column_count) in check.detail


# ====================================================================== §7.4 口径选边


class TestLineageNodeLabels:
    """§7.4 A-10：五类节点逐字照抄原文，Dataset / Model 不许出现。"""

    def test_five_labels_verbatim(self) -> None:
        assert [n.value for n in NodeLabel] == [
            "Clip",
            "Artifact",
            "Run",
            "DatasetVersion",
            "Badcase",
        ]
        assert NODE_LABEL_COUNT == len(NodeLabel) == 5

    def test_no_dataset_or_model_label(self) -> None:
        """曾被怀疑的实现侧节点集 Clip/Artifact/Run/Dataset/Model 并不存在。"""
        values = {n.value for n in NodeLabel}
        assert "Dataset" not in values
        assert "Model" not in values
        assert "Training" not in values and "Evaluation" not in values

    def test_neo4j_constraints_match_the_labels(self) -> None:
        """落盘的 Cypher 是 graph.py 的副本，两边标签必须一一对上。"""
        cypher = (_REPO_ROOT / "docker" / "neo4j" / "init-constraints.cypher").read_text(
            encoding="utf-8"
        )
        for label in NodeLabel:
            assert f"(n:`{label.value}`)" in cypher
        assert cypher.count("CREATE CONSTRAINT") == NODE_LABEL_COUNT

    def test_forward_trace_declares_the_graph_boundary(self) -> None:
        """选了边就要说出来：图库段止于 DatasetVersion，后两跳在湖仓。"""
        from adas_lakehouse.lineage.query import LineageQueryService

        doc = LineageQueryService.forward_trace.__doc__ or ""
        assert "DatasetVersion" in doc
        assert "Training/Evaluation" in doc or "Training / Evaluation" in doc


class TestRetentionConflict:
    """§7.4 A-11：180 天与 90 天各挂一档介质，两个数字都活着。"""

    def test_both_thresholds_survive(self) -> None:
        assert COLD_TO_ARCHIVE_NO_ACCESS_DAYS == 90
        assert DEEP_ARCHIVE_NO_ACCESS_DAYS == 180
        assert TIER_MODEL_THRESHOLDS[LifecycleStage.ARCHIVE] == 180
        assert TIER_MODEL_THRESHOLDS[LifecycleStage.COLD] == 90

    def test_deep_archive_media_is_not_a_dead_constant(self) -> None:
        """180 天那一档存在的意义，就是让深度归档 0.05x 不是个没人调用的常量。"""
        from adas_lakehouse.lifecycle.tiers import RELATIVE_PRICE

        assert StorageMedia.OSS_DEEP_ARCHIVE in RELATIVE_PRICE
        assert (
            RELATIVE_PRICE[StorageMedia.OSS_DEEP_ARCHIVE] < RELATIVE_PRICE[StorageMedia.OSS_ARCHIVE]
        )

    def test_type_schedule_is_the_primary_rule(self) -> None:
        """冲突一取第三章：原始数据 30 天转低频，不是第二章的 90 天。"""
        raw = RETENTION_SCHEDULE[DataType.RAW]
        assert raw.standard_days == 30
        assert raw.ia_until_days == 90
        assert TIER_MODEL_THRESHOLDS[LifecycleStage.WARM] == 30

    def test_registered_in_deviations(self) -> None:
        """选了边必须登记——这份文档是本项目的诚信底线。"""
        doc = (_REPO_ROOT / "docs" / "source-deviations.md").read_text(encoding="utf-8")
        assert "### A-11" in doc
        assert "COLD_TO_ARCHIVE_NO_ACCESS_DAYS" in doc
        assert "DEEP_ARCHIVE_NO_ACCESS_DAYS" in doc


class TestDeviationsRegistration:
    """三条新登记都在文档里，且都在 A 节（口径冲突）而不是 B 节（本项目设计）。"""

    @pytest.mark.parametrize("heading", ["### A-10", "### A-11", "### A-12"])
    def test_heading_present(self, heading: str) -> None:
        doc = (_REPO_ROOT / "docs" / "source-deviations.md").read_text(encoding="utf-8")
        assert heading in doc
        assert doc.index(heading) < doc.index("## B. 本项目设计")


# ====================================================================== §7.2 遗留四项


class TestTagAliasArbitration:
    """§7.2①：别名冲突仲裁 + merged 合并态。

    守的是一个真实存在过的 bug——``add(replace=True)`` 先摘索引再判冲突，
    工单被拒时字典留下内伤：条目自称有某别名，``lookup()`` 却查不到。
    """

    def test_conflict_is_rejected(self) -> None:
        book = default_dictionary()
        victim = book.require("SCENE_HIGHWAY_TOLL_STATION")
        stolen = "环形交叉口"  # 归 SCENE_URBAN_ROUNDABOUT
        assert book.lookup(stolen).tag_id == "SCENE_URBAN_ROUNDABOUT"
        with pytest.raises(ValueError, match="别名冲突"):
            book.add(replace(victim, aliases=(stolen, *victim.aliases)), replace=True)

    def test_rejected_update_leaves_the_index_intact(self) -> None:
        """★ 原子性：拒绝一单，不许动任何一个 key。"""
        book = default_dictionary()
        victim = book.require("SCENE_HIGHWAY_TOLL_STATION")
        before = {form: book.lookup(form) for form in victim.normalized_forms}

        with pytest.raises(ValueError):
            book.add(replace(victim, aliases=("环形交叉口", *victim.aliases)), replace=True)

        for form, entry in before.items():
            after = book.lookup(form)
            assert after is not None, f"{form!r} 在被拒的工单之后从倒排索引里消失了"
            assert after.tag_id == entry.tag_id
        # 条目自身也不许被改
        assert book.require("SCENE_HIGHWAY_TOLL_STATION").aliases == victim.aliases

    def test_clean_alias_update_still_works(self) -> None:
        book = default_dictionary()
        victim = book.require("SCENE_HIGHWAY_TOLL_STATION")
        book.add(replace(victim, aliases=(*victim.aliases, "收费岛")), replace=True)
        assert book.lookup("收费岛").tag_id == "SCENE_HIGHWAY_TOLL_STATION"
        for form in victim.normalized_forms:
            assert book.lookup(form) is not None

    def test_merged_tombstone_yields_the_alias(self) -> None:
        """merged 让位：墓碑的旧写法已转挂目标标签，不该再报冲突。"""
        from adas_lakehouse.tags.dictionary import TagStatus

        book = default_dictionary()
        source, target = "SCENE_HIGHWAY_TOLL_STATION", "SCENE_URBAN_ROUNDABOUT"
        book.merge_into(source, target)
        tomb = book.require(source)
        assert tomb.status is TagStatus.MERGED
        assert tomb.merged_into_tag_id == target
        # 原名转挂到目标标签上，检索不会「查一个漏一个」
        assert book.lookup("收费站").tag_id == target


class TestCoverageDailyColumns:
    """§7.2②：覆盖度日指标——表里声明的每一列都要真的算得出来。"""

    def test_every_business_column_is_emitted(self) -> None:
        spec = next(t for t in registry.all_tables() if t.name == "dws_mining_tag_coverage_daily")
        emitted = set(compute_daily_coverage("2026-09-13").pop().to_row())
        declared = {c.name for c in spec.all_columns()}
        system = set(SYSTEM_COLUMNS)
        missing = declared - emitted - system
        assert not missing, f"这些列在表里声明了却永远落 NULL：{sorted(missing)}"
        assert not emitted - declared

    def test_gap_count_is_dictionary_minus_used(self) -> None:
        rows = compute_daily_coverage("2026-09-13", total_data_count=10)
        for row in rows:
            assert row.gap_tag_count == max(row.active_tag_count - row.distinct_tag_count, 0)

    def test_trend_needs_history_and_says_so(self) -> None:
        """历史不足就留 None，不拿 0.0 冒充「没变化」。"""
        series = []
        for idx, day in enumerate(f"2026-09-0{d}" for d in range(1, 6)):
            row = compute_daily_coverage(day, total_data_count=100)[0]
            row.tagged_data_count = 80 - idx * 5
            series.append(row)
        fill_coverage_trend(series)

        assert series[0].coverage_trend_7d is None
        assert series[-1].coverage_trend_7d == pytest.approx(-0.20, abs=1e-6)
        assert all(r.coverage_trend_7d < 0 for r in series[1:])

    def test_trend_window_must_be_at_least_two_days(self) -> None:
        with pytest.raises(ValueError, match="window_days"):
            fill_coverage_trend([], window_days=1)


class TestReleaseGate:
    """§7.2③：性能基准发布门禁——验收线要卡在换代发布这条路上。"""

    @staticmethod
    def _registry(**kwargs: object) -> VersionRegistry:
        reg = VersionRegistry(**kwargs)  # type: ignore[arg-type]
        reg.register(EmbeddingVersion("clip_v1", "CLIP-ViT-B/32"))
        reg.register(EmbeddingVersion("clip_v2", "CLIP-ViT-L/14", status=VectorStatus.DEPRECATED))
        return reg

    def test_gate_reuses_the_article_sla(self) -> None:
        """不新造判据：门禁用的就是原文那条 P95 ≤ 2s 验收线。"""
        assert evaluate_release_gate(2.0).sla_seconds == SEARCH_P95_SLA_SECONDS
        assert evaluate_release_gate(SEARCH_P95_SLA_SECONDS).passed

    def test_default_path_is_unchanged(self) -> None:
        """开关默认关闭：不传基准仍然放行，既有调用方一个都不被拦。"""
        reg = self._registry()
        stmts = reg.plan_switch("clip_v1", "clip_v2")
        assert len(stmts) == 2
        assert reg.get("clip_v2").status is VectorStatus.ACTIVE
        assert reg.last_gate is not None
        assert reg.last_gate.passed and reg.last_gate.evidence_missing

    def test_breaching_p95_blocks_the_switch(self) -> None:
        reg = self._registry()
        with pytest.raises(ReleaseGateBlocked) as excinfo:
            reg.plan_switch("clip_v1", "clip_v2", measured_p95_sec=3.7)
        assert excinfo.value.gate.measured_p95_sec == 3.7
        # ★ 被拒时台账必须原封不动，否则「拒绝」只是半截操作
        assert reg.get("clip_v1").status is VectorStatus.ACTIVE
        assert reg.get("clip_v2").status is VectorStatus.DEPRECATED

    def test_passing_p95_allows_the_switch(self) -> None:
        reg = self._registry()
        reg.plan_switch("clip_v1", "clip_v2", measured_p95_sec=1.4)
        assert reg.get("clip_v2").status is VectorStatus.ACTIVE
        assert reg.last_gate is not None and not reg.last_gate.evidence_missing

    def test_require_benchmark_rejects_missing_evidence(self) -> None:
        reg = self._registry(require_benchmark=True)
        with pytest.raises(ReleaseGateBlocked):
            reg.plan_switch("clip_v1", "clip_v2")
        assert reg.get("clip_v1").status is VectorStatus.ACTIVE

    def test_missing_evidence_is_never_reported_as_passing_the_bar(self) -> None:
        """放行 ≠ 压过了。这两件事在结论里必须分得开。"""
        gate = evaluate_release_gate(None)
        assert gate.passed and gate.evidence_missing
        assert "不等于压过了" in gate.reason_cn


class TestSystemFieldMount:
    """§7.2④：系统字段挂载规范——名字在、类型也要在。"""

    def test_spec_agrees_with_layer_property(self) -> None:
        for layer, (fields, _purpose) in SYSTEM_FIELD_SPEC.items():
            assert fields == layer.system_fields

    def test_all_88_tables_carry_their_layer_fields(self) -> None:
        for spec in registry.all_tables():
            names = {c.name for c in spec.all_columns()}
            for field_name in SYSTEM_FIELD_SPEC[spec.layer][0]:
                assert field_name in names, f"{spec.name} 缺 {field_name}"

    def test_no_business_column_shadows_a_system_field(self) -> None:
        """业务列同名覆盖是这条规范唯一的活口，全 88 张表现在一个都没有。"""
        for spec in registry.all_tables():
            business = {c.name for c in spec.columns}
            assert not business & set(SYSTEM_COLUMNS), spec.name

    def test_shadowing_with_a_wrong_type_is_now_caught(self) -> None:
        """★ 反例：同名业务列改了类型，从前名字检查照样通过，现在必须报违规。"""
        spec = next(t for t in registry.all_tables() if t.layer is Layer.DWD)
        tampered = replace(spec, columns=[*spec.columns, Column("update_time", "STRING", "冒牌货")])
        problems = tampered.validate()
        assert any("update_time" in p and "类型" in p for p in problems)

    def test_shadowing_with_wrong_nullability_is_caught(self) -> None:
        spec = next(t for t in registry.all_tables() if t.layer is Layer.DWD)
        tampered = replace(
            spec,
            columns=[
                *spec.columns,
                Column("_ingest_time", "TIMESTAMP(3)", "可空冒牌货", nullable=True),
            ],
        )
        assert any("可空性" in p for p in tampered.validate())

    def test_registry_is_still_clean(self) -> None:
        """收紧校验之后，88 张表仍然零违规。"""
        assert registry.validate_all() == {}
