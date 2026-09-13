"""OpenAPI 网关：平台能力对外的唯一通道。

原文第三章接入层：「Web 控制台 + OpenAPI 网关……统一入口，认证 / 限流 / 审计，
**OpenAPI 为唯一对外通道**」；第六章给出四组接口：

  | 接口组 | 代表接口 | 说明 |
  | 检索类 | POST /api/v1/scene/semantic-search；GET /api/v1/scene/tag-coverage | 文搜图 / 图搜图 / 混合检索 + 标量过滤 |
  | 任务类 | POST /api/v1/mining/rule-jobs；GET /jobs/{jobId}/progress | 任务创建与进度查询，幂等键防重复提交 |
  | 标签治理类 | 标签字典 CRUD；候选审核 approve / reject；标签 merge | 一律经统一标签服务收口 |
  | 数据集类 | POST /api/v1/scene/curate；GET /datasets/{id}/export | 圈选结果回写数据资产域，clip 级防泄漏 |

本模块实现的是网关的**控制面部分**：路由表、幂等、限流、审计、准入。
真正的检索执行在数据面（走 External Catalog 查 Paimon / StarRocks 内表），
本模块只负责把请求翻译成任务或查询计划，绝不在控制面缓存或落地检索结果明细。

⚠️ 原文未明确，本项目设计：标签治理类接口原文只给了动作名没给路径，
这里按 ``/api/v1/tags/...`` 补全并显式标注；限流阈值
:data:`~.constants.GATEWAY_RATE_LIMIT_QPS` 同样是本项目设计。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import constants as K
from .contracts import MasterDataLeak, ReviewDecision, TaskKind, assert_no_master_data
from .scheduler import ControlPlane, SubmitRequest

__all__ = [
    "ApiGroup",
    "ApiEndpoint",
    "API_ENDPOINTS",
    "ApiError",
    "RateLimited",
    "AuditEntry",
    "TokenBucket",
    "OpenApiGateway",
    "openapi_document",
]

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- 路由表


@dataclass(frozen=True, slots=True)
class ApiGroup:
    """一个接口组。原文第六章：「接口分四组，每组都对应一个明确的业务动作」。"""

    key: str
    name_cn: str
    description: str


#: 四组接口（原文第六章表格，逐字）
API_GROUPS: tuple[ApiGroup, ...] = (
    ApiGroup("search", "检索类", "文搜图 / 图搜图 / 混合检索 + 标量过滤"),
    ApiGroup("task", "任务类", "任务创建与进度查询，幂等键防重复提交"),
    ApiGroup("tag", "标签治理类", "一律经统一标签服务收口"),
    ApiGroup("dataset", "数据集类", "圈选结果回写数据资产域，clip 级防泄漏"),
)
assert len(API_GROUPS) == K.OPENAPI_GROUP_COUNT  # 原文：接口分四组


@dataclass(frozen=True, slots=True)
class ApiEndpoint:
    """一个接口。``verbatim=True`` 表示路径逐字来自原文第六章表格。"""

    method: str
    path: str
    group: str
    summary: str
    verbatim: bool = True
    requires_idempotency_key: bool = False

    @property
    def route(self) -> tuple[str, str]:
        return (self.method.upper(), self.path)


#: 全部接口。原文点名的六个 verbatim=True，标签治理类的三个路径为本项目补全。
API_ENDPOINTS: tuple[ApiEndpoint, ...] = (
    # ---- 检索类（原文逐字）----
    ApiEndpoint(
        "POST", K.PATH_SEMANTIC_SEARCH, "search", "语义检索：文搜图 / 图搜图 / 混合检索 + 标量过滤"
    ),
    ApiEndpoint("GET", K.PATH_TAG_COVERAGE, "search", "标签覆盖率查询"),
    # ---- 任务类（原文逐字）----
    ApiEndpoint(
        "POST", K.PATH_RULE_JOBS, "task", "创建规则挖掘任务", requires_idempotency_key=True
    ),
    ApiEndpoint("GET", K.PATH_JOB_PROGRESS, "task", "任务进度查询"),
    # ---- 标签治理类（⚠️ 原文只给动作名，路径为本项目补全）----
    ApiEndpoint("POST", "/api/v1/tags/dict", "tag", "标签字典 CRUD（新增）", verbatim=False),
    ApiEndpoint(
        "POST",
        "/api/v1/tags/candidates/{candidateId}/review",
        "tag",
        "候选审核 approve / reject",
        verbatim=False,
    ),
    ApiEndpoint(
        "POST",
        "/api/v1/tags/merge",
        "tag",
        "标签 merge",
        verbatim=False,
        requires_idempotency_key=True,
    ),
    # ---- 数据集类（原文逐字）----
    ApiEndpoint(
        "POST",
        K.PATH_SCENE_CURATE,
        "dataset",
        "圈选结果回写数据资产域，clip 级防泄漏",
        requires_idempotency_key=True,
    ),
    ApiEndpoint("GET", K.PATH_DATASET_EXPORT, "dataset", "数据集导出"),
)


# --------------------------------------------------------------------------- 异常


class ApiError(RuntimeError):
    """网关层错误，带 HTTP 状态码。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class RateLimited(ApiError):
    """限流。原文第三章接入层职责之一。"""

    def __init__(self, qps: int) -> None:
        super().__init__(429, f"超过网关限流阈值 {qps} QPS")


