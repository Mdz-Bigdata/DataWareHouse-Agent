"""六项闭环业务服务：把 11 张 ADS 表变成业务语义接口。

来源 [S1-全景] 第九章《能力用得出：服务层把闭环变成接口》（原文原话）：

    业务平台（谁在调用）→ 统一 API 网关（认证、限流、审计）
    → 六项闭环业务服务 → 基础技术服务（元数据 / 权限 / 质量 / 告警）

    | 业务服务 | 回答的业务问题 | 代表 API |
    | 🚦 数据生产追踪 | 这批数据到哪一步了？哪个环节最慢？ | production/batch/.../progress |
    | 🎯 场景检索与样本圈选 | 缺雨天数据，多久能从库里圈出来？ | scene/search · scene/curate |
    | 📦 数据集版本与交付 | V3 的数据到底从哪来？谁用了它？ | dataset/.../composition |
    | 🔁 模型迭代评测 | 效果回退是数据问题还是模型问题？ | model/compare · badcase/root-cause |
    | 🔄 回传与挖掘闭环 | 触发到进训练集多久？缺口补上了吗？ | trigger/.../closed-loop · scene-gap/status |
    | 🧬 全链路血缘追溯 | Badcase 数据从哪来？影响了哪些模型？ | lineage/business/trace · lineage/impact |

本模块是第三层（六项业务服务），上接 :mod:`gateway`（第二层），
下接 :mod:`query`（ADS 内表取数）。三条纪律：

  1. **零 JOIN**：每个接口只读 ADS 表，且一次只读一张——需要两张表的结论
     （如 OTA 放行要同时看部署汇总与触发热力图）由服务层在内存里合，
     不下推成跨表 JOIN。[S1-05] 第一章「ADS 存答案」的前提就是查询侧不再拼表。
  2. **口径唯一**：判定阈值全部引用 :mod:`constants`，网格与热力等级引用 :mod:`geo`，
     服务层不重新定义任何一个数字。
  3. **缺数据不放行**：门禁类接口（OTA 放行、回归拦截）拿不到判据时一律判不通过，
     绝不把「没查到」当成「没问题」。

不由本层实现的两项能力显式抛错并给出指引，而不是静默降级：
语义检索走 vector 子系统（:class:`SemanticSearchPort`），
血缘多跳遍历走 lineage 子系统（:class:`LineagePort`）——
与 :func:`routing.route_for` 对向量路径的处理同一风格。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from .constants import (
    ADS_AGGREGATION_MAX_ROWS,
    ADS_QUERY_DEFAULT_LIMIT,
    ADS_QUERY_MAX_LIMIT,
    ASSET_IDLE_DAYS_THRESHOLD,
    ASSET_LOW_QUALITY_SCORE_THRESHOLD,
    ASSET_QUALITY_SCORE_MAX,
    LINEAGE_MAX_TRAVERSAL_DEPTH,
    LINEAGE_MIN_TRAVERSAL_DEPTH,
    LINEAGE_QUERY_DIRECTIONS,
    MINING_TAG_COVERAGE_WARN_THRESHOLD,
    MINING_TAG_PENDING_REVIEW_WARN_COUNT,
    MINING_TAG_SOURCES,
    MODEL_COMPARE_DEMO_BASELINE_VERSION,
    MODEL_COMPARE_DEMO_DATASET_NAME,
    MODEL_COMPARE_DEMO_MODEL_VERSION,
    MODEL_REGRESSION_TOLERANCE_PP_DEFAULT,
    OTA_GATE_CONDITION_COUNT,
    OTA_GATE_DECISION_HOLD,
    OTA_GATE_DECISION_PASS,
    OTA_GATE_FLOAT_EPSILON,
    OTA_GATE_MAX_SAFETY_ISSUE_COUNT,
    OTA_GATE_MIN_SUCCESS_RATE,
    OTA_GATE_MIN_TRIGGER_GROWTH_RATE,
    OTA_POST_RELEASE_OBSERVE_DAYS,
    OTA_RELEASE_CHANNELS,
    PRODUCTION_BLOCKED_ALERT_HOURS,
    SCENE_COVERAGE_STATUS_FLOW,
    STORAGE_COST_MOM_ALERT_THRESHOLD,
    TRIGGER_HEAT_LEVEL_MAX,
    TRIGGER_HEAT_LEVEL_MIN,
    TRIGGER_WOW_ANOMALY_THRESHOLD,
    TRIGGER_WOW_WINDOW_DAYS,
)
from .errors import AdsQueryError, ServiceUnavailableError
from .geo import GeoGridCell, grid_center, heat_level
from .products import (
    PRODUCTS,
    ClosedLoopService,
    get_product,
    products_by_service,
)
from .query import AdsQuery, AdsQueryService, Filter, Row

__all__ = [
    # 端口
    "SemanticSearchPort",
    "LineagePort",
    "SafetyIssuePort",
    # 值对象
    "GateCondition",
    "OtaGateDecision",
    "RegressionCheck",
    "TriggerGrowth",
    "HeatCell",
    "GridHotspot",
    "WowAnomaly",
    "HardCaseAdoption",
    "HardCaseAdoptionSummary",
    # 口径函数
    "adoption_rate",
    "growth_rate",
    "evaluate_ota_release_gate",
    # 六项服务
    "ProductionTrackingService",
    "SceneSearchCurationService",
    "DatasetVersionDeliveryService",
    "ModelIterationEvaluationService",
    "TriggerMiningClosedLoopService",
    "LineageTraceService",
    # 装配
    "ClosedLoopServiceSuite",
    "ApiBinding",
    "API_BINDINGS",
    "DELEGATED_APIS",
    "CATALOG_GAPS",
    "verify_service_exits",
    "service_exit_matrix",
]


# ===========================================================================
# 一、外部子系统端口（本层只定义，不实现）
# ===========================================================================


class SemanticSearchPort(Protocol):
    """语义检索端口：以文搜图 / 以图搜图，由 vector 子系统实现。

    [S1-全景] 第七章：HNSW 索引建在 StarRocks 外部表上，向量与标量同表同权限，
    验收线 P95 ≤2s。ADS 服务层只做标量路径，命中的 data_id 再回 ADS 表取聚合值。
    """

    def search(self, text: str, *, top_k: int) -> list[str]: ...


class LineagePort(Protocol):
    """血缘追溯端口：图库找关系、湖仓取明细（[S1-全景] 第八章②），由 lineage 子系统实现。"""

    def trace(self, anchor_id: str, *, direction: str, depth: int) -> list[dict[str, Any]]: ...


class SafetyIssuePort(Protocol):
    """安全相关问题计数端口：由问题分析平台（dwd_issue_detail 口径）提供。

    ⚠️ ``ads_ota_deployment_summary`` 里没有「安全相关问题数」这一列（见本模块
    :data:`CATALOG_GAPS`），所以 OTA 三条件放行的第三条判据必须由调用方给出——
    要么直接传 ``safety_issue_count``，要么注入本端口。两者都没有时门禁判**不通过**，
    绝不把「查不到」当成「0 起」。
    """

    def safety_issue_count(
        self, *, software_version: str, since: date, until: date, project_code: str | None = None
    ) -> int: ...


# ===========================================================================
# 二、小工具：类型归一
# ===========================================================================


def _as_date(value: Any, *, field_name: str = "日期") -> date:
    """把行里的日期值归一成 :class:`datetime.date`。

    ADS 行可能来自 StarRocks（``date``/``datetime``）或离线行源（ISO 字符串），
    两种形态在同一个接口里都要能比大小。

    Raises:
        AdsQueryError: 值不是日期，或字符串不是 ``yyyy-MM-dd`` / ISO 格式。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            raise AdsQueryError(f"{field_name} {value!r} 不是 yyyy-MM-dd 格式") from None
    raise AdsQueryError(f"{field_name} 期望日期，收到 {type(value).__name__}: {value!r}")


def _as_float(value: Any) -> float | None:
    """数值归一；``None`` / 空串 / 非数值一律回 ``None``（缺数据就是缺数据，不补 0）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    """整数归一。浮点按截断取整（行里的计数列都是整数语义）。"""
    number = _as_float(value)
    return None if number is None else int(number)


def _as_bool(value: Any) -> bool | None:
    """布尔归一：兼容 StarRocks 的 0/1 与字符串 'true'/'false'。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "y", "yes"):
            return True
        if low in ("false", "0", "n", "no"):
            return False
    return None


def _ge(actual: float, threshold: float) -> bool:
    """``actual >= threshold``，只吸收浮点表示误差（见 OTA_GATE_FLOAT_EPSILON）。"""
    return actual >= threshold - OTA_GATE_FLOAT_EPSILON


# ===========================================================================
# 三、口径：增长率 / 采纳率
# ===========================================================================


def growth_rate(current: float | int | None, previous: float | int | None) -> float | None:
    """环比增长率 =（本期 − 上期）/ 上期。

    Args:
        current: 本期值。
        previous: 上期值。

    Returns:
        增长率；上期为 0 或缺任一侧时返回 ``None``——除以 0 得不到「增长了多少倍」，
        这种情况必须由调用方按「新增热点」单独处理，不能悄悄当成 0。

    Examples:
        原文案例：发布后一周回传触发量环比增长 30%（[S1-05] 第五章表 8）

        >>> growth_rate(1300, 1000)
        0.3
    """
    if current is None or previous is None:
        return None
    if previous == 0:
        return None
    return (float(current) - float(previous)) / float(previous)


def adoption_rate(adopted_count: int | None, hard_case_count: int | None) -> float | None:
    """难例采纳率 = 训练采纳数 / 难例总数（[S1-05] 第四章 3 / 表 5 口径）。

    原文案例原话：「v3.2 评测挖出 3,200 条难例（夜间行人 900、逆光车辆 700），
    训练平台采纳 2,800 条（采纳率 87.5%）混入 v3.3 训练集」——
    2,800 / 3,200 = 87.5%，分母是**挖出的难例总数**，不是 Badcase 数、
    也不是关联 clip 数。

    Args:
        adopted_count: 训练平台采纳数（``ads_hard_case_library.adopted_count``）。
        hard_case_count: 难例总数（``ads_hard_case_library.hard_case_count``）。

    Returns:
        采纳率；总数为 0 或缺任一侧时返回 ``None``（没挖出难例就无所谓采纳率）。

    Raises:
        ValueError: 出现负数——计数为负说明上游聚合错了，不能当成 0 糊过去。

    Examples:
        >>> adoption_rate(2800, 3200)
        0.875
    """
    if adopted_count is None or hard_case_count is None:
        return None
    if adopted_count < 0 or hard_case_count < 0:
        raise ValueError(
            f"难例计数不能为负：adopted_count={adopted_count!r}, "
            f"hard_case_count={hard_case_count!r}"
        )
    if hard_case_count == 0:
        return None
    return adopted_count / hard_case_count


# ===========================================================================
# 四、OTA 灰度三条件放行
# ===========================================================================


@dataclass(frozen=True, slots=True)
class GateCondition:
    """放行门的一条判据。

    Args:
        key: 判据键（审计与前端按它定位）。
        name_cn: 判据中文名（原文用词）。
        comparator: ``>=`` 或 ``<=``。
        threshold: 门槛值，来自 :mod:`constants`。
        actual: 实测值；``None`` 表示判据数据缺失。
        passed: 是否通过。数据缺失一律为 ``False``。
        source_cn: 该门槛在原文里的出处。
        detail_cn: 人话结论，直接进审计与看板。
    """

    key: str
    name_cn: str
    comparator: str
    threshold: float
    actual: float | None
    passed: bool
    source_cn: str
    detail_cn: str

    @property
    def missing(self) -> bool:
        """判据数据是否缺失（缺失 → 不通过，但要和「实测不达标」区分开）。"""
        return self.actual is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name_cn,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "actual": self.actual,
            "passed": self.passed,
            "missing": self.missing,
            "source": self.source_cn,
            "detail": self.detail_cn,
        }


