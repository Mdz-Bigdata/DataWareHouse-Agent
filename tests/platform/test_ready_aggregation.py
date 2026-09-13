"""网关 /api/platform/ready 的聚合口径。

子系统把「按设计未启用的可选依赖」标成 not_configured 时：不计入 degraded，
但必须如实列出来。子系统真挂了（503 / 连不上）仍然照旧拉低整体状态。
"""

import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from platform_gateway import main
from platform_gateway.capabilities import CapabilityRegistry, Subsystem


AUDIO = Subsystem(
    "audio", "Audio", "/platform/audio", "http://audio:8000", health_path="/ready"
)
CORE = Subsystem("core", "Core", "/platform/core", "http://core:8000")


def json_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def ready_payload(subsystems, responder) -> tuple[int, dict]:
    transport = main.create_http_client(transport=httpx.MockTransport(responder))
    registry = CapabilityRegistry(subsystems)
    with patch.object(main, "registry", registry), patch.object(
        main, "create_http_client", return_value=transport
    ):
        with TestClient(main.app) as client:
            response = client.get("/api/platform/ready")
    return response.status_code, response.json()


class ReadyAggregationTests(unittest.TestCase):
    def test_optional_dependency_not_enabled_does_not_degrade_the_platform(self):
        def upstream(request):
            if request.url.host == "audio":
                return json_response(
                    200,
                    {
                        "status": "ready",
                        "dependencies": {
                            "metadata_mysql": {"status": "ok"},
                            "embedding": {
                                "status": "not_configured",
                                "detail": "dns_unresolved",
                                "target": "embedding:80",
                            },
                        },
                    },
                )
            return json_response(200, {"status": "ok"})

        status_code, payload = ready_payload([CORE, AUDIO], upstream)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["status"], "ready")
        # 未启用的可选依赖如实列出，而不是被藏起来。
        self.assertEqual(
            payload["not_configured"],
            [
                {
                    "subsystem": "audio",
                    "dependency": "embedding",
                    "detail": "dns_unresolved",
                    "target": "embedding:80",
                }
            ],
        )
        audio = next(item for item in payload["subsystems"] if item["slug"] == "audio")
        self.assertTrue(audio["ready"])
        self.assertEqual(audio["not_configured"], ["embedding"])

    def test_real_dependency_failure_still_degrades_and_returns_503(self):
        """★反例★：embedding 真配上却连不通时，子系统报 503，网关必须 degraded。"""

        def upstream(request):
            if request.url.host == "audio":
                return json_response(
                    503,
                    {
                        "status": "unavailable",
                        "dependencies": {
                            "embedding": {
                                "status": "error",
                                "detail": "ClientConnectorDNSError",
                                "target": "nope.invalid:80",
                            }
                        },
                    },
                )
            return json_response(200, {"status": "ok"})

        status_code, payload = ready_payload([CORE, AUDIO], upstream)

        self.assertEqual(status_code, 503)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["not_configured"], [])
        audio = next(item for item in payload["subsystems"] if item["slug"] == "audio")
        self.assertFalse(audio["ready"])
        self.assertEqual(audio["status_code"], 503)

    def test_unreachable_subsystem_still_degrades(self):
        def upstream(request):
            raise httpx.ConnectError("no route", request=request)

        status_code, payload = ready_payload([CORE], upstream)

        self.assertEqual(status_code, 503)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["subsystems"][0]["error"], "ConnectError")

    def test_optional_dependencies_are_listed_even_when_the_subsystem_is_down(self):
        def upstream(request):
            return json_response(
                503,
                {
                    "status": "unavailable",
                    "dependencies": {
                        "metadata_mysql": {"status": "error", "detail": "OperationalError"},
                        "embedding": {"status": "not_configured", "detail": "disabled_by_config"},
                    },
                },
            )

        status_code, payload = ready_payload([AUDIO], upstream)

        self.assertEqual(status_code, 503)
        self.assertEqual(payload["not_configured"][0]["dependency"], "embedding")

    def test_disabled_subsystem_is_not_probed_and_does_not_degrade(self):
        probed = []

        def upstream(request):
            probed.append(str(request.url))
            return json_response(200, {"status": "ok"})

        disabled = Subsystem(
            "audio", "Audio", "/platform/audio", "http://audio:8000", enabled=False
        )
        status_code, payload = ready_payload([CORE, disabled], upstream)

        self.assertEqual(status_code, 200)
        self.assertEqual(probed, ["http://core:8000/health"])

    def test_non_json_and_oversized_health_bodies_are_ignored(self):
        def text_upstream(request):
            return httpx.Response(200, text="OK", headers={"content-type": "text/plain"})

        status_code, payload = ready_payload([CORE], text_upstream)
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["not_configured"], [])

        def huge_upstream(request):
            filler = {
                f"dep{index}": {"status": "not_configured"}
                for index in range(main.MAX_HEALTH_BODY_BYTES // 30)
            }
            return json_response(200, {"dependencies": filler})

        status_code, payload = ready_payload([CORE], huge_upstream)
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["not_configured"], [])

    def test_malformed_dependency_entries_are_ignored(self):
        def upstream(request):
            return json_response(
                200,
                {"dependencies": {"a": "not-a-dict", "b": {"status": "ok"}, "c": None}},
            )

        status_code, payload = ready_payload([CORE], upstream)

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["not_configured"], [])

    def test_upstream_strings_are_truncated_before_being_echoed(self):
        def upstream(request):
            return json_response(
                200,
                {
                    "dependencies": {
                        "x" * 500: {"status": "not_configured", "detail": "y" * 500}
                    }
                },
            )

        _, payload = ready_payload([CORE], upstream)

        entry = payload["not_configured"][0]
        self.assertEqual(len(entry["dependency"]), 120)
        self.assertEqual(len(entry["detail"]), 120)


if __name__ == "__main__":
    unittest.main()