# --------------------------------------------------------------------------- 限流与审计


class TokenBucket:
    """滑动窗口限流器。阈值 :data:`~.constants.GATEWAY_RATE_LIMIT_QPS`（⚠️ 本项目设计）。"""

    def __init__(self, qps: int = K.GATEWAY_RATE_LIMIT_QPS) -> None:
        self.qps = qps
        self._lock = threading.Lock()
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] >= 1.0:
                self._hits.popleft()
            if len(self._hits) >= self.qps:
                return False
            self._hits.append(now)
            return True


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """一条审计记录。原文第三章接入层：认证 / 限流 / **审计**。"""

    api_path: str
    http_method: str
    caller: str
    at: datetime
    outcome: str
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "api_path": self.api_path,
            "http_method": self.http_method,
            "caller": self.caller,
            "at": self.at,
            "outcome": self.outcome,
            "detail": self.detail[:500],
        }


# --------------------------------------------------------------------------- 网关


@dataclass(slots=True)
class _Principal:
    """调用方身份。

    ⚠️ 原文未明确，本项目设计：原文只说「统一登录，权限复用湖仓四级管控」
    （:data:`~.constants.LAKEHOUSE_PERMISSION_LEVELS` = 4 级）。
    这里用一个 0-3 的 level 表达那四级，0 最高。真实鉴权接统一登录，不在本模块实现。
    """

    name: str
    level: int = K.LAKEHOUSE_PERMISSION_LEVELS - 1