@dataclass(frozen=True, slots=True)
class RegressionCheck:
    """表 7 的回归项检查：「修复后再 OTA，避免带病上车」（[S1-05] 第五章表 7）。

    原文案例：v3.3 与 v3.2 在「城区 NOA 评测集 v5」对比——总体通过率 91.2% vs 87.5%，
    夜间场景 +8.3pp，高速场景 −1.2pp → 评测平台标记回归项。
    """

    model_version: str
    baseline_model_version: str
    checked_scene_count: int
    worst_scene_type: str
    worst_diff_pp: float | None
    best_scene_type: str
    best_diff_pp: float | None
    regression_scenes: tuple[str, ...]

    @classmethod
    def from_rows(
        cls, rows: Sequence[Row], *, tolerance_pp: float = MODEL_REGRESSION_TOLERANCE_PP_DEFAULT
    ) -> RegressionCheck | None:
        """从 ``ads_model_version_comparison`` 的行里归纳回归结论。

        判回归的口径：任一场景的 ``pass_rate_diff_pp`` 低于 ``tolerance_pp``
        （默认 0.0 pp，即通过率比基线低就算回归），或该行 ``regression_flag`` 为真。

        Returns:
            没有行时返回 ``None``（调用方据此判断「评测数据缺失」）。
        """
        if not rows:
            return None
        diffs: list[tuple[str, float]] = []
        regressions: list[str] = []
        for row in rows:
            scene = str(row.get("scene_type", ""))
            diff = _as_float(row.get("pass_rate_diff_pp"))
            flagged = _as_bool(row.get("regression_flag"))
            if diff is not None:
                diffs.append((scene, diff))
                if diff < tolerance_pp:
                    regressions.append(scene)
                    continue
            if flagged:
                regressions.append(scene)
        worst = min(diffs, key=lambda item: item[1]) if diffs else ("", None)
        best = max(diffs, key=lambda item: item[1]) if diffs else ("", None)
        first = rows[0]
        return cls(
            model_version=str(first.get("model_version", "")),
            baseline_model_version=str(first.get("baseline_model_version", "")),
            checked_scene_count=len(rows),
            worst_scene_type=worst[0],
            worst_diff_pp=worst[1],
            best_scene_type=best[0],
            best_diff_pp=best[1],
            regression_scenes=tuple(dict.fromkeys(regressions)),
        )

    @property
    def has_regression(self) -> bool:
        return bool(self.regression_scenes)


@dataclass(frozen=True, slots=True)
class OtaGateDecision:
    """OTA「灰度 → 全量」放行结论。

    [S1-05] 第五章表 8 原话：「v3.3 灰度推送 500 台车：升级成功率 99.2%，
    发布后一周回传触发量环比增长 30%（新版本主动采集策略生效），安全相关问题 0 起
    → 确认全量推送，回传数据反哺下一轮训练，闭环真正转动起来。」

    三个观察值并列之后才有「确认全量推送」这个结论，所以三条件是**与**关系：
    :attr:`grey_conditions` 三条全通过才 :attr:`grey_passed`。

    第四条（评测回归项）不是原文表 8 的条件，而是 [S1-05] 表 7 与 [S1-全景] 第一章
    「仿真评测必须先于 OTA 部署」这条硬约束在放行门上的落点：带回归项的版本
    「修复后再 OTA」。它只在调用方要求检查评测时加入，可按项目用
    :attr:`tolerance_pp` 放宽。

    Args:
        ota_task_id: OTA 任务 ID（``ads_ota_deployment_summary`` 主键）。
        software_version: 软件版本号。
        release_channel: 发布通道，取值见 :data:`constants.OTA_RELEASE_CHANNELS`。
        conditions: 判据列表，前 :data:`constants.OTA_GATE_CONDITION_COUNT` 条
            恒为原文三条件且顺序与原文一致。
        tolerance_pp: 回归容差（百分点）。
    """

    ota_task_id: str
    software_version: str
    release_channel: str
    conditions: tuple[GateCondition, ...]
    tolerance_pp: float = MODEL_REGRESSION_TOLERANCE_PP_DEFAULT

    @property
    def grey_conditions(self) -> tuple[GateCondition, ...]:
        """原文表 8 的三条件（成功率 / 触发量环比 / 安全问题），顺序与原文一致。"""
        return self.conditions[:OTA_GATE_CONDITION_COUNT]

    @property
    def grey_passed(self) -> bool:
        """三条件的**与**：全部通过才为真。"""
        return all(c.passed for c in self.grey_conditions)

    @property
    def passed(self) -> bool:
        """全部判据（含可选的回归项）通过才放行。"""
        return all(c.passed for c in self.conditions)

    @property
    def decision(self) -> str:
        """``full_rollout``（确认全量推送）/ ``hold``（不放行）。"""
        return OTA_GATE_DECISION_PASS if self.passed else OTA_GATE_DECISION_HOLD

    @property
    def blocked_by(self) -> tuple[str, ...]:
        """没通过的判据键——审计与告警按它归因。"""
        return tuple(c.key for c in self.conditions if not c.passed)

    @property
    def missing_inputs(self) -> tuple[str, ...]:
        """因**缺数据**而不通过的判据键（与「实测不达标」区分）。"""
        return tuple(c.key for c in self.conditions if c.missing)

    @property
    def summary_cn(self) -> str:
        if self.passed:
            return f"{self.ota_task_id}：三条件全部满足 → 确认全量推送"
        reasons = "；".join(c.detail_cn for c in self.conditions if not c.passed)
        return f"{self.ota_task_id}：不放行（{reasons}）"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ota_task_id": self.ota_task_id,
            "software_version": self.software_version,
            "release_channel": self.release_channel,
            "decision": self.decision,
            "passed": self.passed,
            "grey_passed": self.grey_passed,
            "blocked_by": list(self.blocked_by),
            "missing_inputs": list(self.missing_inputs),
            "tolerance_pp": self.tolerance_pp,
            "conditions": [c.as_dict() for c in self.conditions],
            "summary": self.summary_cn,
        }


_MISSING_HINT: Final[str] = "判据数据缺失，放行必须有据——按不通过处理"


def evaluate_ota_release_gate(
    *,
    ota_task_id: str,
    deploy_success_rate: float | None,
    post_release_trigger_growth_rate: float | None,
    safety_issue_count: int | None,
    software_version: str = "",
    release_channel: str = "",
    regression: RegressionCheck | None = None,
    require_evaluation: bool = False,
    tolerance_pp: float = MODEL_REGRESSION_TOLERANCE_PP_DEFAULT,
) -> OtaGateDecision:
    """按原文三条件判定 OTA 灰度能否转全量。

    三条件（[S1-05] 第五章表 8，逐字）：

      ① 升级成功率 ≥ :data:`constants.OTA_GATE_MIN_SUCCESS_RATE`（99.2%）
      ② 发布后一周回传触发量环比增长 ≥
         :data:`constants.OTA_GATE_MIN_TRIGGER_GROWTH_RATE`（+30%）
      ③ 安全相关问题 ≤ :data:`constants.OTA_GATE_MAX_SAFETY_ISSUE_COUNT`（0 起）

    三条件是**与**关系——原文是三个观察值并列后才「确认全量推送」。

    比较号取 ``≥`` / ``≤`` 而不是严格不等号，依据是原文本身：99.2% / +30% / 0 起
    这三个**实测值**在原文里的结论就是「确认全量推送」，所以门槛必须把等号算进来，
    否则原文案例自己都过不了门。反过来，99.19% 这种「差一点」一律不放行——
    :data:`constants.OTA_GATE_FLOAT_EPSILON` 只抵消 IEEE-754 表示误差
    （1e-9，折算成百分点是 1e-7 pp），不放宽任何一条门槛。

    Args:
        ota_task_id: OTA 任务 ID。
        deploy_success_rate: 实测升级成功率（0~1）。
        post_release_trigger_growth_rate: 实测发布后一周回传触发量环比增长率
            （可为负，表示回传量下降）。
        safety_issue_count: 实测安全相关问题数（非负）。
        software_version: 软件版本号，仅用于结论回显。
        release_channel: 发布通道；非空时必须是
            :data:`constants.OTA_RELEASE_CHANNELS` 之一。
        regression: 评测回归项检查结论；传入即追加第四条判据。
        require_evaluation: 要求必须有评测结论。为真且 ``regression`` 为 ``None``
            时追加一条**不通过**的判据——对应原文硬约束「仿真评测必须先于 OTA 部署」。
        tolerance_pp: 回归容差（百分点），默认
            :data:`constants.MODEL_REGRESSION_TOLERANCE_PP_DEFAULT`。

    Returns:
        :class:`OtaGateDecision`。

    Raises:
        ValueError: 发布通道不在三档之内；或判据本身不合法——成功率落在 [0, 1] 之外、
            安全问题数为负。这类值说明上游算错了，**不能当成一条通过的判据**：
            一个 -1 起的安全问题数会让 ``≤ 0`` 这条门槛悄悄成立，
            是最典型的「带病上车」漏放路径。

    Examples:
        原文案例（99.2% / +30% / 0 起）：

        >>> d = evaluate_ota_release_gate(
        ...     ota_task_id="OTA-v3.3", deploy_success_rate=0.992,
        ...     post_release_trigger_growth_rate=0.30, safety_issue_count=0)
        >>> d.grey_passed, d.decision
        (True, 'full_rollout')

        差 0.01 个百分点也不放行：

        >>> d = evaluate_ota_release_gate(
        ...     ota_task_id="OTA-v3.3", deploy_success_rate=0.9919,
        ...     post_release_trigger_growth_rate=0.30, safety_issue_count=0)
        >>> d.passed, d.blocked_by
        (False, ('deploy_success_rate',))

        安全问题只要 1 起就不放行，另外两条再漂亮也没用：

        >>> d = evaluate_ota_release_gate(
        ...     ota_task_id="OTA-v3.3", deploy_success_rate=0.999,
        ...     post_release_trigger_growth_rate=0.80, safety_issue_count=1)
        >>> d.passed, d.blocked_by
        (False, ('safety_issue_count',))
    """
    if release_channel and release_channel not in OTA_RELEASE_CHANNELS:
        raise ValueError(
            f"发布通道 {release_channel!r} 不合法；可选：{', '.join(OTA_RELEASE_CHANNELS)}"
        )
    if deploy_success_rate is not None and not 0.0 <= deploy_success_rate <= 1.0:
        raise ValueError(
            f"升级成功率 {deploy_success_rate!r} 越界：应在 [0, 1] 之内"
            f"（ads_ota_deployment_summary.deploy_success_rate 是比率列）。"
            f"越界值多半是上游把分母算错了，放行门不接受这种判据"
        )
    if safety_issue_count is not None and safety_issue_count < 0:
        raise ValueError(
            f"安全相关问题数 {safety_issue_count!r} 为负：计数为负说明上游聚合错了。"
            f"负数会让「≤ {OTA_GATE_MAX_SAFETY_ISSUE_COUNT} 起」这条门槛凭空成立，"
            f"必须报错而不是放行"
        )

    conditions: list[GateCondition] = [
        _rate_condition(
            key="deploy_success_rate",
            name_cn="升级成功率",
            actual=deploy_success_rate,
            threshold=OTA_GATE_MIN_SUCCESS_RATE,
            source_cn="[S1-05] 第五章表 8「升级成功率 99.2%」",
        ),
        _rate_condition(
            key="post_release_trigger_growth_rate",
            name_cn="发布后一周回传触发量环比增长",
            actual=post_release_trigger_growth_rate,
            threshold=OTA_GATE_MIN_TRIGGER_GROWTH_RATE,
            source_cn="[S1-05] 第五章表 8「发布后一周回传触发量环比增长 30%」",
        ),
        _count_condition(
            key="safety_issue_count",
            name_cn="安全相关问题",
            actual=safety_issue_count,
            threshold=OTA_GATE_MAX_SAFETY_ISSUE_COUNT,
            source_cn="[S1-05] 第五章表 8「安全相关问题 0 起」",
        ),
    ]

    if regression is not None:
        worst = regression.worst_diff_pp
        passed = worst is not None and not regression.has_regression and _ge(worst, tolerance_pp)
        if worst is None:
            detail = f"评测回归项：{_MISSING_HINT}"
        elif passed:
            detail = (
                f"评测回归项：{regression.checked_scene_count} 个场景无回归，"
                f"最差 {regression.worst_scene_type} {worst:+.1f}pp ≥ 容差 {tolerance_pp:+.1f}pp"
            )
        else:
            detail = (
                f"评测回归项：{'/'.join(regression.regression_scenes) or regression.worst_scene_type}"
                f" 回归 {worst:+.1f}pp < 容差 {tolerance_pp:+.1f}pp，修复后再 OTA"
            )
        conditions.append(
            GateCondition(
                key="evaluation_regression",
                name_cn="评测回归项",
                comparator=">=",
                threshold=tolerance_pp,
                actual=worst,
                passed=passed,
                source_cn="[S1-05] 第五章表 7「标记回归项…修复后再 OTA，避免带病上车」",
                detail_cn=detail,
            )
        )
    elif require_evaluation:
        conditions.append(
            GateCondition(
                key="evaluation_regression",
                name_cn="评测回归项",
                comparator=">=",
                threshold=tolerance_pp,
                actual=None,
                passed=False,
                source_cn="[S1-全景] 第一章「仿真评测必须先于 OTA 部署——顺序不能乱」",
                detail_cn=f"评测回归项：没有该版本的版本对比结论，{_MISSING_HINT}",
            )
        )

    return OtaGateDecision(
        ota_task_id=ota_task_id,
        software_version=software_version,
        release_channel=release_channel,
        conditions=tuple(conditions),
        tolerance_pp=tolerance_pp,
    )


