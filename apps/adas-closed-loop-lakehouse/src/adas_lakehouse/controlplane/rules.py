"""规则配置与规则下发：控制面「规则配置」那一类运行态的具体实现。

原文依据：
  · 第一章「规则挖掘引擎：基于结构化元数据与已有标签，批量产出高价值场景标签——
    「雨天 + 夜间 + 无灯路口」这类组合条件，用 SQL 就能圈出来」；
  · 第四章「规则配置经 Flink CDC 同步入湖（ods_mining_rule_config）」。

两件事合在一起决定了规则的形态：
  1. 规则本体存控制面 MySQL（可随时重建），**不是**湖仓主数据；
  2. 但它必须回流数据面——因为「平台的每一步操作都进血缘」，规则是标签的成因，
     标签在湖仓，成因就不能只活在平台本地。回流走 Flink CDC，SQL 见
     ``flink/sql/plane_control_cdc.sql``。

规则下发（dispatch）在本项目里的定义是：把某个规则的某个版本冻结成一份
:class:`~.contracts.TaskEnvelope` 的参数快照，交给调度器。规则改了不影响已下发的
任务——任务信封里记着 ``rule_version``，重跑可复现。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from . import constants as K

__all__ = [
    "RuleEngine",
    "RuleConfig",
    "RuleRegistry",
    "RuleValidationError",
    "EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION",
]


class RuleEngine(str, Enum):
    """规则跑在哪个引擎上。

    原文第三章核心引擎层：「规则挖掘（Spark 批 + Flink 流）」——两种都要支持：
    存量数据批量圈选走 Spark 批，增量回传数据实时命中走 Flink 流。
    """

    SPARK_BATCH = "spark_batch"
    FLINK_STREAM = "flink_stream"


class RuleValidationError(ValueError):
    """规则配置非法。"""


#: 场景表达式里禁止出现的 SQL 关键字。
#: ⚠️ 原文未明确，本项目设计：原文只说「用 SQL 就能圈出来」，没讲注入防护。
#: 规则表达式来自控制台用户输入，最终会被拼进数据面的 WHERE 子句，因此必须只允许谓词。
_FORBIDDEN_SQL = re.compile(
    r"(?i)\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|merge|call|"
    r"load\s+data|into\s+outfile)\b|;|--|/\*"
)

#: 场景表达式长度上限。⚠️ 原文未明确，本项目设计：与 contracts 的标量上限保持一致。
_MAX_EXPRESSION_CHARS = 4096


@dataclass(frozen=True, slots=True)
class RuleConfig:
    """一条规则配置。

    :param rule_id: 规则 ID（业务主键，不用自增 ID）
    :param rule_name: 规则名，如「雨天 + 夜间 + 无灯路口」
    :param scene_expression: 场景表达式——一段纯 WHERE 谓词，作用在数据面表上
    :param tag_code: 命中后打的标签编码，进统一标签体系
    :param engine: 批还是流
    :param rule_version: 版本号，每次改动 +1；任务信封冻结版本号以便复现
    :param input_tables: 规则作用的数据面表（默认 clip 明细 + 抽帧明细）
    """

    rule_id: str
    rule_name: str
    scene_expression: str
    tag_code: str
    engine: RuleEngine = RuleEngine.SPARK_BATCH
    rule_version: int = 1
    enabled: bool = True
    priority: int = K.PRIORITY_DEFAULT
    owner: str = "system"
    input_tables: tuple[str, ...] = (K.TABLE_CLIP_DETAIL, K.TABLE_IMAGE_FRAME_DETAIL)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """校验规则。非法直接抛 :class:`RuleValidationError`。"""
        if not self.rule_id:
            raise RuleValidationError("rule_id 不能为空（业务主键优先，不用自增 ID）")
        if not self.scene_expression.strip():
            raise RuleValidationError(f"规则 {self.rule_id} 的场景表达式为空")
        if len(self.scene_expression) > _MAX_EXPRESSION_CHARS:
            raise RuleValidationError(
                f"规则 {self.rule_id} 的场景表达式超过 {_MAX_EXPRESSION_CHARS} 字符"
            )
        hit = _FORBIDDEN_SQL.search(self.scene_expression)
        if hit:
            raise RuleValidationError(
                f"规则 {self.rule_id} 的场景表达式含禁用片段 {hit.group(0)!r}；"
                f"表达式只能是 WHERE 谓词，DDL/DML 一律不允许"
            )
        if self.rule_version < 1:
            raise RuleValidationError(f"规则 {self.rule_id} 的版本号必须 ≥ 1")
        if not K.PRIORITY_HIGHEST <= self.priority <= K.PRIORITY_LOWEST:
            raise RuleValidationError(
                f"规则 {self.rule_id} 的优先级必须落在 [{K.PRIORITY_HIGHEST}, {K.PRIORITY_LOWEST}]"
            )
        for table in self.input_tables:
            if not table.startswith(("ods_", "dwd_", "dws_", "ads_")):
                raise RuleValidationError(
                    f"规则 {self.rule_id} 的输入表 {table!r} 不是湖仓表；"
                    f"规则只能作用在数据面（平台不自建数据通道）"
                )

    def bump(self, **changes: Any) -> RuleConfig:
        """改规则 = 生成下一个版本。旧版本保留，已下发的任务不受影响。"""
        changes.setdefault("rule_version", self.rule_version + 1)
        changes.setdefault("updated_at", datetime.now())
        return replace(self, **changes)

    def where_clause(self) -> str:
        """渲染成可安全拼接的 WHERE 谓词（已过校验，外面包一层括号防优先级串味）。"""
        return f"({self.scene_expression.strip()})"

    def dispatch_params(self) -> dict[str, Any]:
        """冻结成任务信封的参数快照。

        注意这里面全是「怎么算」——表达式、标签码、引擎、版本，
        没有一条命中的数据。数据面拿着它自己去湖仓跑。
        """
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "rule_version": self.rule_version,
            "scene_expression": self.scene_expression,
            "tag_code": self.tag_code,
            "engine": self.engine.value,
            "input_tables": list(self.input_tables),
        }

    def as_cdc_row(self) -> dict[str, Any]:
        """展平成控制面 MySQL 的一行——Flink CDC 就是从这张表同步到
        ``ods_mining_rule_config``（原文第四章）。"""
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_name": self.rule_name,
            "scene_expression": self.scene_expression,
            "tag_code": self.tag_code,
            "engine": self.engine.value,
            "enabled": 1 if self.enabled else 0,
            "priority": self.priority,
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


#: 原文第一章的规则示例，逐字落地：「雨天 + 夜间 + 无灯路口」这类组合条件，用 SQL 就能圈出来。
#:
#: ⚠️ 字段名为本项目推断：原文只给了这三个中文条件，没给列名。
#: 这里用采集域 ``dwd_collect_clip_detail`` 已有的 ``weather`` / ``light_condition``
#: 字段，加上抽帧明细里推断的 ``road_element``。
EXAMPLE_RULE_RAIN_NIGHT_UNLIT_INTERSECTION: RuleConfig = RuleConfig(
    rule_id="RULE_RAIN_NIGHT_UNLIT_INTERSECTION",
    rule_name="雨天 + 夜间 + 无灯路口",
    scene_expression=(
        "weather = 'rain' AND light_condition = 'night' "
        "AND road_element = 'intersection' AND street_light = 'none'"
    ),
    tag_code="SCENE_RAIN_NIGHT_UNLIT_INTERSECTION",
    engine=RuleEngine.SPARK_BATCH,
    owner="mining-platform",
)


class RuleRegistry:
    """规则注册表：控制面对规则配置的增删改查 + 版本管理。

    只管配置，不碰数据。规则删除也只是 ``enabled=False``——因为已经据此打过的标签
    在湖仓，成因不能凭空消失（血缘要求）。
    """

    def __init__(self, rules: Iterable[RuleConfig] = ()) -> None:
        #: (rule_id, rule_version) -> RuleConfig，全版本保留
        self._versions: dict[tuple[str, int], RuleConfig] = {}
        #: rule_id -> 当前生效版本号
        self._current: dict[str, int] = {}
        for rule in rules:
            self.upsert(rule)

    def upsert(self, rule: RuleConfig) -> RuleConfig:
        """登记一条规则（含具体版本）。同 ID 同版本重复登记会被拒绝——版本是不可变的。"""
        key = (rule.rule_id, rule.rule_version)
        existing = self._versions.get(key)
        if existing is not None and existing != rule:
            raise RuleValidationError(
                f"规则 {rule.rule_id} 版本 {rule.rule_version} 已存在且内容不同；"
                f"改规则请用 RuleConfig.bump() 生成新版本"
            )
        self._versions[key] = rule
        current = self._current.get(rule.rule_id, 0)
        if rule.rule_version >= current:
            self._current[rule.rule_id] = rule.rule_version
        return rule

    def get(self, rule_id: str, version: int | None = None) -> RuleConfig:
        """取规则。不给版本号就取当前生效版本。"""
        ver = version if version is not None else self._current.get(rule_id)
        if ver is None:
            raise KeyError(f"未注册的规则: {rule_id!r}")
        try:
            return self._versions[(rule_id, ver)]
        except KeyError as exc:
            raise KeyError(f"规则 {rule_id!r} 没有版本 {ver}") from exc

    def disable(self, rule_id: str, *, owner: str = "system") -> RuleConfig:
        """停用规则 = 发一个 ``enabled=False`` 的新版本，不删历史。"""
        new = self.get(rule_id).bump(enabled=False, owner=owner)
        return self.upsert(new)

    def enabled_rules(self, engine: RuleEngine | None = None) -> list[RuleConfig]:
        """当前生效且启用的规则，按优先级排序，供调度器批量下发。"""
        out = [self.get(rid) for rid in self._current]
        out = [r for r in out if r.enabled and (engine is None or r.engine is engine)]
        out.sort(key=lambda r: (r.priority, r.rule_id))
        return out

    def versions_of(self, rule_id: str) -> list[RuleConfig]:
        """某规则的全部历史版本，按版本号升序——重刷/复现要用。"""
        rows = [r for (rid, _), r in self._versions.items() if rid == rule_id]
        rows.sort(key=lambda r: r.rule_version)
        return rows

    def cdc_rows(self) -> list[dict[str, Any]]:
        """全量 CDC 行，供 ``ods_mining_rule_config`` 首次全量初始化。"""
        return [r.as_cdc_row() for _, r in sorted(self._versions.items())]

    def __len__(self) -> int:
        return len(self._current)

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._current
