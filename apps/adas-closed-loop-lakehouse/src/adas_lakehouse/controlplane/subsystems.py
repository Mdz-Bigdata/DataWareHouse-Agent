"""子系统接入契约：控制面如何调度 mining / sampling / tags，而**不** import 它们的内部实现。

原文第三章的原话是：「引擎与服务解耦、引擎之间也解耦」——

  「抽帧结果写入 dwd_mining_image_frame_detail 之后，规则挖掘与 VLM 推理各自基于该表
    独立运行——批处理引擎白天跑批，推理引擎按优先级抢 GPU，谁也不等谁。任何一个引擎
    故障或扩容，都不影响其他链路。」

这句话在 Python 里怎么落地？两条硬约束：

  1. **控制面不 import 子系统内部实现**。它只认 :class:`SubsystemAdapter` 这个
     Protocol。子系统在自己的目录里提供 ``plane_adapter.build_plane_adapter()``
     工厂函数，控制面用 :func:`importlib.import_module` 按**字符串路径**延迟绑定。
     子系统没写好、没装依赖、甚至整个目录不存在，控制面都照样 import、照样启动——
     只是那一条链路报 :class:`SubsystemUnavailable`，其他链路不受影响。
     这就是「任何一个引擎故障，都不影响其他链路」的代码形态。

  2. **子系统之间零耦合**。它们不互相调用，只通过数据面的 Paimon 表交接
     （sampling 写 ``dwd_mining_image_frame_detail``，mining 读它）。控制面负责的
     只是「谁先跑谁后跑」的编排，不负责搬运中间结果。

⚠️ 原文未明确，本项目设计：``plane_adapter`` 模块名、``build_plane_adapter`` 工厂名、
以及 Adapter 的五个方法签名，都是本项目定的接入约定。原文只给了架构图层面的解耦要求。
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .contracts import RunReport, TaskEnvelope, TaskKind

__all__ = [
    "SUBSYSTEM_MODULE_PATHS",
    "REQUIRED_SUBSYSTEMS",
    "OPTIONAL_SUBSYSTEMS",
    "ADAPTER_FACTORY_NAME",
    "SubsystemAdapter",
    "SubsystemUnavailable",
    "SubsystemBinding",
    "SubsystemRegistry",
    "subsystem_for",
]


#: 控制面必须能调度的三个子系统（本任务的题面）。
REQUIRED_SUBSYSTEMS: tuple[str, ...] = ("mining", "sampling", "tags")

#: 可选子系统：向量化流水线。原文把它与 VLM 推理并列在 GPU 池里，但它不属于题面三件套。
OPTIONAL_SUBSYSTEMS: tuple[str, ...] = ("vector",)

#: 子系统名 -> 适配器模块路径（**字符串**，永不在模块顶层 import）。
SUBSYSTEM_MODULE_PATHS: dict[str, str] = {
    "mining": "adas_lakehouse.mining.plane_adapter",
    "sampling": "adas_lakehouse.sampling.plane_adapter",
    "tags": "adas_lakehouse.tags.plane_adapter",
    "vector": "adas_lakehouse.vector.plane_adapter",
}

#: 适配器工厂函数名。子系统在 ``plane_adapter`` 模块里提供
#: ``def build_plane_adapter() -> SubsystemAdapter``。
ADAPTER_FACTORY_NAME: str = "build_plane_adapter"


class SubsystemUnavailable(RuntimeError):
    """子系统适配器不可用（未实现 / 依赖缺失 / 工厂函数签名不对）。

    刻意做成「单链路失败」而不是「平台启动失败」——对应原文
    「任何一个引擎故障或扩容，都不影响其他链路」。
    """


@runtime_checkable
class SubsystemAdapter(Protocol):
    """数据面子系统必须实现的五个方法。

    控制面只会调这五个，多一个都不会调；子系统内部用 Spark、Flink、K8s Job 还是
    Ray，控制面一概不知道，也不该知道。
    """

    #: 子系统名，取值须在 SUBSYSTEM_MODULE_PATHS 里
    name: str

    def supported_kinds(self) -> frozenset[TaskKind]:
        """本子系统能接的任务种类。控制面下发前做准入校验。"""
        ...

    def submit(self, envelope: TaskEnvelope) -> str:
        """接收任务信封，向数据面引擎提交作业，返回外部作业句柄。

        实现方必须保证**幂等**：同一个 ``envelope.run_id`` 重复提交，应返回同一个句柄
        而不是跑第二遍。这是重试能安全进行的前提。
        """
        ...

    def poll(self, handle: str) -> RunReport:
        """查作业状态，返回运行回报（只含指针，不含数据本体）。"""
        ...

    def cancel(self, handle: str) -> bool:
        """取消作业。返回是否真的取消掉了（已终止的作业返回 False）。"""
        ...

    def health(self) -> bool:
        """子系统自检。False 时控制面停止向它下发，但不影响其他子系统。"""
        ...


@dataclass(frozen=True, slots=True)
class SubsystemBinding:
    """一次绑定的结果快照，供 ``/health`` 与运维面板展示。"""

    name: str
    module_path: str
    bound: bool
    kinds: frozenset[TaskKind] = frozenset()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.name,
            "module_path": self.module_path,
            "bound": self.bound,
            "supported_kinds": sorted(k.value for k in self.kinds),
            "error": self.error,
        }


def subsystem_for(kind: TaskKind) -> str:
    """任务种类 -> 子系统名。路由表见 :data:`~.contracts.SUBSYSTEM_ROUTING`。"""
    from .contracts import SUBSYSTEM_ROUTING

    try:
        return SUBSYSTEM_ROUTING[kind]
    except KeyError as exc:  # pragma: no cover - 枚举穷尽后不会发生
        raise SubsystemUnavailable(f"任务种类 {kind} 没有登记路由") from exc


class SubsystemRegistry:
    """子系统适配器注册表：显式注册优先，其次按模块路径延迟绑定。

    延迟绑定是关键：控制面 import 时**不会**触发任何子系统的 import，
    因此 mining/sampling/tags 谁没写完、谁的依赖没装，都不会让控制面炸掉。
    """

    def __init__(self, module_paths: dict[str, str] | None = None) -> None:
        self._lock = threading.RLock()
        self._paths = dict(module_paths or SUBSYSTEM_MODULE_PATHS)
        self._adapters: dict[str, SubsystemAdapter] = {}
        self._errors: dict[str, str] = {}

    # ---- 注册 ----

    def register(self, adapter: SubsystemAdapter) -> None:
        """显式注册一个适配器（测试、或者进程内直接装配时用）。"""
        name = getattr(adapter, "name", "")
        if not name:
            raise SubsystemUnavailable("适配器缺少 name 属性")
        if not isinstance(adapter, SubsystemAdapter):
            raise SubsystemUnavailable(
                f"适配器 {name!r} 未实现 SubsystemAdapter 协议的全部五个方法"
            )
        with self._lock:
            self._adapters[name] = adapter
            self._errors.pop(name, None)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._adapters.pop(name, None)

    # ---- 解析 ----

    def resolve(self, name: str) -> SubsystemAdapter:
        """拿到子系统适配器。顺序：已注册 → 延迟 import 绑定 → 报错。

        :raises SubsystemUnavailable: 子系统未实现或依赖缺失（只影响这条链路）
        """
        with self._lock:
            cached = self._adapters.get(name)
            if cached is not None:
                return cached
            path = self._paths.get(name)
            if path is None:
                raise SubsystemUnavailable(f"未知子系统 {name!r}；已登记：{sorted(self._paths)}")
            try:
                module = importlib.import_module(path)
            except ImportError as exc:
                msg = f"子系统 {name!r} 的适配器模块 {path} 不可用：{exc}"
                self._errors[name] = msg
                raise SubsystemUnavailable(msg) from exc
            factory = getattr(module, ADAPTER_FACTORY_NAME, None)
            if factory is None or not callable(factory):
                msg = (
                    f"模块 {path} 缺少工厂函数 {ADAPTER_FACTORY_NAME}()；"
                    f"接入约定：def {ADAPTER_FACTORY_NAME}() -> SubsystemAdapter"
                )
                self._errors[name] = msg
                raise SubsystemUnavailable(msg)
            try:
                adapter = factory()
            except Exception as exc:  # 子系统构造失败不该拖垮控制面
                msg = f"子系统 {name!r} 的 {ADAPTER_FACTORY_NAME}() 构造失败：{exc!r}"
                self._errors[name] = msg
                raise SubsystemUnavailable(msg) from exc
            if not isinstance(adapter, SubsystemAdapter):
                msg = f"子系统 {name!r} 返回的对象未实现 SubsystemAdapter 协议"
                self._errors[name] = msg
                raise SubsystemUnavailable(msg)
            self._adapters[name] = adapter
            self._errors.pop(name, None)
            return adapter

    def resolve_for(self, kind: TaskKind) -> SubsystemAdapter:
        """按任务种类解析适配器，并校验它确实接这种任务。"""
        name = subsystem_for(kind)
        adapter = self.resolve(name)
        if kind not in adapter.supported_kinds():
            raise SubsystemUnavailable(
                f"子系统 {name!r} 不接任务种类 {kind.value}；"
                f"它声明支持 {sorted(k.value for k in adapter.supported_kinds())}"
            )
        return adapter

    # ---- 观测 ----

    def describe(self) -> list[SubsystemBinding]:
        """列出全部子系统的绑定情况。不抛异常——运维面板要的是全景，不是第一个错误。"""
        out: list[SubsystemBinding] = []
        for name in sorted(self._paths):
            path = self._paths[name]
            try:
                adapter = self.resolve(name)
            except SubsystemUnavailable as exc:
                out.append(SubsystemBinding(name, path, False, error=str(exc)))
                continue
            try:
                kinds = adapter.supported_kinds()
            except Exception as exc:  # pragma: no cover - 子系统自身异常
                out.append(
                    SubsystemBinding(name, path, False, error=f"supported_kinds() 失败：{exc!r}")
                )
                continue
            out.append(SubsystemBinding(name, path, True, kinds=kinds))
        return out

    def healthy_subsystems(self) -> set[str]:
        """当前可下发的子系统集合。绑定失败或 health() 为假的一律排除。"""
        ok: set[str] = set()
        for name in self._paths:
            try:
                adapter = self.resolve(name)
                if adapter.health():
                    ok.add(name)
            except Exception:
                continue
        return ok

    def missing_required(self) -> list[str]:
        """还没接上的必需子系统（mining / sampling / tags）。"""
        healthy = self.healthy_subsystems()
        return [n for n in REQUIRED_SUBSYSTEMS if n not in healthy]