def _rate_condition(
    *, key: str, name_cn: str, actual: float | None, threshold: float, source_cn: str
) -> GateCondition:
    passed = actual is not None and _ge(actual, threshold)
    if actual is None:
        detail = f"{name_cn}：{_MISSING_HINT}"
    else:
        detail = (
            f"{name_cn} {actual:.3%} {'≥' if passed else '<'} 门槛 {threshold:.3%}"
            f"{'' if passed else '，不放行'}"
        )
    return GateCondition(
        key=key,
        name_cn=name_cn,
        comparator=">=",
        threshold=threshold,
        actual=actual,
        passed=passed,
        source_cn=source_cn,
        detail_cn=detail,
    )


def _count_condition(
    *, key: str, name_cn: str, actual: int | None, threshold: int, source_cn: str
) -> GateCondition:
    passed = actual is not None and actual <= threshold
    if actual is None:
        detail = f"{name_cn}：{_MISSING_HINT}"
    else:
        detail = (
            f"{name_cn} {actual} 起 {'≤' if passed else '>'} 门槛 {threshold} 起"
            f"{'' if passed else '，不放行'}"
        )
    return GateCondition(
        key=key,
        name_cn=name_cn,
        comparator="<=",
        threshold=float(threshold),
        actual=None if actual is None else float(actual),
        passed=passed,
        source_cn=source_cn,
        detail_cn=detail,
    )


# ===========================================================================
# 五、热力图与难例采纳率的值对象
# ===========================================================================


@dataclass(frozen=True, slots=True)
class TriggerGrowth:
    """两个观察窗口之间的回传触发量对比（表 8 条件②的实测值来源）。"""

    current_window: tuple[date, date]
    previous_window: tuple[date, date]
    current_count: int
    previous_count: int
    window_days: int

    @property
    def rate(self) -> float | None:
        """环比增长率；上期为 0 时为 ``None``（无法计算环比）。"""
        return growth_rate(self.current_count, self.previous_count)

    @property
    def is_new_baseline(self) -> bool:
        """上期为 0：算不出环比，但不是「没查到数据」。

        两者在放行门上都判**不通过**（口径不变），但归因不同，运维要能分开看：
        上期为 0 说明这是首次发布 / 新车型，得换个判据（比如看绝对量）再人工确认；
        两期都为 0 才是「热力图还没物化出来」。
        """
        return self.previous_count == 0 and self.current_count > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "current_window": [d.isoformat() for d in self.current_window],
            "previous_window": [d.isoformat() for d in self.previous_window],
            "current_count": self.current_count,
            "previous_count": self.previous_count,
            "rate": self.rate,
            "is_new_baseline": self.is_new_baseline,
        }


@dataclass(frozen=True, slots=True)
class HeatCell:
    """热力图的一格：一天 × 一个项目 × 一个网格 × 一种触发类型。

    对应 ``ads_trigger_heatmap`` 的一行（零 JOIN：网格中心、城市、代表样本都打平在行内）。
    """

    stat_date: date
    project_code: str
    geo_grid_id: str
    trigger_type: str
    trigger_cnt: int
    heat_level: int | None
    vehicle_cnt: int
    hard_case_cnt: int
    grid_center_lat: float | None
    grid_center_lon: float | None
    city_code: str
    city_name: str
    road_type: str
    top_scene_tag: str
    sample_data_id: str

    @classmethod
    def from_row(cls, row: Row) -> HeatCell:
        return cls(
            stat_date=_as_date(row.get("stat_date"), field_name="stat_date"),
            project_code=str(row.get("project_code", "")),
            geo_grid_id=str(row.get("geo_grid_id", "")),
            trigger_type=str(row.get("trigger_type", "")),
            trigger_cnt=_as_int(row.get("trigger_cnt")) or 0,
            heat_level=_as_int(row.get("heat_level")),
            vehicle_cnt=_as_int(row.get("vehicle_cnt")) or 0,
            hard_case_cnt=_as_int(row.get("hard_case_cnt")) or 0,
            grid_center_lat=_as_float(row.get("grid_center_lat")),
            grid_center_lon=_as_float(row.get("grid_center_lon")),
            city_code=str(row.get("city_code") or ""),
            city_name=str(row.get("city_name") or ""),
            road_type=str(row.get("road_type") or ""),
            top_scene_tag=str(row.get("top_scene_tag") or ""),
            sample_data_id=str(row.get("sample_data_id") or ""),
        )

    @property
    def expected_heat_level(self) -> int:
        """按 :func:`geo.heat_level` 重算的等级——与批作业同一口径。"""
        return heat_level(self.trigger_cnt)

    @property
    def heat_level_matches(self) -> bool:
        """行里存的等级是否与重算结果一致（对账用：不一致说明物化与服务层口径漂了）。"""
        return self.heat_level is None or self.heat_level == self.expected_heat_level

    @property
    def cell(self) -> GeoGridCell:
        """网格几何：中心点与四至，前端画方格用。"""
        return grid_center(self.geo_grid_id)


@dataclass(frozen=True, slots=True)
class GridHotspot:
    """把一个网格上的各类触发合并后的热点（大屏地图的一个色块）。"""

    geo_grid_id: str
    trigger_cnt: int
    vehicle_cnt: int
    hard_case_cnt: int
    heat_level: int
    trigger_types: tuple[str, ...]
    top_trigger_type: str
    top_scene_tag: str
    city_name: str
    road_type: str
    sample_data_id: str
    center_lat: float | None
    center_lon: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "geo_grid_id": self.geo_grid_id,
            "trigger_cnt": self.trigger_cnt,
            "vehicle_cnt": self.vehicle_cnt,
            "hard_case_cnt": self.hard_case_cnt,
            "heat_level": self.heat_level,
            "trigger_types": list(self.trigger_types),
            "top_trigger_type": self.top_trigger_type,
            "top_scene_tag": self.top_scene_tag,
            "city_name": self.city_name,
            "road_type": self.road_type,
            "sample_data_id": self.sample_data_id,
            "center": [self.center_lat, self.center_lon],
        }


@dataclass(frozen=True, slots=True)
class WowAnomaly:
    """周环比异常的一格：某网格某触发类型涨得太快。

    原文案例：某区域「AEB 误触发」周环比上升 45%（[S1-05] 第五章表 9）。
    告警线取 :data:`constants.TRIGGER_WOW_ANOMALY_THRESHOLD`（本项目设计的 30%），
    45% 必然被捞出来。
    """

    geo_grid_id: str
    trigger_type: str
    current_count: int
    previous_count: int
    wow_rate: float | None
    threshold: float
    is_new_hotspot: bool
    city_name: str
    road_type: str
    top_scene_tag: str
    sample_data_id: str

    @property
    def reason_cn(self) -> str:
        if self.is_new_hotspot:
            return f"{self.geo_grid_id} 的「{self.trigger_type}」上周为 0、本周 {self.current_count} 次，新增热点"
        return (
            f"{self.geo_grid_id} 的「{self.trigger_type}」周环比上升 "
            f"{(self.wow_rate or 0.0):.0%}（告警线 {self.threshold:.0%}）"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "geo_grid_id": self.geo_grid_id,
            "trigger_type": self.trigger_type,
            "current_count": self.current_count,
            "previous_count": self.previous_count,
            "wow_rate": self.wow_rate,
            "threshold": self.threshold,
            "is_new_hotspot": self.is_new_hotspot,
            "city_name": self.city_name,
            "road_type": self.road_type,
            "top_scene_tag": self.top_scene_tag,
            "sample_data_id": self.sample_data_id,
            "reason": self.reason_cn,
        }


@dataclass(frozen=True, slots=True)
class HardCaseAdoption:
    """难例库的一行：一类难例 × 一个来源 × 一个模型版本的采纳与闭环验证效果。"""

    stat_date: date
    hard_case_category: str
    source_type: str
    model_version: str
    hard_case_count: int
    adopted_count: int
    stored_adoption_rate: float | None
    adopted_dataset_id: str
    adopted_dataset_version: str
    retrain_model_version: str
    miss_rate_drop_pp: float | None
    metric_gain_pp: float | None
    closed_loop_status: str

    @classmethod
    def from_row(cls, row: Row) -> HardCaseAdoption:
        return cls(
            stat_date=_as_date(row.get("stat_date"), field_name="stat_date"),
            hard_case_category=str(row.get("hard_case_category", "")),
            source_type=str(row.get("source_type", "")),
            model_version=str(row.get("model_version", "")),
            hard_case_count=_as_int(row.get("hard_case_count")) or 0,
            adopted_count=_as_int(row.get("adopted_count")) or 0,
            stored_adoption_rate=_as_float(row.get("adoption_rate")),
            adopted_dataset_id=str(row.get("adopted_dataset_id") or ""),
            adopted_dataset_version=str(row.get("adopted_dataset_version") or ""),
            retrain_model_version=str(row.get("retrain_model_version") or ""),
            miss_rate_drop_pp=_as_float(row.get("miss_rate_drop_pp")),
            metric_gain_pp=_as_float(row.get("metric_gain_pp")),
            closed_loop_status=str(row.get("closed_loop_status") or ""),
        )

    @property
    def adoption_rate(self) -> float | None:
        """按 :func:`adoption_rate` 重算的采纳率（服务层不信任列里的值，自己算一遍）。"""
        return adoption_rate(self.adopted_count, self.hard_case_count)

    @property
    def rate_matches(self) -> bool:
        """列里存的采纳率与重算值是否一致——不一致说明物化口径漂了，看板要标出来。"""
        mine, stored = self.adoption_rate, self.stored_adoption_rate
        if mine is None or stored is None:
            return mine is None and stored is None
        return abs(mine - stored) <= OTA_GATE_FLOAT_EPSILON

    @property
    def verified(self) -> bool:
        """是否完成闭环验证：采纳后重训，并落到了指标提升 / 漏检率下降。"""
        return self.adopted_count > 0 and bool(self.retrain_model_version)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stat_date": self.stat_date.isoformat(),
            "hard_case_category": self.hard_case_category,
            "source_type": self.source_type,
            "model_version": self.model_version,
            "hard_case_count": self.hard_case_count,
            "adopted_count": self.adopted_count,
            "adoption_rate": self.adoption_rate,
            "stored_adoption_rate": self.stored_adoption_rate,
            "rate_matches": self.rate_matches,
            "adopted_dataset": f"{self.adopted_dataset_id}@{self.adopted_dataset_version}".strip(
                "@"
            ),
            "retrain_model_version": self.retrain_model_version,
            "miss_rate_drop_pp": self.miss_rate_drop_pp,
            "metric_gain_pp": self.metric_gain_pp,
            "closed_loop_status": self.closed_loop_status,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class HardCaseAdoptionSummary:
    """难例采纳率的汇总视图：整批难例进了多少、闭环验证到哪一步。"""

    rows: tuple[HardCaseAdoption, ...]
    hard_case_count: int
    adopted_count: int

    @property
    def adoption_rate(self) -> float | None:
        """整体采纳率 = Σ采纳 / Σ难例（原文案例 2,800 / 3,200 = 87.5%）。"""
        return adoption_rate(self.adopted_count, self.hard_case_count)

    @property
    def by_category(self) -> dict[str, float | None]:
        """按难例类别拆的采纳率。"""
        buckets: dict[str, list[int]] = {}
        for row in self.rows:
            bucket = buckets.setdefault(row.hard_case_category, [0, 0])
            bucket[0] += row.adopted_count
            bucket[1] += row.hard_case_count
        return {k: adoption_rate(v[0], v[1]) for k, v in buckets.items()}

    @property
    def verified_count(self) -> int:
        return sum(1 for row in self.rows if row.verified)

    @property
    def inconsistent_rows(self) -> tuple[HardCaseAdoption, ...]:
        """列里存的采纳率与重算值对不上的行（物化口径对账）。"""
        return tuple(row for row in self.rows if not row.rate_matches)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hard_case_count": self.hard_case_count,
            "adopted_count": self.adopted_count,
            "adoption_rate": self.adoption_rate,
            "by_category": self.by_category,
            "verified_count": self.verified_count,
            "inconsistent_row_count": len(self.inconsistent_rows),
            "rows": [row.as_dict() for row in self.rows],
        }


