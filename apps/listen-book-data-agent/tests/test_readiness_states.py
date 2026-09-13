"""/ready 的三态口径：ok / not_configured / error。

重点不是「让 full profile 变绿」，而是**不能把真故障吞掉**：
只有拿到明确依据（显式关闭 / 没配地址 / 可选依赖且主机名解析不出来）才判
not_configured，其余一律 error。下面每条 not_configured 用例都配了一条反例。
"""

from __future__ import annotations

import asyncio
import socket
import unittest

from app.services import health_service
from app.services.health_service import (
    Dependency,
    DependencyUnavailable,
    readiness_report,
    tri_state,
)


async def _ok() -> None:
    return None


class _Boom(Exception):
    pass


async def _broken() -> None:
    raise _Boom("password=should-not-leak")


class _Calls:
    """记录探针是否真的被调用过。"""

    def __init__(self, probe):
        self.count = 0
        self._probe = probe

    async def __call__(self) -> None:
        self.count += 1
        await self._probe()


class ReadinessStateTest(unittest.TestCase):
    def setUp(self) -> None:
        # 单测里不碰真 DNS：显式控制「主机名解析不出来」这一输入。
        self._real_resolver = health_service._definitely_unresolvable
        self.resolver_calls: list[str] = []

        async def fake_resolver(host: str, port: int) -> bool:
            self.resolver_calls.append(host)
            return host.endswith(".invalid")

        health_service._definitely_unresolvable = fake_resolver

    def tearDown(self) -> None:
        health_service._definitely_unresolvable = self._real_resolver

    def report(self, *dependencies: Dependency) -> dict:
        return asyncio.run(readiness_report(list(dependencies), timeout_seconds=1.0))

    # ---------- not_configured：按设计未启用 ----------

    def test_optional_dependency_with_unresolvable_host_is_not_configured(self):
        probe = _Calls(_broken)
        report = self.report(
            Dependency("metadata_mysql", _ok, host="mysql", port=3306),
            Dependency("embedding", probe, optional=True, host="embedding.invalid", port=80),
        )

        self.assertEqual(report["dependencies"]["embedding"]["status"], "not_configured")
        self.assertEqual(report["dependencies"]["embedding"]["detail"], "dns_unresolved")
        self.assertEqual(report["dependencies"]["embedding"]["target"], "embedding.invalid:80")
        self.assertEqual(report["not_configured"], ["embedding"])
        # 未启用的依赖不拉低整体状态，也不该浪费一次探测超时。
        self.assertEqual(report["status"], "ready")
        self.assertEqual(probe.count, 0)

    def test_explicitly_disabled_dependency_is_not_probed(self):
        probe = _Calls(_broken)
        report = self.report(
            Dependency("embedding", probe, optional=True, enabled=False, host="embedding", port=80)
        )

        self.assertEqual(report["dependencies"]["embedding"]["status"], "not_configured")
        self.assertEqual(report["dependencies"]["embedding"]["detail"], "disabled_by_config")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(probe.count, 0)

    def test_optional_dependency_without_host_is_not_configured(self):
        report = self.report(Dependency("embedding", _broken, optional=True, host=""))

        self.assertEqual(report["dependencies"]["embedding"]["status"], "not_configured")
        self.assertEqual(report["dependencies"]["embedding"]["detail"], "host_not_configured")
        self.assertEqual(report["status"], "ready")

    # ---------- ★反例★：真故障必须照报不误 ----------

    def test_explicitly_enabled_dependency_reports_error_even_when_dns_fails(self):
        """把 EMBEDDING_HOST 指到不存在的主机名、但显式启用 —— 必须是 error。"""

        probe = _Calls(_broken)
        report = self.report(
            Dependency(
                "embedding", probe, optional=True, enabled=True, host="embedding.invalid", port=80
            )
        )

        self.assertEqual(report["dependencies"]["embedding"]["status"], "error")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["not_configured"], [])
        # 显式启用时压根不看 DNS：解析成功与否都不能成为「未启用」的借口。
        self.assertEqual(self.resolver_calls, [])
        self.assertEqual(probe.count, 1)

    def test_optional_dependency_that_resolves_but_fails_is_an_error(self):
        """主机名能解析 = 服务应该在。连不上就是真故障，不许判 not_configured。"""

        report = self.report(
            Dependency("embedding", _broken, optional=True, host="embedding", port=80)
        )

        self.assertEqual(report["dependencies"]["embedding"]["status"], "error")
        self.assertEqual(report["dependencies"]["embedding"]["detail"], "_Boom")
        self.assertEqual(report["status"], "unavailable")

    def test_required_dependency_is_never_excused_by_dns(self):
        """非可选依赖（元数据库等）解析不出来也是 error，不能被当成「未启用」。"""

        report = self.report(Dependency("metadata_mysql", _broken, host="mysql.invalid", port=3306))

        self.assertEqual(report["dependencies"]["metadata_mysql"]["status"], "error")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(self.resolver_calls, [])

    def test_explicitly_enabled_dependency_without_host_is_a_configuration_error(self):
        report = self.report(Dependency("embedding", _ok, optional=True, enabled=True, host=""))

        self.assertEqual(report["dependencies"]["embedding"]["status"], "error")
        self.assertEqual(report["dependencies"]["embedding"]["detail"], "host_not_configured")
        self.assertEqual(report["status"], "unavailable")

    # ---------- 加速层（Redis）：失败只降级 ----------

    def test_soft_fail_dependency_degrades_without_breaking_readiness(self):
        report = self.report(
            Dependency("metadata_mysql", _ok, host="mysql", port=3306),
            Dependency("redis", _broken, optional=True, soft_fail=True, host="redis", port=6379),
        )

        self.assertEqual(report["dependencies"]["redis"]["status"], "degraded")
        self.assertEqual(report["degraded"], ["redis"])
        self.assertEqual(report["status"], "ready")

    # ---------- 细节 ----------

    def test_failure_detail_never_leaks_probe_exception_message(self):
        report = self.report(Dependency("metadata_mysql", _broken, host="mysql", port=3306))

        self.assertEqual(report["dependencies"]["metadata_mysql"]["detail"], "_Boom")
        self.assertNotIn("password", str(report))

    def test_declared_unavailable_reason_is_kept_as_a_fixed_token(self):
        async def not_initialised() -> None:
            raise DependencyUnavailable("client_not_initialized")

        report = self.report(Dependency("qdrant", not_initialised, host="qdrant", port=6333))

        self.assertEqual(report["dependencies"]["qdrant"]["detail"], "client_not_initialized")

    def test_timeout_is_reported_as_an_error(self):
        async def hangs() -> None:
            await asyncio.sleep(5)

        report = asyncio.run(
            readiness_report([Dependency("qdrant", hangs, host="qdrant")], timeout_seconds=0.05)
        )

        self.assertEqual(report["dependencies"]["qdrant"]["status"], "error")
        self.assertEqual(report["dependencies"]["qdrant"]["detail"], "timeout")

    def test_legacy_mapping_signature_treats_every_probe_as_required(self):
        report = asyncio.run(readiness_report({"mysql": _ok, "qdrant": _broken}))

        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["dependencies"]["qdrant"]["status"], "error")