class OpenApiGateway:
    """OpenAPI 网关：唯一对外通道。

    职责按原文第三章接入层三件事落地：**认证**（调用方身份 + 四级权限）、
    **限流**（:class:`TokenBucket`）、**审计**（:class:`AuditEntry` 流）。
    业务逻辑一概委托给 :class:`~.scheduler.ControlPlane`，网关自己不持有任何状态
    ——除了审计流与限流窗口，两者都是可丢的运行态。

    :param control_plane: 控制面编排器
    :param search_executor: 检索执行器（数据面），签名
        ``(payload: Mapping) -> Mapping``。不传则检索类接口返回 501——
        因为检索必须在数据面执行，控制面没有能力也没有权力自己查数据。
    """

    def __init__(
        self,
        control_plane: ControlPlane,
        *,
        search_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self.control_plane = control_plane
        self._search = search_executor
        self._limiter = rate_limiter or TokenBucket()
        self._audit: list[AuditEntry] = []
        self._routes: dict[tuple[str, str], Callable[..., Any]] = {
            ("POST", K.PATH_SEMANTIC_SEARCH): self._semantic_search,
            ("GET", K.PATH_TAG_COVERAGE): self._tag_coverage,
            ("POST", K.PATH_RULE_JOBS): self._create_rule_job,
            ("GET", K.PATH_JOB_PROGRESS): self._job_progress,
            ("POST", "/api/v1/tags/dict"): self._tag_dict,
            ("POST", "/api/v1/tags/candidates/{candidateId}/review"): self._tag_review,
            ("POST", "/api/v1/tags/merge"): self._tag_merge,
            ("POST", K.PATH_SCENE_CURATE): self._curate,
            ("GET", K.PATH_DATASET_EXPORT): self._dataset_export,
        }

    # ---- 分发 ----

    def handle(
        self,
        method: str,
        path: str,
        *,
        caller: str = "anonymous",
        level: int = K.LAKEHOUSE_PERMISSION_LEVELS - 1,
        body: Mapping[str, Any] | None = None,
        path_params: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """处理一次调用：限流 → 认证 → 主数据守卫 → 路由 → 审计。

        :raises ApiError: 路由不存在（404）、限流（429）、载荷越界（400）
        """
        route = (method.upper(), path)
        principal = _Principal(caller, level)
        if not self._limiter.allow():
            self._record(path, method, caller, "rate_limited")
            raise RateLimited(self._limiter.qps)
        handler = self._routes.get(route)
        if handler is None:
            self._record(path, method, caller, "not_found")
            raise ApiError(404, f"未定义的接口: {method} {path}")
        payload = dict(body or {})
        try:
            # 入口守卫：外部调用方同样不允许往控制面塞主数据
            assert_no_master_data(payload, where=f"{method} {path}")
        except MasterDataLeak as exc:
            self._record(path, method, caller, "master_data_rejected", str(exc))
            raise ApiError(400, str(exc)) from exc
        try:
            result = handler(payload, dict(path_params or {}), principal, idempotency_key)
        except ApiError:
            self._record(path, method, caller, "error")
            raise
        except Exception as exc:
            self._record(path, method, caller, "error", repr(exc))
            raise ApiError(500, f"内部错误: {exc!r}") from exc
        self._record(path, method, caller, "ok")
        return dict(result)

    def audit_log(self) -> list[AuditEntry]:
        """审计流。原文第三章接入层职责之一，落 ``cp_audit_log``。"""
        return list(self._audit)

    def _record(self, path: str, method: str, caller: str, outcome: str, detail: str = "") -> None:
        self._audit.append(
            AuditEntry(path, method.upper(), caller, datetime.now(), outcome, detail)
        )

    # ---- 检索类 ----

    def _semantic_search(
        self, body: Mapping[str, Any], _p: Mapping[str, str], principal: _Principal, _k: str | None
    ) -> Mapping[str, Any]:
        """POST /api/v1/scene/semantic-search

        检索**必须**在数据面执行——控制面不持有明细，也不缓存明细。
        这里只做参数规整与 top_k 限制（:data:`~.constants.SEARCH_MAX_TOP_K`），
        然后交给注入的数据面执行器。
        """
        if self._search is None:
            raise ApiError(
                501,
                "未接入数据面检索执行器；检索走 External Catalog 查 Paimon "
                "或 StarRocks 内表，控制面不自建数据通道",
            )
        top_k = int(body.get("topK", 50))
        if top_k > K.SEARCH_MAX_TOP_K:
            raise ApiError(400, f"topK 超过上限 {K.SEARCH_MAX_TOP_K}")
        query = {
            "mode": body.get("mode", "text2image"),
            "query_text": body.get("queryText", ""),
            "query_image_artifact_id": body.get("queryImageArtifactId"),
            "filters": body.get("filters", {}),
            "top_k": top_k,
            "caller_level": principal.level,
        }
        return dict(self._search(query))

    def _tag_coverage(
        self, body: Mapping[str, Any], _p: Mapping[str, str], principal: _Principal, _k: str | None
    ) -> Mapping[str, Any]:
        """GET /api/v1/scene/tag-coverage —— 标签覆盖率，走数据面看板出口。"""
        if self._search is None:
            raise ApiError(501, "未接入数据面查询执行器")
        return dict(
            self._search(
                {
                    "mode": "tag_coverage",
                    "filters": body.get("filters", {}),
                    "caller_level": principal.level,
                }
            )
        )

    # ---- 任务类 ----

    def _create_rule_job(
        self, body: Mapping[str, Any], _p: Mapping[str, str], principal: _Principal, key: str | None
    ) -> Mapping[str, Any]:
        """POST /api/v1/mining/rule-jobs —— 创建规则挖掘任务，幂等键防重复提交。"""
        rule_id = body.get("ruleId")
        if not rule_id:
            raise ApiError(400, "缺少 ruleId")
        request = SubmitRequest(
            kind=TaskKind.RULE_MINING,
            rule_id=str(rule_id),
            rule_version=body.get("ruleVersion"),
            input_selector=str(body.get("inputSelector", "")),
            input_tables=tuple(body.get("inputTables", ())),
            params=body.get("params", {}),
            priority=int(body.get("priority", K.PRIORITY_DEFAULT)),
            requested_by=principal.name,
            idempotency_key=key,
        )
        record = self.control_plane.submit(request)
        return {
            "jobId": record.task_id,
            "runId": record.envelope.run_id,
            "state": record.state.value,
        }

    def _job_progress(
        self, _b: Mapping[str, Any], path_params: Mapping[str, str], _pr: _Principal, _k: str | None
    ) -> Mapping[str, Any]:
        """GET /jobs/{jobId}/progress"""
        job_id = path_params.get("jobId")
        if not job_id:
            raise ApiError(400, "缺少路径参数 jobId")
        try:
            return self.control_plane.progress(job_id)
        except KeyError as exc:
            raise ApiError(404, str(exc)) from exc

    # ---- 标签治理类 ----

    def _tag_dict(
        self, body: Mapping[str, Any], _p: Mapping[str, str], principal: _Principal, key: str | None
    ) -> Mapping[str, Any]:
        """标签字典 CRUD —— 一律经统一标签服务收口，落到 tags 子系统执行。"""
        record = self.control_plane.submit(
            SubmitRequest(
                kind=TaskKind.TAG_GOVERNANCE,
                params={"action": body.get("action", "upsert"), "tag": body.get("tag", {})},
                requested_by=principal.name,
                idempotency_key=key,
            )
        )
        return {"jobId": record.task_id, "state": record.state.value}

    def _tag_review(
        self,
        body: Mapping[str, Any],
        path_params: Mapping[str, str],
        principal: _Principal,
        _k: str | None,
    ) -> Mapping[str, Any]:
        """候选审核 approve / reject（原文第六章逐字动作名）。"""
        decision_raw = str(body.get("decision", "")).lower()
        try:
            decision = ReviewDecision(decision_raw)
        except ValueError as exc:
            raise ApiError(400, f"decision 必须是 approve / reject，收到 {decision_raw!r}") from exc
        job_id = body.get("jobId") or path_params.get("candidateId")
        if not job_id:
            raise ApiError(400, "缺少 jobId / candidateId")
        record = self.control_plane.review(
            str(job_id), decision, reviewer=principal.name, note=str(body.get("note", ""))
        )
        return {"jobId": record.task_id, "state": record.state.value, "decision": decision.value}

    def _tag_merge(
        self, body: Mapping[str, Any], _p: Mapping[str, str], principal: _Principal, key: str | None
    ) -> Mapping[str, Any]:
        """标签 merge。合并是写操作，同样落 tags 子系统在数据面执行。"""
        sources = list(body.get("sourceTagCodes", ()))
        target = body.get("targetTagCode")
        if not sources or not target:
            raise ApiError(400, "缺少 sourceTagCodes / targetTagCode")
        record = self.control_plane.submit(
            SubmitRequest(
                kind=TaskKind.TAG_GOVERNANCE,
                params={"action": "merge", "source_tag_codes": sources, "target_tag_code": target},
                requested_by=principal.name,
                idempotency_key=key,
            )
        )
        return {"jobId": record.task_id, "state": record.state.value}

    # ---- 数据集类 ----

    def _curate(
        self, body: Mapping[str, Any], _p: Mapping[str, str], principal: _Principal, key: str | None
    ) -> Mapping[str, Any]:
        """POST /api/v1/scene/curate —— 圈选结果回写数据资产域，clip 级防泄漏。

        「clip 级防泄漏」的落地：圈选粒度只能是 clip（data_id），不允许按帧导出，
        否则同一 clip 的帧会散落到训练/验证集两侧造成数据泄漏。
        """
        selector = str(body.get("selector", ""))
        if not selector:
            raise ApiError(400, "缺少 selector（圈选谓词）")
        grain = str(body.get("grain", "clip"))
        if grain != "clip":
            raise ApiError(400, "圈选粒度只能是 clip —— clip 级防泄漏（原文第六章数据集类）")
        record = self.control_plane.submit(
            SubmitRequest(
                kind=TaskKind.CURATION,
                input_selector=selector,
                params={
                    "grain": grain,
                    K.BACKFILL_DATASET_ID_FIELD: body.get("backfillDatasetId"),
                    "dataset_name": body.get("datasetName", ""),
                },
                requested_by=principal.name,
                idempotency_key=key,
            )
        )
        return {"jobId": record.task_id, "state": record.state.value}

    def _dataset_export(
        self,
        _b: Mapping[str, Any],
        path_params: Mapping[str, str],
        principal: _Principal,
        _k: str | None,
    ) -> Mapping[str, Any]:
        """GET /datasets/{id}/export —— 导出只给指针（对象存储 key / 数据集版本），不给数据本体。"""
        dataset_id = path_params.get("id")
        if not dataset_id:
            raise ApiError(400, "缺少路径参数 id")
        return {
            "datasetId": dataset_id,
            "grain": "clip",
            "permissionLevel": principal.level,
            "note": (
                "导出返回数据集指针（dataset_id / dataset_version / object_key），"
                "数据本体留在湖仓与对象存储——平台从不自建数据通道"
            ),
        }


# --------------------------------------------------------------------------- 文档


def openapi_document() -> dict[str, Any]:
    """生成最小 OpenAPI 3.0 文档，供网关对外发布。

    只描述路由与分组；schema 细节留给各服务，本模块不替它们定义业务字段。
    """
    paths: dict[str, dict[str, Any]] = {}
    for ep in API_ENDPOINTS:
        item = paths.setdefault(ep.path, {})
        item[ep.method.lower()] = {
            "summary": ep.summary,
            "tags": [ep.group],
            "x-source-verbatim": ep.verbatim,
            "x-requires-idempotency-key": ep.requires_idempotency_key,
        }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "数据挖掘与多模态检索平台 OpenAPI",
            "version": K.API_VERSION,
            "description": (
                f"OpenAPI 为唯一对外通道；接口分 {K.OPENAPI_GROUP_COUNT} 组；"
                f"权限复用湖仓 {K.LAKEHOUSE_PERMISSION_LEVELS} 级管控。"
                f"来源：{K.SOURCE_URL}"
            ),
        },
        "tags": [
            {"name": g.key, "description": f"{g.name_cn}：{g.description}"} for g in API_GROUPS
        ],
        "paths": paths,
        "x-rate-limit-qps": K.GATEWAY_RATE_LIMIT_QPS,
    }