# ===========================================================================
# 六、服务基类
# ===========================================================================


@dataclass(slots=True)
class _AdsService:
    """六项业务服务的共同底座：一个查询服务 + 几个取数口径。

    Args:
        ads: ADS 查询服务（:class:`query.AdsQueryService`）。
    """

    ads: AdsQueryService

    #: 该服务对应的闭环业务服务（子类覆盖）
    SERVICE: ClosedLoopService = ClosedLoopService.PRODUCTION_TRACKING

    @property
    def tables(self) -> tuple[str, ...]:
        """本服务读取的 ADS 表——直接由 ``products.consumed_by`` 反查，不另抄一份。"""
        return tuple(p.table for p in products_by_service(self.SERVICE))

    # ---- 取数 ----

    def _latest(
        self,
        table: str,
        *,
        filters: Sequence[Filter] = (),
        columns: Sequence[str] = (),
        order_by: Sequence[tuple[str, str]] = (),
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
        stat_date: Any = None,
    ) -> list[Row]:
        """取「最新一天」的一屏数据（六项服务里最常用的取数形态）。"""
        self._assert_owns(table)
        return self.ads.latest_snapshot(
            table,
            filters=filters,
            columns=columns,
            order_by=order_by,
            limit=limit,
            stat_date=stat_date,
        )

    def _fetch_all(
        self,
        table: str,
        *,
        filters: Sequence[Filter] = (),
        columns: Sequence[str] = (),
    ) -> list[Row]:
        """翻页取全量——聚合类接口专用。

        单页 LIMIT 会把聚合悄悄算少（一周的热力图可能几千格），所以这里按
        :data:`constants.ADS_QUERY_MAX_LIMIT` 翻页，直到取完；总行数超过
        :data:`constants.ADS_AGGREGATION_MAX_ROWS` 时**报错而不是截断**——
        少算的总量会直接污染 OTA 放行结论，宁可让调用方缩小窗口。

        排序键固定取该表的主键，保证翻页稳定（无序翻页会重复或漏行）。
        """
        self._assert_owns(table)
        product = get_product(table)
        order_by = tuple((col, "ASC") for col in product.key_columns)
        page = ADS_QUERY_MAX_LIMIT
        offset = 0
        out: list[Row] = []
        while True:
            rows = self.ads.fetch_rows(
                AdsQuery(
                    table,
                    columns=tuple(columns),
                    filters=tuple(filters),
                    order_by=order_by,
                    limit=page,
                    offset=offset,
                )
            )
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page
            if offset >= ADS_AGGREGATION_MAX_ROWS:
                raise AdsQueryError(
                    f"{table} 的聚合窗口超过 {ADS_AGGREGATION_MAX_ROWS} 行上限："
                    f"请缩小日期窗口或加上项目/类型过滤后重试（截断会把聚合值算少）"
                )

    def _assert_owns(self, table: str) -> None:
        """越权护栏：一项服务只许读它在产品矩阵里登记的表。"""
        if table not in self.tables:
            raise AdsQueryError(
                f"{type(self).__name__} 不读 {table!r}："
                f"该服务在产品矩阵里登记的表是 {', '.join(self.tables) or '（无）'}"
            )


# ===========================================================================
# 七、六项闭环业务服务
# ===========================================================================


@dataclass(slots=True)
class ProductionTrackingService(_AdsService):
    """🚦 数据生产追踪：这批数据到哪一步了？哪个环节最慢？

    两级下钻（[S1-05] 第三章）：大盘看「闭环慢不慢」（表 1），
    瓶颈表定位「慢在哪个环节」（表 2）。

    ⚠️ 原文代表 API 是 ``production/batch/{batch_id}/progress``（批次粒度），
    但 ADS 两张表的粒度是「统计日期 × 项目（× 环节）」，没有批次维度
    （表 2 只有 ``affected_batch_count`` 这个计数）。批次级进度要回
    ``dwd_data_production_chain`` 逐条查，属明细层出口，不在 ADS 服务层——
    见 :data:`DELEGATED_APIS`。本服务提供项目级的等价回答。
    """

    SERVICE: ClosedLoopService = ClosedLoopService.PRODUCTION_TRACKING

    def closed_loop_overview(
        self, *, project_code: str | None = None, stat_date: Any = None
    ) -> list[Row]:
        """闭环大盘（表 1）：总量、平均闭环耗时、Badcase 解决率、各状态分布。"""
        filters = [Filter("project_code", "=", project_code)] if project_code else []
        return self._latest("ads_closed_loop_dashboard", filters=filters, stat_date=stat_date)

    def bottleneck_ranking(
        self,
        *,
        project_code: str | None = None,
        stat_date: Any = None,
        top_n: int = 4,
    ) -> list[Row]:
        """产线瓶颈（表 2）：按瓶颈排名返回各环节耗时、积压与吞吐。

        Args:
            top_n: 取前几名。默认 4——原文的产线环节就是
                :data:`constants.PRODUCTION_STAGE_NAMES` 四段（上云/前处理/标注/质检）。
        """
        filters = [Filter("project_code", "=", project_code)] if project_code else []
        return self._latest(
            "ads_production_bottleneck_analysis",
            filters=filters,
            order_by=(("bottleneck_rank", "ASC"),),
            limit=max(1, top_n),
            stat_date=stat_date,
        )

    def progress(self, *, project_code: str, stat_date: Any = None) -> dict[str, Any]:
        """项目级进度：大盘 + 瓶颈环节 + 积压，一次问清「到哪一步了、谁最慢」。

        零 JOIN：两张表各查一次，在内存里拼——不下推跨表 JOIN。
        """
        overview = self.closed_loop_overview(project_code=project_code, stat_date=stat_date)
        stages = self.bottleneck_ranking(project_code=project_code, stat_date=stat_date)
        blocked = sum(_as_int(s.get("blocked_over_48h_count")) or 0 for s in stages)
        return {
            "project_code": project_code,
            "overview": overview[0] if overview else None,
            "stages": stages,
            "bottleneck_stage": stages[0].get("stage_name") if stages else None,
            f"blocked_over_{PRODUCTION_BLOCKED_ALERT_HOURS}h_count": blocked,
            "blocked_threshold_hours": PRODUCTION_BLOCKED_ALERT_HOURS,
        }


