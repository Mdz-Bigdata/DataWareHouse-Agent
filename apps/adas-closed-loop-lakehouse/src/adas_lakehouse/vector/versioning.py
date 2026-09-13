"""embedding_version 版本管理：新旧向量并存、灰度切换、一键回滚。

来源：原文第二章设计决策三——「embedding_version 入主键：模型换代时新旧向量并存，
用 vector_status 区分 active / deprecated，检索默认只查 active 版本——灰度切换与一键回滚
都不需要重写数据」。

关键点是「不重写数据」：
  · 换代不是覆盖——新版本以新的 embedding_version 作为主键的一部分整行写入，旧行原封不动；
  · 切换只改 vector_status 一列（Paimon partial-update），向量本体不动；
  · 回滚就是把 status 改回去，秒级完成，不需要重算任何向量。

这与 ids 模块「重刷不覆盖，旧产物标 superseded」是同一套哲学：
ids.ArtifactStatus 管产物血缘，本模块的 schema.VectorStatus 管向量在役状态，两者互不替代。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from .params import DEFAULT_VECTOR_DIM, SEARCH_P95_SLA_SECONDS, SEARCH_SLA_SCALE_CN
from .schema import VECTOR_TABLE_NAME, VectorStatus

__all__ = [
    "EmbeddingVersion",
    "VersionRegistry",
    "ReleaseGate",
    "ReleaseGateBlocked",
    "evaluate_release_gate",
    "render_activate_sql",
    "render_deprecate_sql",
    "render_rollback_sql",
    "render_active_filter",
    "validate_embedding_version",
    "EMBEDDING_VERSION_PATTERN",
    "ACTIVE_FILTER_CLAUSE",
]

_log = logging.getLogger(__name__)

#: 检索默认只查 active 版本——这句 WHERE 必须出现在每一条向量检索 SQL 里（原文第二章）。
ACTIVE_FILTER_CLAUSE: str = f"vector_status = '{VectorStatus.ACTIVE.value}'"

#: embedding_version 允许的字面量形态：字母 / 数字 / 下划线 / 点 / 连字符，1~64 位。
#: 形如 ``clip_v1`` / ``clip_v2.1`` / ``ViT-L-14_v3``。
EMBEDDING_VERSION_PATTERN: str = r"[A-Za-z0-9_.\-]{1,64}"
_VERSION_RE = re.compile(rf"\A{EMBEDDING_VERSION_PATTERN}\Z")


def validate_embedding_version(version: str) -> str:
    """校验 embedding_version 字面量，非法就炸。

    为什么必须校验：``vector_status`` 是枚举、``dt`` 有 yyyy-MM-dd 白名单，而
    embedding_version 是唯一一个会被**原样拼进 SQL 文本**的取值——检索请求里的
    ``embedding_version``（灰度对比用）直接来自上层应用。ANN 函数不接受参数化数组，
    这条 SQL 本来就有内联成分，版本号再不校验就等于开了一扇注入的门。

    :raises ValueError: 版本号为空或含白名单外的字符
    """
    if not version or not _VERSION_RE.match(version):
        raise ValueError(
            f"非法 embedding_version: {version!r}；只允许 {EMBEDDING_VERSION_PATTERN}"
            "（它会被拼进 SQL 文本，不能放任意字符串）"
        )
    return version


@dataclass(frozen=True, slots=True)
class EmbeddingVersion:
    """一个 Embedding 模型版本。

    :param version: embedding_version 取值，入表主键，形如 ``clip_v1`` / ``clip_v2``
    :param model_name: CLIP 模型标识（图文双塔同一个模型，保证向量同空间）
    :param dim: 向量维度，必须与 HNSW 索引 PROPERTIES 里的 dim 一致
    :param status: 在役状态 active / deprecated
    :param released_at: 版本发布时间
    :param rollout_ratio: 灰度比例 0.0~1.0，仅用于检索侧分流记录，不影响落表
    :param notes: 备注（换代原因、评测结论等）
    """

    version: str
    model_name: str
    dim: int = DEFAULT_VECTOR_DIM
    status: VectorStatus = VectorStatus.ACTIVE
    released_at: datetime | None = None
    rollout_ratio: float = 1.0
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("embedding_version 不能为空——它是主键的一部分")
        validate_embedding_version(self.version)
        if self.dim <= 0:
            raise ValueError(f"向量维度必须为正整数，收到 {self.dim}")
        if not 0.0 <= self.rollout_ratio <= 1.0:
            raise ValueError(f"灰度比例必须落在 [0, 1]，收到 {self.rollout_ratio}")

    @property
    def is_active(self) -> bool:
        return self.status is VectorStatus.ACTIVE

    def with_status(self, status: VectorStatus) -> EmbeddingVersion:
        """返回换了状态的新版本对象（frozen dataclass，不原地改）。"""
        return EmbeddingVersion(
            self.version,
            self.model_name,
            self.dim,
            status,
            self.released_at,
            self.rollout_ratio,
            self.notes,
        )


def _table(table: str | None = None) -> str:
    return table or VECTOR_TABLE_NAME


def render_activate_sql(version: str, *, table: str | None = None) -> str:
    """把某个 embedding_version 的向量置为 active（不重写向量本体）。

    ⚠️ 原文未明确，本项目设计：原文只说「灰度切换与一键回滚都不需要重写数据」，
    没给具体写法。这里用 Flink SQL 的 UPDATE 表达——Paimon 主键表支持部分列更新，
    只改 vector_status 一列，image_embedding / text_embedding 不参与写入。
    """
    validate_embedding_version(version)
    return (
        f"-- 激活 embedding_version={version}：只改 vector_status 一列，向量本体不动\n"
        f"UPDATE `{_table(table)}` SET `vector_status` = '{VectorStatus.ACTIVE.value}'\n"
        f"WHERE `embedding_version` = '{version}';\n"
    )


def render_deprecate_sql(version: str, *, table: str | None = None) -> str:
    """把某个 embedding_version 的向量置为 deprecated（旧版本退役，数据保留）。"""
    validate_embedding_version(version)
    return (
        f"-- 退役 embedding_version={version}：数据保留，检索不再命中\n"
        f"UPDATE `{_table(table)}` SET `vector_status` = '{VectorStatus.DEPRECATED.value}'\n"
        f"WHERE `embedding_version` = '{version}';\n"
    )


def render_rollback_sql(*, from_version: str, to_version: str, table: str | None = None) -> str:
    """一键回滚：新版本退役 + 旧版本重新激活，两条语句成对下发。

    因为新旧向量并存（embedding_version 入主键），回滚不需要重算、不需要重刷索引，
    只是把两个版本的 vector_status 对调。
    """
    return (
        f"-- 一键回滚 {from_version} -> {to_version}（新旧向量并存，无需重算、无需重建索引）\n"
        + render_deprecate_sql(from_version, table=table)
        + render_activate_sql(to_version, table=table)
    )


def render_active_filter(version: str | None = None) -> str:
    """检索 SQL 里的版本过滤条件。

    :param version: 指定版本则精确匹配（灰度对比用）；不指定则只按 active 过滤
    """
    if version:
        validate_embedding_version(version)
        return f"{ACTIVE_FILTER_CLAUSE} AND embedding_version = '{version}'"
    return ACTIVE_FILTER_CLAUSE


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    """embedding_version 切换前的**性能基准发布门禁**结论。

    为什么要有这道门：原文第六章把「千万级数据量单次向量检索 P95 ≤ 2 秒」称作
    **整条链路的验收线**，:data:`~.params.POC_CHECKLIST` 第 5 项也是它。但这条线原先
    只在两个地方起作用——选型时的 :meth:`~.backend.BackendSelector.decide_from_poc`
    与上线后的 :meth:`~.backend.BackendSelector.decide_from_runtime`，
    **换代发布这条路上一个门都没有**：``plan_switch`` 从来不问新版本压过没有，
    一条 UPDATE 就把全站检索切到未经基准验证的向量上。换代恰恰是最容易踩线的时刻
    （维度变了、模型变了、索引要重建），线上 P95 崩了才发现，只能回滚。

    ⚠️ 原文未明确，本项目设计：原文给了验收线与 POC 五项，但没说换代发布要不要复用它。
    本门禁复用同一条线（:data:`~.params.SEARCH_P95_SLA_SECONDS`），不新造判据；
    默认**不强制**（``require_benchmark=False``），只在没有基准数据时留一条 WARNING，
    以免既有调用方被一刀切拦住。要卡死就把 :attr:`VersionRegistry.require_benchmark`
    打开。

    :param passed: 是否放行
    :param reason_cn: 中文理由，可直接贴进发布单
    :param measured_p95_sec: 本次基准实测 P95（秒）；None 表示压根没测
    :param sla_seconds: 对照的验收线
    """

    passed: bool
    reason_cn: str
    measured_p95_sec: float | None = None
    sla_seconds: float = SEARCH_P95_SLA_SECONDS

    @property
    def evidence_missing(self) -> bool:
        """没有基准数据——放行也只是「没人拦」，不是「压过了」。"""
        return self.measured_p95_sec is None


def evaluate_release_gate(
    measured_p95_sec: float | None,
    *,
    sla_seconds: float = SEARCH_P95_SLA_SECONDS,
    require_benchmark: bool = False,
) -> ReleaseGate:
    """按验收线判一次发布门禁。纯函数，不碰台账，便于在发布流水线里单独调用。

    :param measured_p95_sec: 新版本在 :data:`~.params.SEARCH_SLA_SCALE_CN` 数据量上的
        实测 P95（秒）。None = 没做基准。
    :param sla_seconds: 验收线，默认原文的 2 秒
    :param require_benchmark: True 时「没做基准」也判不通过
    """
    if measured_p95_sec is None:
        if require_benchmark:
            return ReleaseGate(
                False,
                f"未提供基准实测 P95，且已开启强制基准：换代发布必须先在 "
                f"{SEARCH_SLA_SCALE_CN}数据量上压出 P95（验收线 {sla_seconds}s）",
                None,
                sla_seconds,
            )
        return ReleaseGate(
            True,
            f"未提供基准实测 P95（强制基准未开启）：放行，但这不等于压过了验收线 "
            f"{sla_seconds}s——建议发布前补一次 {SEARCH_SLA_SCALE_CN}基准",
            None,
            sla_seconds,
        )
    if measured_p95_sec <= sla_seconds:
        return ReleaseGate(
            True,
            f"基准实测 P95 {measured_p95_sec:.3f}s ≤ 验收线 {sla_seconds}s，准予发布",
            measured_p95_sec,
            sla_seconds,
        )
    return ReleaseGate(
        False,
        f"基准实测 P95 {measured_p95_sec:.3f}s 超过验收线 {sla_seconds}s，"
        "拒绝切换：换代前先调 HNSW 参数或按 backend.BackendSelector 降级到内表，"
        "不要把未达标的版本切成 active",
        measured_p95_sec,
        sla_seconds,
    )


class ReleaseGateBlocked(RuntimeError):
    """发布门禁未通过，切换被拒。异常里带着 :class:`ReleaseGate` 供发布单留痕。"""

    def __init__(self, gate: ReleaseGate) -> None:
        super().__init__(gate.reason_cn)
        self.gate = gate


@dataclass(slots=True)
class VersionRegistry:
    """进程内的 embedding_version 台账 + 切换编排。

    典型换代流程（原文第二章「灰度切换与一键回滚」）：
        reg = VersionRegistry()
        reg.register(EmbeddingVersion("clip_v1", "CLIP-ViT-B/32"))
        reg.register(EmbeddingVersion("clip_v2", "CLIP-ViT-L/14",
                                      status=VectorStatus.DEPRECATED))   # 先影子写入
        reg.plan_switch("clip_v1", "clip_v2")                            # 切换 SQL
        reg.rollback("clip_v2", "clip_v1")                               # 出事回滚

    ⚠️ 原文未明确，本项目设计：台账落在内存 / 调用方自行持久化。生产上应当把它落到
    控制面表（如 ods_*_config），此处不擅自新增表定义——表注册归 catalog 模块管。
    """

    versions: dict[str, EmbeddingVersion] = field(default_factory=dict)
    #: 是否强制要求换代发布带基准实测 P95。
    #: **默认 False = 行为与从前一致**（没基准也放行，只留 WARNING）；打开即卡死。
    require_benchmark: bool = False
    #: 最近一次 :meth:`plan_switch` 的门禁结论，供发布单留痕。
    last_gate: ReleaseGate | None = field(default=None, init=False)

    def register(self, version: EmbeddingVersion) -> EmbeddingVersion:
        """登记一个版本。重复登记直接覆盖，方便调度重放。"""
        self.versions[version.version] = version
        return version

    def get(self, version: str) -> EmbeddingVersion:
        try:
            return self.versions[version]
        except KeyError as exc:
            raise KeyError(f"未登记的 embedding_version: {version!r}") from exc

    def active_versions(self) -> tuple[EmbeddingVersion, ...]:
        """当前在役版本。正常只应有一个；灰度期间允许两个并存。"""
        return tuple(v for v in self.versions.values() if v.is_active)

    def require_single_active(self) -> EmbeddingVersion:
        """取唯一在役版本，多于一个说明灰度没收尾，直接报错而不是随便挑一个。"""
        actives = self.active_versions()
        if not actives:
            raise RuntimeError("没有任何 active 的 embedding_version，检索会返回空集")
        if len(actives) > 1:
            raise RuntimeError(
                "存在多个 active 版本："
                + "、".join(v.version for v in actives)
                + "；灰度期请在检索请求里显式指定 embedding_version"
            )
        return actives[0]

    def check_dim_compatible(self, version: str, index_dim: int) -> None:
        """维度对账：模型维度和索引 dim 不一致会让检索静默返回错误结果，必须提前拦。"""
        v = self.get(version)
        if v.dim != index_dim:
            raise ValueError(
                f"embedding_version={version} 的维度 {v.dim} 与索引 dim {index_dim} 不一致，"
                "换代时必须同步重建索引"
            )

    # ---- 切换编排 ----

    def plan_switch(
        self,
        from_version: str,
        to_version: str,
        *,
        measured_p95_sec: float | None = None,
    ) -> tuple[str, ...]:
        """生成灰度切换语句，并同步更新内存台账。

        :param measured_p95_sec: 新版本在 :data:`~.params.SEARCH_SLA_SCALE_CN` 数据量上的
            基准实测 P95（秒）。**默认 None：行为与从前完全一致**——不拦，只在日志里
            说明这次换代没有基准背书。传了值就走
            :func:`evaluate_release_gate`；超线抛 :class:`ReleaseGateBlocked`。
            把 :attr:`require_benchmark` 打开后，不传值也会被拦。
        :return: 待下发的 SQL 语句序列（先激活新版本，再退役旧版本——顺序反过来会出现
                 「一瞬间没有 active 版本」的检索空窗）
        :raises ReleaseGateBlocked: 性能基准发布门禁未通过
        """
        old, new = self.get(from_version), self.get(to_version)

        gate = evaluate_release_gate(measured_p95_sec, require_benchmark=self.require_benchmark)
        self.last_gate = gate
        if not gate.passed:
            _log.error("embedding_version 切换被门禁拒绝: %s", gate.reason_cn)
            raise ReleaseGateBlocked(gate)
        if gate.evidence_missing:
            _log.warning(
                "embedding_version %s -> %s 换代没有基准实测 P95：%s",
                from_version,
                to_version,
                gate.reason_cn,
            )

        self.versions[new.version] = new.with_status(VectorStatus.ACTIVE)
        self.versions[old.version] = old.with_status(VectorStatus.DEPRECATED)
        _log.info("embedding_version 切换: %s -> %s", from_version, to_version)
        return (
            render_activate_sql(to_version),
            render_deprecate_sql(from_version),
        )

    def rollback(self, from_version: str, to_version: str) -> tuple[str, ...]:
        """一键回滚，返回 SQL 并回写台账。"""
        old, new = self.get(from_version), self.get(to_version)
        self.versions[old.version] = old.with_status(VectorStatus.DEPRECATED)
        self.versions[new.version] = new.with_status(VectorStatus.ACTIVE)
        _log.warning("embedding_version 回滚: %s -> %s", from_version, to_version)
        return (render_rollback_sql(from_version=from_version, to_version=to_version),)