class TriStateTest(unittest.TestCase):
    def test_switch_parsing(self):
        self.assertIs(tri_state("true"), True)
        self.assertIs(tri_state("1"), True)
        self.assertIs(tri_state("false"), False)
        self.assertIs(tri_state("off"), False)
        self.assertIsNone(tri_state("auto"))
        self.assertIsNone(tri_state(""))
        self.assertIsNone(tri_state(None))
        # 认不出来的值走 auto，而不是悄悄当成「已关闭」。
        self.assertIsNone(tri_state("maybe"))


class ResolverTest(unittest.TestCase):
    """验证 _definitely_unresolvable 本身：只有明确的解析失败才返回 True。"""

    def test_reserved_invalid_tld_is_unresolvable(self):
        self.assertTrue(
            asyncio.run(health_service._definitely_unresolvable("no-such-host.invalid", 80))
        )

    def test_loopback_resolves(self):
        self.assertFalse(asyncio.run(health_service._definitely_unresolvable("127.0.0.1", 80)))

    def test_resolver_timeout_does_not_count_as_unconfigured(self):
        async def hangs(*args, **kwargs):
            await asyncio.sleep(5)

        async def run() -> bool:
            loop = asyncio.get_running_loop()
            original = loop.getaddrinfo
            loop.getaddrinfo = hangs  # type: ignore[method-assign]
            try:
                health_service.DEFAULT_RESOLVE_TIMEOUT = 0.05
                return await health_service._definitely_unresolvable("slow-dns", 80)
            finally:
                loop.getaddrinfo = original  # type: ignore[method-assign]
                health_service.DEFAULT_RESOLVE_TIMEOUT = 2.0

        self.assertFalse(asyncio.run(run()))

    def test_resolution_error_other_than_gaierror_does_not_count_as_unconfigured(self):
        async def explodes(*args, **kwargs):
            raise OSError("resolver broken")

        async def run() -> bool:
            loop = asyncio.get_running_loop()
            original = loop.getaddrinfo
            loop.getaddrinfo = explodes  # type: ignore[method-assign]
            try:
                return await health_service._definitely_unresolvable("weird", 80)
            finally:
                loop.getaddrinfo = original  # type: ignore[method-assign]

        self.assertFalse(asyncio.run(run()))

    def test_gaierror_counts_as_unresolvable(self):
        async def nxdomain(*args, **kwargs):
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

        async def run() -> bool:
            loop = asyncio.get_running_loop()
            original = loop.getaddrinfo
            loop.getaddrinfo = nxdomain  # type: ignore[method-assign]
            try:
                return await health_service._definitely_unresolvable("ghost", 80)
            finally:
                loop.getaddrinfo = original  # type: ignore[method-assign]

        self.assertTrue(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