@dataclass(slots=True)
class SceneSearchCurationService(_AdsService):
    """🎯 场景检索与样本圈选：缺雨天数据，多久能从库里圈出来？

    读场景库汇总表（表 4）：总量、高质量量、关联 Badcase、覆盖度与缺口状态机
    （:data:`constants.SCENE_COVERAGE_STATUS_FLOW`，GAP → FILLING → COVERED）。

    Args:
        semantic_port: 语义检索端口（vector 子系统）。不注入时
            :meth:`semantic_search` 抛 :class:`errors.ServiceUnavailableError`，
            与 :func:`routing.route_for` 对向量路径的处理一致。
    """

    SERVICE: ClosedLoopService = ClosedLoopService.SCENE_SEARCH_CURATION
    semantic_port: SemanticSearchPort | None = None

    def search(
        self,
        *,
        scene_type: str | None = None,
        keyword: str | None = None,
        coverage_status: str | None = None,
        stat_date: Any = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> list[Row]:
        """标量检索场景库：按场景类型 / 标签名关键词 / 覆盖状态圈选。

        Raises:
            AdsQueryError: ``coverage_status`` 不在状态机三态内。
        """
        filters: list[Filter] = []
        if scene_type:
            filters.append(Filter("scene_type", "=", scene_type))
        if keyword:
            filters.append(Filter("tag_name", "LIKE", f"%{keyword}%"))
        if coverage_status:
            if coverage_status not in SCENE_COVERAGE_STATUS_FLOW:
                raise AdsQueryError(
                    f"覆盖状态 {coverage_status!r} 不合法；"
                    f"可选：{' → '.join(SCENE_COVERAGE_STATUS_FLOW)}"
                )
            filters.append(Filter("coverage_status", "=", coverage_status))
        return self._latest(
            "ads_scene_library_summary",
            filters=filters,
            order_by=(("total_data_count", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )

    def gap_list(self, *, stat_date: Any = None, limit: int = ADS_QUERY_DEFAULT_LIMIT) -> list[Row]:
        """场景缺口清单：还没达标的标签，按缺口量从大到小。

        原文案例：盘点发现「施工区域」仅 150 条（达标线 2,000 条）且 Badcase 逐月上升
        → 生成场景缺口清单，定向补采两周后达标、状态流转 COVERED（[S1-05] 第四章 2）。
        """
        return self._latest(
            "ads_scene_library_summary",
            filters=[Filter("coverage_status", "!=", SCENE_COVERAGE_STATUS_FLOW[-1])],
            order_by=(("gap_count", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )

    def curate(
        self, *, scene_type: str | None = None, min_high_quality_rate: float = 0.0, **kwargs: Any
    ) -> list[Row]:
        """样本圈选：在检索结果里只留高质量占比达标的标签。"""
        rows = self.search(scene_type=scene_type, **kwargs)
        return [
            r
            for r in rows
            if (_as_float(r.get("high_quality_rate")) or 0.0) >= min_high_quality_rate
        ]

    def semantic_search(self, text: str, *, top_k: int = 100) -> list[str]:
        """「以文搜图」：一句话捞出一批同类场景的 data_id。

        Raises:
            ServiceUnavailableError: 没注入向量子系统的检索端口。
        """
        if self.semantic_port is None:
            raise ServiceUnavailableError(
                "语义检索属 vector 子系统（HNSW 索引建在 StarRocks 外部表上，"
                "见 routing.route_for 对 SEMANTIC_RETRIEVAL 的说明）："
                "请在构造 SceneSearchCurationService 时注入 semantic_port"
            )
        return self.semantic_port.search(text, top_k=top_k)


@dataclass(slots=True)
class DatasetVersionDeliveryService(_AdsService):
    """📦 数据集版本与交付：V3 的数据到底从哪来？谁用了它？

    读数据资产目录（表 6）与存储成本看板（表 10）——资产交付与存储账单同属
    资产运营视角（这一归类是本项目的，见 ``products`` 表 10 的 notes）。
    """

    SERVICE: ClosedLoopService = ClosedLoopService.DATASET_VERSION_DELIVERY

    def asset_catalog(
        self,
        *,
        asset_type: str | None = None,
        owner: str | None = None,
        stat_date: Any = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> list[Row]:
        """资产目录（表 6）：按「资产类型 × 负责人」查注册数、使用次数与质量评分。"""
        filters: list[Filter] = []
        if asset_type:
            filters.append(Filter("asset_type", "=", asset_type))
        if owner:
            filters.append(Filter("owner", "=", owner))
        return self._latest(
            "ads_data_asset_catalog",
            filters=filters,
            order_by=(("ref_count_90d", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )

    def composition(self, *, dataset_id: str, stat_date: Any = None) -> dict[str, Any]:
        """某数据集各版本的构成与热度（原文代表 API ``dataset/.../composition``）。"""
        rows = self._latest(
            "ads_data_asset_catalog",
            filters=[Filter("dataset_id", "=", dataset_id)],
            order_by=(("dataset_version", "DESC"),),
            stat_date=stat_date,
        )
        return {
            "dataset_id": dataset_id,
            "version_count": len(rows),
            "versions": rows,
            "total_ref_count": sum(_as_int(r.get("ref_count")) or 0 for r in rows),
            "quality_score_max": ASSET_QUALITY_SCORE_MAX,
        }

    def archive_candidates(
        self, *, stat_date: Any = None, limit: int = ADS_QUERY_DEFAULT_LIMIT
    ) -> list[Row]:
        """归档建议清单：低分 + 近 90 天零引用的老旧资产。

        原文案例：「3 个低分老旧数据集长期无人使用 → 标记归档释放存储」
        （[S1-05] 第六章表 6）。分数线与「长期」的天数线原文没给，
        本项目取 :data:`constants.ASSET_LOW_QUALITY_SCORE_THRESHOLD` 与
        :data:`constants.ASSET_IDLE_DAYS_THRESHOLD`。
        """
        rows = self._latest(
            "ads_data_asset_catalog",
            filters=[Filter("ref_count_90d", "<=", 0)],
            order_by=(("quality_score", "ASC"),),
            limit=limit,
            stat_date=stat_date,
        )
        return [
            r
            for r in rows
            if (_as_float(r.get("quality_score")) or 0.0) < ASSET_LOW_QUALITY_SCORE_THRESHOLD
        ]

    @property
    def idle_days_threshold(self) -> int:
        """「长期无人使用」的天数线（本项目设计，见 constants）。"""
        return ASSET_IDLE_DAYS_THRESHOLD

    def storage_cost(
        self,
        *,
        storage_media: str | None = None,
        lifecycle_stage: str | None = None,
        stat_date: Any = None,
    ) -> list[Row]:
        """存储成本看板（表 10）：按「存储介质 × 生命周期分层 × 数据类型」看容量、成本与节省额。"""
        filters: list[Filter] = []
        if storage_media:
            filters.append(Filter("storage_media", "=", storage_media))
        if lifecycle_stage:
            filters.append(Filter("lifecycle_stage", "=", lifecycle_stage))
        return self._latest(
            "ads_storage_cost_dashboard",
            filters=filters,
            order_by=(("month_cost_yuan", "DESC"),),
            stat_date=stat_date,
        )

    def cost_alerts(self, *, stat_date: Any = None) -> list[Row]:
        """成本告警行：成本环比增长超 10% 自动告警（[S1-全景] 第八章③）。"""
        rows = self.storage_cost(stat_date=stat_date)
        return [
            r
            for r in rows
            if (_as_float(r.get("cost_mom_rate")) or 0.0) > STORAGE_COST_MOM_ALERT_THRESHOLD
            or _as_bool(r.get("budget_alert_flag")) is True
        ]


@dataclass(slots=True)
class ModelIterationEvaluationService(_AdsService):
    """🔁 模型迭代评测：效果回退是数据问题还是模型问题？外加 OTA 放行门。

    读版本对比（表 7）、Badcase 根因分布（表 3）与 OTA 部署汇总（表 8）。

    Args:
        trigger_service: 回传闭环服务。OTA 放行的第二条判据（发布后一周回传
            触发量环比增长）要读热力图表，那张表归 🔄 回传与挖掘闭环服务管——
            这里注入而不是自己越权去读，:meth:`_assert_owns` 会挡住越权。
        safety_issue_port: 安全问题计数端口（见 :class:`SafetyIssuePort`）。
    """

    SERVICE: ClosedLoopService = ClosedLoopService.MODEL_ITERATION_EVALUATION
    trigger_service: TriggerMiningClosedLoopService | None = None
    safety_issue_port: SafetyIssuePort | None = None

    # ---- 表 7 / 表 3 ----

    def compare(
        self,
        *,
        model_version: str,
        baseline_model_version: str | None = None,
        dataset_id: str | None = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> list[Row]:
        """模型版本对比（表 7）：按「模型版本 × 数据集 × 场景类型」取通过率与基线差值。"""
        self._assert_owns("ads_model_version_comparison")
        filters = [Filter("model_version", "=", model_version)]
        if baseline_model_version:
            filters.append(Filter("baseline_model_version", "=", baseline_model_version))
        if dataset_id:
            filters.append(Filter("dataset_id", "=", dataset_id))
        return self.ads.fetch_rows(
            AdsQuery(
                "ads_model_version_comparison",
                filters=tuple(filters),
                order_by=(("pass_rate_diff_pp", "DESC"),),
                limit=limit,
            )
        )

    def regression_check(
        self,
        *,
        model_version: str,
        baseline_model_version: str | None = None,
        dataset_id: str | None = None,
        tolerance_pp: float = MODEL_REGRESSION_TOLERANCE_PP_DEFAULT,
    ) -> RegressionCheck | None:
        """回归项检查：有没有「带病」的场景。没有评测数据时返回 ``None``。"""
        rows = self.compare(
            model_version=model_version,
            baseline_model_version=baseline_model_version,
            dataset_id=dataset_id,
        )
        return RegressionCheck.from_rows(rows, tolerance_pp=tolerance_pp)

    def badcase_root_cause(
        self,
        *,
        model_version: str | None = None,
        project_code: str | None = None,
        stat_date: Any = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> list[Row]:
        """Badcase 根因分布（表 3）：按根因分类/子分类看数量、占比与趋势。"""
        filters: list[Filter] = []
        if model_version:
            filters.append(Filter("model_version", "=", model_version))
        if project_code:
            filters.append(Filter("project_code", "=", project_code))
        return self._latest(
            "ads_badcase_root_cause_distribution",
            filters=filters,
            order_by=(("badcase_count", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )

    # ---- 表 8 ----

    def ota_deployment(self, *, ota_task_id: str) -> Row | None:
        """按任务 ID 取 OTA 部署汇总（表 8 主键就是 ``ota_task_id``，零 JOIN 一次命中）。"""
        self._assert_owns("ads_ota_deployment_summary")
        return self.ads.fetch_one(
            AdsQuery(
                "ads_ota_deployment_summary",
                filters=(Filter("ota_task_id", "=", ota_task_id),),
            )
        )

    def ota_release_gate(
        self,
        *,
        ota_task_id: str,
        safety_issue_count: int | None = None,
        post_release_trigger_growth_rate: float | None = None,
        publish_date: Any = None,
        check_regression: bool = True,
        tolerance_pp: float = MODEL_REGRESSION_TOLERANCE_PP_DEFAULT,
    ) -> OtaGateDecision:
        """OTA 灰度三条件放行：成功率 99.2% / 触发量环比 +30% / 安全问题 0 起。

        三条件是与关系，缺一不可（[S1-05] 第五章表 8）。判据取数：

          ① 升级成功率 ← ``ads_ota_deployment_summary.deploy_success_rate``
          ② 触发量环比 ← 调用方直接给，或由 :meth:`TriggerMiningClosedLoopService
             .post_release_trigger_growth` 按发布日前后各一周在 ``ads_trigger_heatmap``
             上算（⚠️ 表 8 没有「发布后回传触发量」这一列，见 :data:`CATALOG_GAPS`）
          ③ 安全相关问题数 ← 调用方直接给，或由 ``safety_issue_port`` 提供
             （⚠️ 表 8 同样没有这一列）

        Args:
            ota_task_id: OTA 任务 ID。
            safety_issue_count: 安全相关问题数。不传且没注入端口时，该条判**不通过**。
            post_release_trigger_growth_rate: 直接给定的触发量环比；不传则自动算。
            publish_date: 发布日；不传则取表 8 的 ``publish_time``。
            check_regression: 是否加挂表 7 的回归项判据（默认加挂，
                对应「仿真评测必须先于 OTA 部署」的硬约束）。
            tolerance_pp: 回归容差。

        Returns:
            :class:`OtaGateDecision`。

        Raises:
            AdsQueryError: 该 OTA 任务不在 ``ads_ota_deployment_summary`` 里。
        """
        row = self.ota_deployment(ota_task_id=ota_task_id)
        if row is None:
            raise AdsQueryError(
                f"OTA 任务 {ota_task_id!r} 不在 ads_ota_deployment_summary 里："
                f"T+1 物化还没跑到，或任务 ID 写错了"
            )

        software_version = str(row.get("software_version") or "")
        project_code = str(row.get("project_code") or "") or None
        release_channel = str(row.get("release_channel") or "")

        publish_day: date | None
        try:
            publish_day = _as_date(
                publish_date if publish_date is not None else row.get("publish_time"),
                field_name="publish_time",
            )
        except AdsQueryError:
            publish_day = None

        growth = post_release_trigger_growth_rate
        if growth is None and publish_day is not None and self.trigger_service is not None:
            growth = self.trigger_service.post_release_trigger_growth(
                publish_date=publish_day, project_code=project_code
            ).rate

        safety = safety_issue_count
        if safety is None and self.safety_issue_port is not None and publish_day is not None:
            safety = self.safety_issue_port.safety_issue_count(
                software_version=software_version,
                since=publish_day,
                until=publish_day + timedelta(days=OTA_POST_RELEASE_OBSERVE_DAYS - 1),
                project_code=project_code,
            )

        regression = None
        model_version = str(row.get("model_version") or "")
        if check_regression and model_version:
            regression = self.regression_check(
                model_version=model_version, tolerance_pp=tolerance_pp
            )

        return evaluate_ota_release_gate(
            ota_task_id=ota_task_id,
            deploy_success_rate=_as_float(row.get("deploy_success_rate")),
            post_release_trigger_growth_rate=growth,
            safety_issue_count=safety,
            software_version=software_version,
            release_channel=release_channel,
            regression=regression,
            require_evaluation=check_regression,
            tolerance_pp=tolerance_pp,
        )


@dataclass(slots=True)
class TriggerMiningClosedLoopService(_AdsService):
    """🔄 回传与挖掘闭环：触发到进训练集多久？缺口补上了吗？

    读热力图（表 9）、难例库（表 5）、场景库（表 4）与挖掘标签看板（表 11）。
    热力图的网格与热力等级口径来自 :mod:`geo`，与 Flink 批作业同源。
    """

    SERVICE: ClosedLoopService = ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP

    # ---- 表 9：地理网格热力图 ----

    def heatmap(
        self,
        *,
        stat_date: Any = None,
        project_code: str | None = None,
        trigger_type: str | None = None,
        min_heat_level: int | None = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> list[HeatCell]:
        """取一天的热力图格子（大屏地图直接渲染，零 JOIN）。

        Args:
            stat_date: 统计日期；不传取已物化的最新一天。
            project_code: 项目过滤。
            trigger_type: 触发类型过滤（如「AEB 误触发」）。
            min_heat_level: 只要热力等级不低于该档的格子（1~5）。
            limit: 行数上限。

        Returns:
            :class:`HeatCell` 列表，按触发次数从多到少。

        Raises:
            AdsQueryError: ``min_heat_level`` 不在
                :data:`constants.TRIGGER_HEAT_LEVEL_MIN` ~
                :data:`constants.TRIGGER_HEAT_LEVEL_MAX` 之内。
                档外的值会**静默返回空列表**（传 9）或**静默返回全量**（传 0），
                大屏两种都看不出问题，所以这里报错而不是照单全收。
        """
        filters: list[Filter] = []
        if project_code:
            filters.append(Filter("project_code", "=", project_code))
        if trigger_type:
            filters.append(Filter("trigger_type", "=", trigger_type))
        if min_heat_level is not None:
            if not TRIGGER_HEAT_LEVEL_MIN <= min_heat_level <= TRIGGER_HEAT_LEVEL_MAX:
                raise AdsQueryError(
                    f"热力等级 {min_heat_level} 越界："
                    f"ads_trigger_heatmap.heat_level 只有 "
                    f"{TRIGGER_HEAT_LEVEL_MIN}~{TRIGGER_HEAT_LEVEL_MAX} 五档"
                )
            filters.append(Filter("heat_level", ">=", min_heat_level))
        rows = self._latest(
            "ads_trigger_heatmap",
            filters=filters,
            order_by=(("trigger_cnt", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )
        return [HeatCell.from_row(r) for r in rows]

    @staticmethod
    def grid_rollup(cells: Iterable[HeatCell]) -> list[GridHotspot]:
        """把同一网格上的各类触发合并成一个热点色块。

        聚合口径：触发次数、车辆数、难例数逐项求和；热力等级按**合并后的总量**
        用 :func:`geo.heat_level` 重算（不是把各类型的等级相加）；
        代表触发类型取该网格里次数最多的那一类。
        """
        buckets: dict[str, list[HeatCell]] = {}
        for cell in cells:
            buckets.setdefault(cell.geo_grid_id, []).append(cell)

        hotspots: list[GridHotspot] = []
        for grid_id_value, group in buckets.items():
            total = sum(c.trigger_cnt for c in group)
            top = max(group, key=lambda c: c.trigger_cnt)
            hotspots.append(
                GridHotspot(
                    geo_grid_id=grid_id_value,
                    trigger_cnt=total,
                    vehicle_cnt=sum(c.vehicle_cnt for c in group),
                    hard_case_cnt=sum(c.hard_case_cnt for c in group),
                    heat_level=heat_level(total),
                    trigger_types=tuple(sorted({c.trigger_type for c in group if c.trigger_type})),
                    top_trigger_type=top.trigger_type,
                    top_scene_tag=top.top_scene_tag,
                    city_name=top.city_name,
                    road_type=top.road_type,
                    sample_data_id=top.sample_data_id,
                    center_lat=top.grid_center_lat,
                    center_lon=top.grid_center_lon,
                )
            )
        hotspots.sort(key=lambda h: (-h.trigger_cnt, h.geo_grid_id))
        return hotspots

    def top_hotspots(
        self,
        *,
        stat_date: Any = None,
        project_code: str | None = None,
        top_n: int = 10,
    ) -> list[GridHotspot]:
        """热力最高的若干网格——回答「触发集中在哪」（原文案例：城区晚高峰路口）。"""
        cells = self.heatmap(
            stat_date=stat_date, project_code=project_code, limit=ADS_QUERY_MAX_LIMIT
        )
        return self.grid_rollup(cells)[: max(1, top_n)]

    def trigger_volume(
        self,
        *,
        start: date,
        end: date,
        project_code: str | None = None,
        trigger_type: str | None = None,
    ) -> int:
        """某个日期窗口内的触发总量（闭区间，按网格表全量翻页求和）。

        Raises:
            AdsQueryError: 窗口起止颠倒。
        """
        if end < start:
            raise AdsQueryError(f"日期窗口颠倒：start={start} 晚于 end={end}")
        filters: list[Filter] = [Filter("stat_date", "BETWEEN", (start, end))]
        if project_code:
            filters.append(Filter("project_code", "=", project_code))
        if trigger_type:
            filters.append(Filter("trigger_type", "=", trigger_type))
        rows = self._fetch_all("ads_trigger_heatmap", filters=filters)
        return sum(_as_int(r.get("trigger_cnt")) or 0 for r in rows)

    def post_release_trigger_growth(
        self,
        *,
        publish_date: Any,
        project_code: str | None = None,
        window_days: int = OTA_POST_RELEASE_OBSERVE_DAYS,
    ) -> TriggerGrowth:
        """发布后一周 vs 发布前一周的回传触发量对比（OTA 三条件之②的实测值）。

        窗口口径（[S1-05] 第五章表 8「发布后一周回传触发量环比增长 30%」）：
        本期 = [发布日, 发布日 + window_days − 1]，上期 = 紧邻的前 window_days 天。

        Args:
            publish_date: 发布日。
            project_code: 项目过滤。
            window_days: 观察窗口天数，默认
                :data:`constants.OTA_POST_RELEASE_OBSERVE_DAYS`（7 天）。

        Raises:
            AdsQueryError: 窗口天数非正。
        """
        if window_days <= 0:
            raise AdsQueryError(f"观察窗口必须为正天数，收到 {window_days}")
        day = _as_date(publish_date, field_name="publish_date")
        current = (day, day + timedelta(days=window_days - 1))
        previous = (day - timedelta(days=window_days), day - timedelta(days=1))
        return TriggerGrowth(
            current_window=current,
            previous_window=previous,
            current_count=self.trigger_volume(
                start=current[0], end=current[1], project_code=project_code
            ),
            previous_count=self.trigger_volume(
                start=previous[0], end=previous[1], project_code=project_code
            ),
            window_days=window_days,
        )

    def wow_anomalies(
        self,
        *,
        stat_date: Any,
        project_code: str | None = None,
        trigger_type: str | None = None,
        threshold: float = TRIGGER_WOW_ANOMALY_THRESHOLD,
        window_days: int = TRIGGER_WOW_WINDOW_DAYS,
        include_new_hotspots: bool = True,
    ) -> list[WowAnomaly]:
        """周环比异常网格清单——原文案例「某区域『AEB 误触发』周环比上升 45%」的落点。

        口径（⚠️ 原文只给了 45% 这个结果值，没给窗口与告警线）：
        本周 = [stat_date − window_days + 1, stat_date]，上周 = 紧邻的前 window_days 天；
        按「网格 × 触发类型」分组比总量，涨幅 ≥ ``threshold``
        （默认 :data:`constants.TRIGGER_WOW_ANOMALY_THRESHOLD` = 30%）即进清单，
        45% 的案例必然被捞出来。上周为 0、本周有触发的格子算「新增热点」单列。

        Raises:
            AdsQueryError: 窗口天数非正。
        """
        if window_days <= 0:
            raise AdsQueryError(f"周环比窗口必须为正天数，收到 {window_days}")
        day = _as_date(stat_date, field_name="stat_date")
        cur_start = day - timedelta(days=window_days - 1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=window_days - 1)

        current = self._grouped_counts(
            start=cur_start, end=day, project_code=project_code, trigger_type=trigger_type
        )
        previous = self._grouped_counts(
            start=prev_start, end=prev_end, project_code=project_code, trigger_type=trigger_type
        )

        out: list[WowAnomaly] = []
        for key, (count, sample) in current.items():
            prev_count = previous.get(key, (0, {}))[0]
            rate = growth_rate(count, prev_count)
            is_new = prev_count == 0 and count > 0
            if is_new:
                if not include_new_hotspots:
                    continue
            elif rate is None or rate < threshold:
                continue
            out.append(
                WowAnomaly(
                    geo_grid_id=key[0],
                    trigger_type=key[1],
                    current_count=count,
                    previous_count=prev_count,
                    wow_rate=rate,
                    threshold=threshold,
                    is_new_hotspot=is_new,
                    city_name=str(sample.get("city_name") or ""),
                    road_type=str(sample.get("road_type") or ""),
                    top_scene_tag=str(sample.get("top_scene_tag") or ""),
                    sample_data_id=str(sample.get("sample_data_id") or ""),
                )
            )
        out.sort(
            key=lambda a: (
                -(a.wow_rate if a.wow_rate is not None else float("inf")),
                -a.current_count,
                a.geo_grid_id,
            )
        )
        return out

    def _grouped_counts(
        self,
        *,
        start: date,
        end: date,
        project_code: str | None,
        trigger_type: str | None,
    ) -> dict[tuple[str, str], tuple[int, Row]]:
        """窗口内按「网格 × 触发类型」聚合触发量，并留一行样本供下钻。"""
        filters: list[Filter] = [Filter("stat_date", "BETWEEN", (start, end))]
        if project_code:
            filters.append(Filter("project_code", "=", project_code))
        if trigger_type:
            filters.append(Filter("trigger_type", "=", trigger_type))
        grouped: dict[tuple[str, str], tuple[int, Row]] = {}
        for row in self._fetch_all("ads_trigger_heatmap", filters=filters):
            key = (str(row.get("geo_grid_id", "")), str(row.get("trigger_type", "")))
            count, sample = grouped.get(key, (0, row))
            grouped[key] = (count + (_as_int(row.get("trigger_cnt")) or 0), sample)
        return grouped

    # ---- 表 5：难例采纳率 ----

    def hard_case_adoption(
        self,
        *,
        stat_date: Any = None,
        model_version: str | None = None,
        source_type: str | None = None,
        hard_case_category: str | None = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> list[HardCaseAdoption]:
        """难例库（表 5）：按「难例类别 × 来源 × 模型版本」取数量、采纳率与闭环效果。"""
        filters: list[Filter] = []
        if model_version:
            filters.append(Filter("model_version", "=", model_version))
        if source_type:
            filters.append(Filter("source_type", "=", source_type))
        if hard_case_category:
            filters.append(Filter("hard_case_category", "=", hard_case_category))
        rows = self._latest(
            "ads_hard_case_library",
            filters=filters,
            order_by=(("hard_case_count", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )
        return [HardCaseAdoption.from_row(r) for r in rows]

    def hard_case_adoption_summary(self, **kwargs: Any) -> HardCaseAdoptionSummary:
        """难例采纳率汇总：整体采纳率、按类别拆分、闭环验证条数与口径对账结果。

        原文案例：v3.2 挖出 3,200 条难例，采纳 2,800 条 → 87.5%（[S1-05] 第四章 3）。
        """
        rows = self.hard_case_adoption(**kwargs)
        return HardCaseAdoptionSummary(
            rows=tuple(rows),
            hard_case_count=sum(r.hard_case_count for r in rows),
            adopted_count=sum(r.adopted_count for r in rows),
        )

    def closed_loop_status(self, *, trigger_type: str, stat_date: Any = None) -> dict[str, Any]:
        """某类触发的闭环状态：触发量、沉淀难例数与采纳情况（代表 API ``trigger/.../closed-loop``）。

        触发量与难例数都是**聚合值**，因此走 :meth:`_AdsService._fetch_all` 翻页取全，
        不能用单页 ``limit``——单页截断会把总量算少，而少算的总量恰恰会顺着
        :meth:`ModelIterationEvaluationService.ota_release_gate` 的条件②
        污染 OTA 放行结论（本模块开头第 3 条纪律）。

        Args:
            trigger_type: 触发类型，如「AEB 误触发」。
            stat_date: 统计日期；不传取已物化的最新一天。

        Returns:
            触发量 / 网格数 / 难例数 / 采纳率 / 闭环验证条数。
            该表还没物化出任何数据时，计数全为 0、采纳率为 ``None``。
        """
        day = (
            stat_date if stat_date is not None else self.ads.latest_stat_date("ads_trigger_heatmap")
        )
        cells: list[HeatCell] = []
        if day is not None:
            rows = self._fetch_all(
                "ads_trigger_heatmap",
                filters=[
                    Filter("stat_date", "=", day),
                    Filter("trigger_type", "=", trigger_type),
                ],
            )
            cells = [HeatCell.from_row(r) for r in rows]
        summary = self.hard_case_adoption_summary(stat_date=stat_date)
        return {
            "trigger_type": trigger_type,
            "stat_date": day,
            "trigger_cnt": sum(c.trigger_cnt for c in cells),
            "grid_cnt": len({c.geo_grid_id for c in cells}),
            "hard_case_cnt": sum(c.hard_case_cnt for c in cells),
            "adoption_rate": summary.adoption_rate,
            "adopted_count": summary.adopted_count,
            "verified_count": summary.verified_count,
        }

    # ---- 表 4 / 表 11 ----

    def scene_gap_status(self, *, stat_date: Any = None) -> dict[str, Any]:
        """场景缺口状态（代表 API ``scene-gap/status``）：三态各有多少标签。

        原文口径：场景库定义 1,200 个标签、覆盖度 82%（[S1-05] 第四章 2）。
        三态计数与缺口合计都是**聚合值**，因此翻页取全而不是单页截断——
        与 :meth:`closed_loop_status` 同理，少算的标签数会让「缺口补上了吗」答错。

        Args:
            stat_date: 统计日期；不传取已物化的最新一天。

        Returns:
            ``status_flow`` 三态顺序、每态标签数、标签总数与缺口合计；
            未物化时计数全为 0。
        """
        day = (
            stat_date
            if stat_date is not None
            else self.ads.latest_stat_date("ads_scene_library_summary")
        )
        rows: list[Row] = []
        if day is not None:
            rows = self._fetch_all(
                "ads_scene_library_summary", filters=[Filter("stat_date", "=", day)]
            )
        counts = dict.fromkeys(SCENE_COVERAGE_STATUS_FLOW, 0)
        for row in rows:
            status = str(row.get("coverage_status") or "")
            if status in counts:
                counts[status] += 1
        return {
            "status_flow": list(SCENE_COVERAGE_STATUS_FLOW),
            "counts": counts,
            "tag_count": len(rows),
            "gap_total": sum(_as_int(r.get("gap_count")) or 0 for r in rows),
        }

    def mining_tag_dashboard(
        self,
        *,
        project_code: str | None = None,
        tag_category: str | None = None,
        stat_date: Any = None,
        limit: int = ADS_QUERY_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """挖掘标签分布看板（表 11）：三来源构成、clip 覆盖率与候选池健康度。"""
        filters: list[Filter] = []
        if project_code:
            filters.append(Filter("project_code", "=", project_code))
        if tag_category:
            filters.append(Filter("tag_category", "=", tag_category))
        rows = self._latest(
            "ads_mining_tag_dashboard",
            filters=filters,
            order_by=(("data_count", "DESC"),),
            limit=limit,
            stat_date=stat_date,
        )
        by_source = {
            source: sum(_as_int(r.get(f"{source}_source_count")) or 0 for r in rows)
            for source in MINING_TAG_SOURCES
        }
        coverages = [_as_float(r.get("coverage_ratio")) for r in rows]
        known = [c for c in coverages if c is not None]
        avg_coverage = sum(known) / len(known) if known else None
        return {
            "tag_count": len(rows),
            "by_source": by_source,
            "source_names": list(MINING_TAG_SOURCES),
            "avg_coverage_ratio": avg_coverage,
            "coverage_warn_threshold": MINING_TAG_COVERAGE_WARN_THRESHOLD,
            "coverage_warning": avg_coverage is not None
            and avg_coverage < MINING_TAG_COVERAGE_WARN_THRESHOLD,
            "pending_review_warn_count": MINING_TAG_PENDING_REVIEW_WARN_COUNT,
            "rows": rows,
        }


@dataclass(slots=True)
class LineageTraceService(_AdsService):
    """🧬 全链路血缘追溯：Badcase 数据从哪来？问题数据影响了哪些模型？

    图库找关系、湖仓取明细（[S1-全景] 第八章②）：多跳遍历本身属 lineage 子系统，
    本服务只做两件属于 ADS 的事——

      1. 提供**追溯锚点**：从产线瓶颈表与热力图表里取代表性样本 ``sample_data_id``，
         让「点热点即下钻到原始片段」有起点；
      2. 转发到 :class:`LineagePort`，并校验方向与跳数在原文给的范围内。

    产品矩阵里没有任何一张 ADS 表登记 ``consumed_by=LINEAGE_TRACE``（血缘的事实
    在图库与 DWD 明细里），所以本服务的 :attr:`tables` 为空，锚点表另行声明在
    :attr:`ANCHOR_TABLES`，并用 :meth:`_anchor_rows` 直连查询服务而不走 owns 护栏。
    """

    SERVICE: ClosedLoopService = ClosedLoopService.LINEAGE_TRACE
    lineage_port: LineagePort | None = None

    #: 可以提供追溯起点（``sample_data_id``）的 ADS 表
    ANCHOR_TABLES: tuple[str, ...] = (
        "ads_production_bottleneck_analysis",
        "ads_trigger_heatmap",
    )

    def trace_anchors(self, *, stat_date: Any = None, limit: int = 20) -> list[dict[str, Any]]:
        """取可用的追溯锚点：(data_id, 来源表, 上下文)。"""
        out: list[dict[str, Any]] = []
        for table in self.ANCHOR_TABLES:
            rows = self.ads.latest_snapshot(table, limit=limit, stat_date=stat_date)
            for row in rows:
                data_id = row.get("sample_data_id")
                if not data_id:
                    continue
                out.append(
                    {
                        "data_id": str(data_id),
                        "source_table": table,
                        "context": {
                            k: row.get(k) for k in get_product(table).key_columns if k in row
                        },
                    }
                )
        return out[:limit]

    def trace(
        self,
        *,
        data_id: str,
        direction: str = LINEAGE_QUERY_DIRECTIONS[0],
        depth: int = LINEAGE_MIN_TRAVERSAL_DEPTH,
    ) -> list[dict[str, Any]]:
        """业务血缘追溯（代表 API ``lineage/business/trace``）。

        Args:
            data_id: 锚点 ID。
            direction: 四个查询方向之一，见
                :data:`constants.LINEAGE_QUERY_DIRECTIONS`。
            depth: 遍历跳数，限定在
                :data:`constants.LINEAGE_MIN_TRAVERSAL_DEPTH` ~
                :data:`constants.LINEAGE_MAX_TRAVERSAL_DEPTH`（3~5 跳防扇出爆炸）。

        Raises:
            AdsQueryError: 方向非法或跳数越界。
            ServiceUnavailableError: 没注入图库端口。
        """
        if direction not in LINEAGE_QUERY_DIRECTIONS:
            raise AdsQueryError(
                f"血缘查询方向 {direction!r} 不合法；可选：{', '.join(LINEAGE_QUERY_DIRECTIONS)}"
            )
        if not LINEAGE_MIN_TRAVERSAL_DEPTH <= depth <= LINEAGE_MAX_TRAVERSAL_DEPTH:
            raise AdsQueryError(
                f"血缘遍历跳数 {depth} 越界：建议限定在 "
                f"{LINEAGE_MIN_TRAVERSAL_DEPTH}~{LINEAGE_MAX_TRAVERSAL_DEPTH} 跳防止扇出爆炸"
            )
        if self.lineage_port is None:
            raise ServiceUnavailableError(
                "血缘多跳遍历属 lineage 子系统（Neo4j 图库找关系、湖仓取明细）："
                "请在构造 LineageTraceService 时注入 lineage_port"
            )
        return self.lineage_port.trace(data_id, direction=direction, depth=depth)

    def impact(
        self, *, data_id: str, depth: int = LINEAGE_MIN_TRAVERSAL_DEPTH
    ) -> list[dict[str, Any]]:
        """影响分析（代表 API ``lineage/impact``）：这批数据影响了哪些数据集与模型。"""
        return self.trace(data_id=data_id, direction="impact_analysis", depth=depth)


# ===========================================================================
# 八、装配：接口清单 + 网关注册 + 出口自检
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ApiBinding:
    """一条业务接口：路径 → 哪项服务的哪个方法 → 读哪几张 ADS 表。"""

    path: str
    service: ClosedLoopService
    attr: str
    method: str
    tables: tuple[str, ...]
    summary_cn: str
    #: [S1-全景] 第九章的代表 API 原文（有对应关系时逐字写上）
    origin_api: str = ""


#: 全部业务接口。每张 ADS 表至少被一条接口覆盖，:func:`verify_service_exits` 守着这一点。
API_BINDINGS: Final[tuple[ApiBinding, ...]] = (
    # 🚦 数据生产追踪
    ApiBinding(
        "closed-loop/overview",
        ClosedLoopService.PRODUCTION_TRACKING,
        "production",
        "closed_loop_overview",
        ("ads_closed_loop_dashboard",),
        "闭环大盘：总量 / 平均闭环耗时 / Badcase 解决率",
    ),
    ApiBinding(
        "production/bottleneck",
        ClosedLoopService.PRODUCTION_TRACKING,
        "production",
        "bottleneck_ranking",
        ("ads_production_bottleneck_analysis",),
        "产线瓶颈排名：各环节耗时、积压与吞吐",
    ),
    ApiBinding(
        "production/project/{project_code}/progress",
        ClosedLoopService.PRODUCTION_TRACKING,
        "production",
        "progress",
        ("ads_closed_loop_dashboard", "ads_production_bottleneck_analysis"),
        "项目级进度：这批数据到哪一步了？哪个环节最慢？",
        origin_api="production/batch/.../progress",
    ),
    # 🎯 场景检索与样本圈选
    ApiBinding(
        "scene/search",
        ClosedLoopService.SCENE_SEARCH_CURATION,
        "scene",
        "search",
        ("ads_scene_library_summary",),
        "场景库检索：按类型 / 标签 / 覆盖状态圈选",
        origin_api="scene/search",
    ),
    ApiBinding(
        "scene/curate",
        ClosedLoopService.SCENE_SEARCH_CURATION,
        "scene",
        "curate",
        ("ads_scene_library_summary",),
        "样本圈选：只留高质量占比达标的标签",
        origin_api="scene/curate",
    ),
    ApiBinding(
        "scene/gap-list",
        ClosedLoopService.SCENE_SEARCH_CURATION,
        "scene",
        "gap_list",
        ("ads_scene_library_summary",),
        "场景缺口清单：未达标标签按缺口量排序",
    ),
    # 📦 数据集版本与交付
    ApiBinding(
        "dataset/{dataset_id}/composition",
        ClosedLoopService.DATASET_VERSION_DELIVERY,
        "dataset",
        "composition",
        ("ads_data_asset_catalog",),
        "数据集构成：某数据集各版本的数据量与引用热度",
        origin_api="dataset/.../composition",
    ),
    ApiBinding(
        "asset/catalog",
        ClosedLoopService.DATASET_VERSION_DELIVERY,
        "dataset",
        "asset_catalog",
        ("ads_data_asset_catalog",),
        "数据资产目录：注册数 / 使用次数 / 质量评分",
    ),
    ApiBinding(
        "asset/archive-candidates",
        ClosedLoopService.DATASET_VERSION_DELIVERY,
        "dataset",
        "archive_candidates",
        ("ads_data_asset_catalog",),
        "归档建议：低分且长期零引用的老旧资产",
    ),
    ApiBinding(
        "storage/cost",
        ClosedLoopService.DATASET_VERSION_DELIVERY,
        "dataset",
        "storage_cost",
        ("ads_storage_cost_dashboard",),
        "存储成本看板：容量 / 成本 / 治理动作量 / 节省额",
    ),
    ApiBinding(
        "storage/cost-alerts",
        ClosedLoopService.DATASET_VERSION_DELIVERY,
        "dataset",
        "cost_alerts",
        ("ads_storage_cost_dashboard",),
        "成本告警：成本环比增长超阈值的分层",
    ),
    # 🔁 模型迭代评测
    ApiBinding(
        "model/compare",
        ClosedLoopService.MODEL_ITERATION_EVALUATION,
        "model",
        "compare",
        ("ads_model_version_comparison",),
        "模型版本对比：通过率 / Badcase 率 / 与基线差值",
        origin_api="model/compare",
    ),
    ApiBinding(
        "badcase/root-cause",
        ClosedLoopService.MODEL_ITERATION_EVALUATION,
        "model",
        "badcase_root_cause",
        ("ads_badcase_root_cause_distribution",),
        "Badcase 根因分布：数量 / 占比 / 趋势",
        origin_api="badcase/root-cause",
    ),
    ApiBinding(
        "ota/{ota_task_id}/summary",
        ClosedLoopService.MODEL_ITERATION_EVALUATION,
        "model",
        "ota_deployment",
        ("ads_ota_deployment_summary",),
        "OTA 部署汇总：任务数 / 车辆数 / 成功率 / 灰度进度",
    ),
    ApiBinding(
        "ota/{ota_task_id}/release-gate",
        ClosedLoopService.MODEL_ITERATION_EVALUATION,
        "model",
        "ota_release_gate",
        ("ads_ota_deployment_summary", "ads_model_version_comparison", "ads_trigger_heatmap"),
        "OTA 灰度三条件放行：成功率 99.2% / 触发量环比 +30% / 安全问题 0 起（与关系）",
    ),
    # 🔄 回传与挖掘闭环
    ApiBinding(
        "trigger/heatmap",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "heatmap",
        ("ads_trigger_heatmap",),
        "触发事件热力图：按地理网格看触发总量与热力等级",
    ),
    ApiBinding(
        "trigger/hotspots",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "top_hotspots",
        ("ads_trigger_heatmap",),
        "热点网格 TOP N：触发集中在哪",
    ),
    ApiBinding(
        "trigger/wow-anomalies",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "wow_anomalies",
        ("ads_trigger_heatmap",),
        "周环比异常网格：涨幅超告警线的网格 × 触发类型",
    ),
    ApiBinding(
        "trigger/{trigger_type}/closed-loop",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "closed_loop_status",
        ("ads_trigger_heatmap", "ads_hard_case_library"),
        "某类触发的闭环状态：触发量 → 难例 → 采纳",
        origin_api="trigger/.../closed-loop",
    ),
    ApiBinding(
        "hard-case/adoption",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "hard_case_adoption_summary",
        ("ads_hard_case_library",),
        "难例采纳率：采纳数 / 难例数，含闭环验证效果",
    ),
    ApiBinding(
        "scene-gap/status",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "scene_gap_status",
        ("ads_scene_library_summary",),
        "场景缺口状态：GAP / FILLING / COVERED 三态分布",
        origin_api="scene-gap/status",
    ),
    ApiBinding(
        "mining/tag-dashboard",
        ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP,
        "trigger",
        "mining_tag_dashboard",
        ("ads_mining_tag_dashboard",),
        "挖掘标签看板：三来源构成 / clip 覆盖率 / 候选池健康度",
    ),
    # 🧬 全链路血缘追溯
    ApiBinding(
        "lineage/anchors",
        ClosedLoopService.LINEAGE_TRACE,
        "lineage",
        "trace_anchors",
        ("ads_production_bottleneck_analysis", "ads_trigger_heatmap"),
        "追溯锚点：从 ADS 代表样本取 data_id 作为血缘起点",
    ),
    ApiBinding(
        "lineage/business/trace",
        ClosedLoopService.LINEAGE_TRACE,
        "lineage",
        "trace",
        (),
        "业务血缘追溯（转发 lineage 子系统）",
        origin_api="lineage/business/trace",
    ),
    ApiBinding(
        "lineage/impact",
        ClosedLoopService.LINEAGE_TRACE,
        "lineage",
        "impact",
        (),
        "影响分析（转发 lineage 子系统）",
        origin_api="lineage/impact",
    ),
)

#: 原文代表 API 中**不由 ADS 服务层回答**的，逐条写明去处——不假装覆盖。
DELEGATED_APIS: Final[dict[str, str]] = {
    "production/batch/.../progress": (
        "批次粒度进度要回 dwd_data_production_chain 逐条查（ADS 两张表的粒度是"
        "「统计日期 × 项目（× 环节）」，没有批次维度）；ADS 侧的等价接口是"
        " production/project/{project_code}/progress"
    ),
}

#: catalog 侧的缺列——本模块受影响的接口在 docstring 里都标了，收口阶段统一补。
#: （catalog/tables/_*.py 不属本模块，这里只登记，不修改。）
CATALOG_GAPS: Final[dict[str, tuple[str, ...]]] = {
    "ads_ota_deployment_summary": (
        "缺「发布后回传触发量」列（原文表 8 核心指标之一），三条件之②只能由服务层"
        "从 ads_trigger_heatmap 现算或由调用方给定",
        "缺「安全相关问题数」列（原文表 8「安全相关问题 0 起」），三条件之③只能由"
        "调用方给定或经 SafetyIssuePort 取",
    ),
    "ads_trigger_heatmap": (
        "缺「上传/处理完成率」与「入数据集量」两列（原文表 9 核心指标原话："
        "触发总量、上传/处理完成率与入数据集量）",
        "缺「周环比」列，45% 的案例只能由服务层按两个 7 天窗口现算",
    ),
    "ads_model_version_comparison": (
        "缺「评测数据集名称」列（只有 dataset_id + dataset_version）。原文表 7 的案例"
        f"是按名字引用数据集的——「{MODEL_COMPARE_DEMO_MODEL_VERSION} 与 "
        f"{MODEL_COMPARE_DEMO_BASELINE_VERSION} 在『{MODEL_COMPARE_DEMO_DATASET_NAME}』对比」"
        "——评测平台要显示这个名字就得回 ods_dataset_info 查，违反本表 notes 自己声明的"
        "「零 JOIN」。同为 ADS 的 ads_data_asset_catalog.asset_name、"
        "ads_ota_deployment_summary.project_name 都冗余了展示名，本表漏了",
    ),
}


@dataclass(slots=True)
class ClosedLoopServiceSuite:
    """六项闭环业务服务的装配体：一个查询服务进，六项服务 + 一套接口出。

    Args:
        ads: ADS 查询服务。
        semantic_port: 语义检索端口（可选，vector 子系统）。
        lineage_port: 血缘图库端口（可选，lineage 子系统）。
        safety_issue_port: 安全问题计数端口（可选，问题分析平台）。

    Examples:
        >>> from adas_lakehouse.ads.query import AdsQueryService, StaticRowSource
        >>> suite = ClosedLoopServiceSuite(AdsQueryService(StaticRowSource()))
        >>> len(suite.services)
        6
        >>> suite.production.SERVICE.name_cn
        '🚦 数据生产追踪'
    """

    ads: AdsQueryService
    semantic_port: SemanticSearchPort | None = None
    lineage_port: LineagePort | None = None
    safety_issue_port: SafetyIssuePort | None = None

    production: ProductionTrackingService = field(init=False)
    scene: SceneSearchCurationService = field(init=False)
    dataset: DatasetVersionDeliveryService = field(init=False)
    model: ModelIterationEvaluationService = field(init=False)
    trigger: TriggerMiningClosedLoopService = field(init=False)
    lineage: LineageTraceService = field(init=False)

    def __post_init__(self) -> None:
        self.production = ProductionTrackingService(self.ads)
        self.scene = SceneSearchCurationService(self.ads, semantic_port=self.semantic_port)
        self.dataset = DatasetVersionDeliveryService(self.ads)
        self.trigger = TriggerMiningClosedLoopService(self.ads)
        self.model = ModelIterationEvaluationService(
            self.ads,
            trigger_service=self.trigger,
            safety_issue_port=self.safety_issue_port,
        )
        self.lineage = LineageTraceService(self.ads, lineage_port=self.lineage_port)

    @property
    def services(self) -> dict[ClosedLoopService, _AdsService]:
        """六项服务，键是 [S1-全景] 第九章的业务服务枚举。"""
        return {
            ClosedLoopService.PRODUCTION_TRACKING: self.production,
            ClosedLoopService.SCENE_SEARCH_CURATION: self.scene,
            ClosedLoopService.DATASET_VERSION_DELIVERY: self.dataset,
            ClosedLoopService.MODEL_ITERATION_EVALUATION: self.model,
            ClosedLoopService.TRIGGER_MINING_CLOSED_LOOP: self.trigger,
            ClosedLoopService.LINEAGE_TRACE: self.lineage,
        }

    def handler(self, binding: ApiBinding) -> Any:
        """取某条接口的处理函数（绑定方法）。"""
        return getattr(getattr(self, binding.attr), binding.method)

    def register_routes(self, gateway: Any) -> int:
        """把全部业务接口注册到统一 API 网关，返回注册条数。

        Args:
            gateway: :class:`gateway.ApiGateway` 实例（这里不做类型 import，
                避免服务层反过来依赖网关）。
        """
        return gateway.register_all(
            (b.path, self.handler(b), b.summary_cn, b.service.name_cn) for b in API_BINDINGS
        )

    def api_catalog(self) -> list[dict[str, Any]]:
        """接口清单，供业务平台自助发现（哪项服务、读哪张表、对应原文哪个 API）。"""
        return [
            {
                "path": b.path,
                "service": b.service.name_cn,
                "question": b.service.question_cn,
                "summary": b.summary_cn,
                "tables": list(b.tables),
                "origin_api": b.origin_api,
            }
            for b in API_BINDINGS
        ]


def verify_service_exits(suite: ClosedLoopServiceSuite | None = None) -> dict[str, list[str]]:
    """服务化出口自检：11 张表是否都有出口、接口方法是否真实存在。

    检查四件事：

      1. 11 张 ADS 表每一张至少被一条接口覆盖（「开箱即用」的底线）；
      2. 每条接口声明的方法在对应服务上真实存在且可调用；
      3. 每条接口读的表都在产品矩阵内；
      4. [S1-全景] 第九章的每个代表 API 要么被实现，要么在
         :data:`DELEGATED_APIS` 里写明去处。

    Args:
        suite: 待检装配体；不传则用一个空行源现造一个（只查结构，不查数据）。

    Returns:
        ``{检查项: [问题…]}``，只含有问题的项；全绿时返回空字典。
    """
    from .query import StaticRowSource  # 局部 import：只在自检时需要

    suite = suite or ClosedLoopServiceSuite(AdsQueryService(StaticRowSource()))
    problems: dict[str, list[str]] = {}

    covered: set[str] = set()
    method_issues: list[str] = []
    table_issues: list[str] = []
    for binding in API_BINDINGS:
        covered |= set(binding.tables)
        service = suite.services.get(binding.service)
        if service is None or not callable(getattr(service, binding.method, None)):
            method_issues.append(f"{binding.path} → {binding.attr}.{binding.method} 不存在")
        for table in binding.tables:
            try:
                get_product(table)
            except Exception:  # noqa: BLE001 - 统一归口成自检问题
                table_issues.append(f"{binding.path} 读了非产品矩阵表 {table}")

    missing_tables = [p.table for p in PRODUCTS if p.table not in covered]
    if missing_tables:
        problems["表无服务化出口"] = [
            f"{t}（{get_product(t).title_cn}）没有任何接口覆盖" for t in missing_tables
        ]
    if method_issues:
        problems["接口方法缺失"] = method_issues
    if table_issues:
        problems["接口读了矩阵外的表"] = table_issues

    bound_origins = {b.origin_api for b in API_BINDINGS if b.origin_api}
    api_issues = [
        api
        for service in ClosedLoopService
        for api in service.representative_apis
        if api not in bound_origins and api not in DELEGATED_APIS
    ]
    if api_issues:
        problems["原文代表 API 未落地"] = [f"{api} 既没实现也没登记去处" for api in api_issues]
    return problems


def service_exit_matrix() -> list[dict[str, str]]:
    """「11 张表 × 服务化出口」矩阵，供文档与运维自检打印。"""
    rows: list[dict[str, str]] = []
    for product in PRODUCTS:
        paths = [b.path for b in API_BINDINGS if product.table in b.tables]
        rows.append(
            {
                "序": str(product.ordinal),
                "表名": product.table,
                "中文名": product.title_cn,
                "闭环业务服务": " / ".join(s.name_cn for s in product.consumed_by),
                "服务化出口": " · ".join(paths),
            }
        )
    return rows
