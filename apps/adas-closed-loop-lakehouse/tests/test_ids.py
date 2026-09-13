"""三级 ID 体系：data_id / artifact_id / run_id。

覆盖 ids/__init__.py docstring 里的四条核心生成规则：

  1. 唯一性   —— data_id 的 sequence 取自 UUID，两次生成必不同
  2. 时间戳   —— yyyyMMddHHmmss，字典序即生产顺序，反解可还原
  3. 重刷     —— 算法升级重刷时 data_id 不变，只长出新 artifact_id
  4. 幂等     —— 内容相同 → content_hash 相同 → artifact_id 相同
"""

from __future__ import annotations

from datetime import datetime

import pytest

from adas_lakehouse.ids import (
    TS_FORMAT,
    ArtifactStatus,
    content_hash,
    derive_artifact_id,
    new_data_id,
    new_run_id,
    parse_artifact_id,
    parse_data_id,
    parse_run_id,
)

MOMENT = datetime(2024, 1, 15, 14, 30, 22)


# --------------------------------------------------------------------------- 一级 data_id


def test_new_data_id_shape():
    did = new_data_id("BP", MOMENT)
    assert str(did).startswith("COLLECT_BP_20240115143022_")
    assert did.vehicle_code == "BP"
    assert did.collected_at == MOMENT
    assert len(did.sequence) >= 4


def test_new_data_id_uppercases_vehicle_code():
    """车辆编码统一大写——否则同一辆车会有两套 ID 前缀。"""
    assert new_data_id("bp", MOMENT).vehicle_code == "BP"
    assert str(new_data_id("bp", MOMENT)).startswith("COLLECT_BP_")


def test_data_id_round_trip():
    """规则二：反解能完整还原车辆 / 时间 / 序列，不需要查任何表。"""
    did = new_data_id("BP", MOMENT)
    back = parse_data_id(str(did))
    assert back.raw == did.raw
    assert back.vehicle_code == "BP"
    assert back.collected_at == MOMENT
    assert back.sequence == did.sequence


def test_data_id_sequence_comes_from_uuid():
    """规则一：sequence 段取自 UUID，同一秒的两条 ID 靠它区分。

    注意熵预算：sequence 只取 uuid4 的前 4 个十六进制字符 = 16 bit = 65536 种取值。
    同一辆车、同一秒内生成上百条时会出现生日碰撞（200 条约 26% 概率撞一次）。
    真实场景一辆车约 1 分钟一个 clip，撞不上；但如果哪天要在同一秒批量造 ID，
    得先把这一段加长。所以这里断言的是「机制正确 + 大体不重复」，
    而不是「任意条数都绝不重复」——后者本来就不成立，写成断言只会变成随机红。
    """
    ids = [new_data_id("BP", MOMENT) for _ in range(200)]

    for did in ids:
        assert len(did.sequence) == 4
        assert all(c in "0123456789abcdef" for c in did.sequence)

    # 16 bit 熵下 200 抽样的期望碰撞数约 0.3，放到 10 条仍有巨大余量
    assert len({str(d) for d in ids}) >= len(ids) - 10


def test_data_id_timestamp_sorts_by_production_order():
    """规则二：按字典序排序即还原生产顺序。"""
    early = new_data_id("BP", datetime(2024, 1, 15, 14, 30, 22))
    late = new_data_id("BP", datetime(2024, 1, 15, 14, 31, 0))
    assert str(early) < str(late)
    assert MOMENT.strftime(TS_FORMAT) == "20240115143022"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "COLLECT_BP_20240115143022",  # 缺 sequence
        "COLLECT_BP_2024011514302_a1b2",  # 时间戳 13 位
        "COLLECT_bp_20240115143022_a1b2",  # 车辆码小写
        "COLLECT_BP_20240115143022_ZZZZ",  # sequence 非十六进制
        "collect_BP_20240115143022_a1b2",  # 前缀小写
        "COLLECT_BP_20240115143022_a1b2_extra",  # 多出一段
    ],
)
def test_parse_data_id_rejects_malformed(bad):
    with pytest.raises(ValueError, match="不是合法的 data_id"):
        parse_data_id(bad)


# --------------------------------------------------------------------------- 内容哈希


def test_content_hash_is_deterministic_and_encoding_stable():
    assert content_hash(b"payload") == content_hash("payload")
    assert content_hash("payload") == content_hash("payload")
    assert content_hash("payload") != content_hash("payload ")
    assert len(content_hash("x")) == 8
    assert len(content_hash("x", length=16)) == 16


# --------------------------------------------------------------------------- 二级 artifact_id


def test_derive_artifact_id_embeds_anchor():
    did = new_data_id("BP", MOMENT)
    aid = derive_artifact_id(did, "slam", "v4", b"point-cloud-bytes")
    assert aid.data_id == str(did)
    assert aid.stage == "slam"
    assert aid.algo_version == "v4"
    assert str(aid) == f"{did}_slam_v4_{content_hash(b'point-cloud-bytes')}"


