"""门禁的 Flink 接入点：``quality_gate_check`` UDF。

``flink/sql/quality_gate_pipeline.sql`` 是原文五步闭环里 ①拦截 + ②隔离的生产入口，
它这样注册门禁::

    CREATE TEMPORARY FUNCTION IF NOT EXISTS quality_gate_check
    AS 'adas_lakehouse.quality.udf.QualityGateUdf'
    LANGUAGE PYTHON;

    CROSS JOIN LATERAL TABLE(quality_gate_check(<表>, <通道>, <原始报文 JSON>)) AS gate

本模块就是那个名字指向的实现——在此之前它不存在，整条门禁作业submit 即失败：
规则、隔离、告警全都写好了，SQL 也写好了，中间少了这一段胶水。

输出契约（与 SQL 里 ``gate.*`` 的取用逐字对齐，改这里必须同步改那边）::

    ROW<disposition STRING, quality_flag STRING, rule_ids STRING,
        issue_json STRING, duration_ms DOUBLE>

三条实现约束：

1. **不写隔离表**。隔离行由 SQL 的 ``INSERT INTO ods_quality_issue`` 负责
   （见 pipeline 第 3.2 段），UDF 这边 ``isolate=False``，只把隔离记录序列化成
   ``issue_json`` 交出去。两边都写会写重。
2. **不在导入期碰 pyflink**。没装 pyflink 也要能 import 本模块——
   与 :mod:`adas_lakehouse.sampling.engine` 的 ``register_flink_udfs`` 同一个纪律。
3. **规则中心只建一次**。原文第六章要求 ``quality_check_duration`` 不超 1000ms，
   每行重建一遍规则中心必然超；建好挂在实例上，按窗口重置去重台账以免内存无界。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, Final

from .gate import QualityGate
from .isolation import IssueRecord
from .rules import RuleHit
from .severity import Channel

__all__ = [
    "RESULT_FIELDS",
    "GateRow",
    "gate_check_row",
    "QualityGateUdf",
    "register_flink_udfs",
]

_log = logging.getLogger("adas.quality.udf")

#: UDF 返回的 ROW 字段（名字, Flink 类型名）。SQL 侧按这个顺序取 gate.xxx。
RESULT_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("disposition", "STRING"),
    ("quality_flag", "STRING"),
    ("rule_ids", "STRING"),
    ("issue_json", "STRING"),
    ("duration_ms", "DOUBLE"),
)

#: 一行 UDF 输出。用 tuple 而不是 dataclass——PyFlink 的 UDTF 就吃 tuple。
GateRow = tuple[str, str, str, str, float]

#: ⚠️ 原文未明确，本项目设计：流作业长跑时唯一性检查的去重台账会一直涨，
#: 每这么多条记录重置一次统计窗口（同时清空台账），把内存钉住。
#: 重复率监控的统计口径因此是「窗口内」，与批作业一次跑批的口径一致。
WINDOW_RECORDS_DEFAULT: Final[int] = 100_000


def _issue_json(issue: IssueRecord) -> str:
    """把隔离记录序列化成 SQL 那边 JSON_VALUE / JSON_QUERY 取得到的结构。

    SQL 里取的路径是 ``$.issue_id / $.severity / $.issue_level / $.dimension /
    $.quality_layer / $.message / $.detail / $.hits / $.raw_payload /
    $.payload_hash / $.repair_action / $.owner / $.response_due_at /
    $.closure_due_at / $.gate_version / $.history``——一个都不能少。
    """
    row = issue.to_row()
    # to_row() 把这两列存成字符串（Paimon 列就是 STRING）；SQL 用的是 JSON_QUERY，
    # 要的是真数组，这里还原回去
    try:
        row["hits"] = json.loads(issue.hits_json)
    except ValueError:  # pragma: no cover - hits_json 由本包生成，不该坏
        row["hits"] = []
    row["history"] = list(issue.history)
    return json.dumps(row, ensure_ascii=False, default=str)


def gate_check_row(
    gate: QualityGate,
    table: str,
    channel: str,
    payload_json: str,
    *,
    now: datetime | None = None,
) -> GateRow:
    """跑一次门禁，返回 SQL 要的那五个字段。

    :param payload_json: 原始报文（JSON 文本）。解析不了**不是**跳过，而是把
        ``{"_raw_payload": 原文}`` 喂给门禁——让 QG-COM-006（Schema 不可解析，P0）
        和主键规则自己去判，判定权始终在规则中心，不在这段胶水里。
    """
    record: Mapping[str, Any]
    try:
        parsed = json.loads(payload_json) if payload_json else {}
    except (TypeError, ValueError):
        parsed = None
    record = parsed if isinstance(parsed, Mapping) else {"_raw_payload": payload_json}

    decision = gate.check(
        table,
        record,
        channel=_channel_of(channel),
        isolate=False,  # 隔离行由 SQL 写，见模块 docstring 约束 1
    )
    rule_ids = ",".join(h.rule_id for h in decision.hits)
    issue_json = ""
    if decision.rejected:
        issue = _build_issue(gate, decision.table, decision.channel, record, decision, now)
        issue_json = _issue_json(issue)
    return (
        decision.disposition.value,
        decision.quality_flag,
        rule_ids,
        issue_json,
        decision.duration_ms,
    )


def _channel_of(channel: str) -> Channel:
    """通道名转枚举。SQL 传的是 'kafka' / 'mysql_cdc' / 'oss_file' 这类字面量。"""
    try:
        return Channel(str(channel))
    except ValueError:
        _log.warning("未知通道 %r，按三通道通用规则处理", channel)
        return Channel.COMMON


def _build_issue(
    gate: QualityGate,
    table: str,
    channel: Channel,
    record: Mapping[str, Any],
    decision: Any,
    now: datetime | None,
) -> IssueRecord:
    """构造隔离记录（不落库），并补上 ①② 两步轨迹——SQL 落库时一起写进去。"""
    at = now or decision.decided_at
    hits: list[RuleHit] = list(decision.hits)
    issue = IssueRecord.from_hits(
        table=table,
        channel=channel,
        record=record,
        hits=hits,
        detected_at=at,
        record_key=decision.record_key,
        source_system=gate.source_system,
        owner=gate.owner_resolver(table) if gate.owner_resolver else "",
        on_duty=gate.on_duty,
    )
    issue.note(f"① 门禁拦截：命中 {', '.join(issue.rule_ids)}，阻断写入 ODS", at)
    issue.note("② 异常隔离：原始报文已随记录保存，可重放", at)
    return issue


def _table_function_base() -> type:
    """PyFlink 可用时返回 ``TableFunction``，否则返回 ``object``。

    在函数体里 import——模块顶层碰 pyflink 会让没装它的环境连 import 都过不去
    （tests/test_package_imports.py 就盯着这条）。
    """
    try:
        from pyflink.table.udf import TableFunction  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - 取决于部署环境
        return object
    return TableFunction  # type: ignore[no-any-return]


#: 装了 pyflink 就是货真价实的 TableFunction 子类（``LANGUAGE PYTHON`` 按类名注册
#: 需要这个基类），没装就是个普通类——裸环境下照样能 import、能被单测直接调用。
_UDF_BASE = _table_function_base()


class QualityGateUdf(_UDF_BASE):  # type: ignore[misc,valid-type]
    """``quality_gate_check`` 的实现体（PyFlink TableFunction 语义）。

    基类见 :data:`_UDF_BASE`。用法（Python 侧，等价于 SQL 里的 CROSS JOIN LATERAL TABLE）::

        udf = QualityGateUdf()
        (disposition, flag, rule_ids, issue_json, ms), = udf.eval(
            "ods_data_file_meta", "oss_file", json.dumps(row)
        )
    """

    def __init__(
        self,
        *,
        source_system: str = "quality-gate",
        window_records: int = WINDOW_RECORDS_DEFAULT,
    ) -> None:
        self.source_system = source_system
        self.window_records = window_records
        self._gate: QualityGate | None = None
        self._seen = 0

    # PyFlink 在算子初始化时调用；裸跑时由 eval 兜底懒建
    def open(self, function_context: Any = None) -> None:  # noqa: ARG002 - Flink 回调签名
        self._gate = QualityGate(source_system=self.source_system)

    @property
    def gate(self) -> QualityGate:
        if self._gate is None:
            self.open()
        assert self._gate is not None
        return self._gate

    def eval(self, table: str, channel: str, payload_json: str) -> Iterator[GateRow]:
        gate = self.gate
        self._seen += 1
        if self.window_records and self._seen % self.window_records == 0:
            # 去重台账与窗口指标一起重置，长跑作业的内存才钉得住
            gate.reset_window()
        yield gate_check_row(gate, table, channel, payload_json)


def register_flink_udfs(table_env: Any, *, name: str = "quality_gate_check") -> None:
    """把门禁注册成 Flink UDTF，供 ``flink/sql/quality_gate_pipeline.sql`` 调用。

    SQL 文件用的是 ``CREATE TEMPORARY FUNCTION ... AS '<类名>' LANGUAGE PYTHON``；
    需要在 Python 侧显式注册（比如 PyFlink 作业里自己建 TableEnvironment）时用这个函数，
    两条路注册出来的是同一个实现。

    :raises RuntimeError: 未安装 pyflink（延迟 import，不影响本模块被 import）。
    """
    try:
        from pyflink.table import DataTypes  # type: ignore[import-not-found]
        from pyflink.table.udf import udtf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 取决于部署环境
        raise RuntimeError("注册质量门禁 UDF 需要安装 pyflink") from exc

    types = {
        "STRING": DataTypes.STRING,
        "DOUBLE": DataTypes.DOUBLE,
    }
    result_type = DataTypes.ROW(
        [DataTypes.FIELD(field, types[kind]()) for field, kind in RESULT_FIELDS]
    )
    table_env.create_temporary_function(name, udtf(QualityGateUdf(), result_types=result_type))
    _log.info("已注册 Flink UDTF: %s", name)