def test_derive_artifact_id_is_idempotent_on_identical_content():
    """规则一：内容相同 → ID 相同 → 重试天然幂等。"""
    did = new_data_id("BP", MOMENT)
    first = derive_artifact_id(did, "slam", "v4", b"same-bytes")
    again = derive_artifact_id(did, "slam", "v4", b"same-bytes")
    assert str(first) == str(again)

    other = derive_artifact_id(did, "slam", "v4", b"different-bytes")
    assert str(other) != str(first)


def test_refresh_keeps_data_id_and_forks_artifact_id():
    """规则三：重刷产线时 data_id 不变，新算法版本长出新 artifact_id，旧产物保留。"""
    did = new_data_id("BP", MOMENT)
    v3 = derive_artifact_id(did, "slam", "v3", b"pointcloud")
    v4 = derive_artifact_id(did, "slam", "v4", b"pointcloud")

    assert v3.data_id == v4.data_id == str(did)  # 锚点终身不变
    assert str(v3) != str(v4)  # 版本分支
    assert v3.content_hash == v4.content_hash  # 同一份输入
    # 旧产物不删，只标记 superseded
    assert ArtifactStatus.SUPERSEDED.value == "superseded"
    assert {s.value for s in ArtifactStatus} == {"active", "superseded", "invalid"}


def test_derive_artifact_id_lowercases_stage():
    did = new_data_id("BP", MOMENT)
    assert derive_artifact_id(did, "SLAM", "v4", b"x").stage == "slam"


def test_derive_artifact_id_rejects_illegal_anchor():
    """早失败：产物必须挂在合法的 data_id 上。"""
    with pytest.raises(ValueError, match="不是合法的 data_id"):
        derive_artifact_id("NOT_AN_ANCHOR", "slam", "v4", b"x")


@pytest.mark.parametrize("version", ["3", "V4", "latest", "", "4.1"])
def test_derive_artifact_id_rejects_bad_algo_version(version):
    did = new_data_id("BP", MOMENT)
    with pytest.raises(ValueError, match="算法版本"):
        derive_artifact_id(did, "slam", version, b"x")


def test_artifact_id_round_trip():
    did = new_data_id("BP", MOMENT)
    aid = derive_artifact_id(did, "ann", "v4.1", b"labels")
    back = parse_artifact_id(str(aid))
    assert back.data_id == str(did)
    assert back.stage == "ann"
    assert back.algo_version == "v4.1"
    assert back.content_hash == aid.content_hash


@pytest.mark.parametrize(
    "bad",
    [
        "COLLECT_BP_20240115143022_a1b2",  # 只有 data_id
        "COLLECT_BP_20240115143022_a1b2_slam_v4",  # 缺 content_hash
        "COLLECT_BP_20240115143022_a1b2_slam_4_deadbeef",  # 版本没有 v 前缀
        "COLLECT_BP_20240115143022_a1b2_SLAM_v4_deadbeef",  # stage 大写
        "NOT_AN_ANCHOR_slam_v4_deadbeef",
    ],
)
def test_parse_artifact_id_rejects_malformed(bad):
    with pytest.raises(ValueError, match="不是合法的 artifact_id"):
        parse_artifact_id(bad)


# --------------------------------------------------------------------------- 三级 run_id


def test_run_id_round_trip():
    rid = new_run_id("MINING", MOMENT)
    assert str(rid).startswith("run_mining_20240115143022_")
    back = parse_run_id(str(rid))
    assert back.stage == "mining"
    assert back.started_at == MOMENT
    assert back.sequence == rid.sequence


def test_run_id_is_unique_per_execution():
    """三级：一次执行一条。run_id 的 sequence 取 8 位十六进制（32 bit），比 data_id 宽。"""
    runs = [new_run_id("mining", MOMENT) for _ in range(200)]
    for rid in runs:
        assert len(rid.sequence) == 8
        assert all(c in "0123456789abcdef" for c in rid.sequence)

    # 32 bit 熵下 200 抽样的期望碰撞数约 2e-6，这里可以要求全不重复
    assert len({str(r) for r in runs}) == 200


@pytest.mark.parametrize(
    "bad",
    ["run_mining_20240115143022", "mining_20240115143022_abcd1234", "run__20240115143022_abcd1234"],
)
def test_parse_run_id_rejects_malformed(bad):
    with pytest.raises(ValueError, match="不是合法的 run_id"):
        parse_run_id(bad)


# --------------------------------------------------------------------------- 跨级不串台


def test_levels_do_not_cross_parse():
    """三级 ID 格式互斥——拿 run_id 去当 data_id 解必须报错，而不是解出垃圾。"""
    did = new_data_id("BP", MOMENT)
    aid = derive_artifact_id(did, "slam", "v4", b"x")
    rid = new_run_id("slam", MOMENT)

    with pytest.raises(ValueError):
        parse_data_id(str(rid))
    with pytest.raises(ValueError):
        parse_run_id(str(did))
    with pytest.raises(ValueError):
        parse_artifact_id(str(rid))
    # data_id 是 artifact_id 的前缀，但前缀本身不是合法 artifact_id
    with pytest.raises(ValueError):
        parse_artifact_id(str(did))
    assert str(aid).startswith(str(did))
