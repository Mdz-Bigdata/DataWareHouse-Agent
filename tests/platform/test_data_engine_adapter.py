import types
import unittest

from fastapi.testclient import TestClient

from integrations.data_engine.app import create_app
from integrations.data_engine.runtime import DataEngineRuntime, TOOL_NAMES


class DataEngineRuntimeTests(unittest.TestCase):
    def fake_module(self):
        module = types.SimpleNamespace()
        for name in TOOL_NAMES:
            def tool(name=name, **arguments):
                return {"called": name, "arguments": arguments}

            tool.__name__ = name
            tool.__doc__ = f"{name} documentation"
            setattr(module, name, tool)
        return module

    def test_all_twenty_native_mcp_tools_are_exposed(self) -> None:
        runtime = DataEngineRuntime(self.fake_module(), allow_mutations=False)

        self.assertEqual(len(runtime.tools()), 20)
        self.assertEqual({item.name for item in runtime.tools()}, set(TOOL_NAMES))

    def test_unknown_tool_is_rejected(self) -> None:
        runtime = DataEngineRuntime(self.fake_module(), allow_mutations=False)

        with self.assertRaisesRegex(KeyError, "unknown data-engine tool"):
            runtime.invoke("not_a_tool", {})

    def test_http_mutation_is_disabled_by_default(self) -> None:
        runtime = DataEngineRuntime(self.fake_module(), allow_mutations=False)

        with self.assertRaisesRegex(PermissionError, "disabled"):
            runtime.invoke("ontology_register", {"kind": "object", "entry": {}})

    def test_scheduler_can_be_completed_by_an_integration_override(self) -> None:
        runtime = DataEngineRuntime(
            self.fake_module(),
            allow_mutations=True,
            overrides={"scheduler_submit": lambda task_spec: {"ok": True, "task": task_spec}},
        )

        result = runtime.invoke("scheduler_submit", {"task_spec": {"name": "daily"}})

        self.assertEqual(result, {"ok": True, "task": {"name": "daily"}})


class DataEngineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        module = types.SimpleNamespace()
        for name in TOOL_NAMES:
            def tool(name=name, **arguments):
                return {"called": name, "arguments": arguments}

            tool.__name__ = name
            tool.__doc__ = f"{name} documentation"
            setattr(module, name, tool)
        runtime = DataEngineRuntime(module, allow_mutations=False)
        self.client = TestClient(create_app(runtime, service_token="test-service-token"))

    def test_health_is_available_without_service_token(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tools"], 20)

    def test_health_never_exposes_the_native_database_dsn(self) -> None:
        module = types.SimpleNamespace()
        for name in TOOL_NAMES:
            def tool(name=name, **arguments):
                return {"called": name, "arguments": arguments}

            setattr(module, name, tool)
        module.health_check = lambda: {
            "ok": True,
            "dialect": "mysql",
            "ontology": {"objects": 4},
            "db": {
                "dsn": "mysql://warehouse:super-secret@database.internal/prod",
                "configured_remote": {"mysql": True},
            },
            "checks": [{"name": "db", "ok": True}],
        }
        client = TestClient(create_app(
            DataEngineRuntime(module, allow_mutations=False),
            service_token="test-service-token",
        ))

        response = client.get("/health")
        tool_response = client.post(
            "/api/tools/health_check",
            headers={"Authorization": "Bearer test-service-token"},
            json={"arguments": {}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("super-secret", response.text)
        self.assertNotIn("dsn", response.json()["native"]["db"])
        self.assertEqual(response.json()["native"]["db"]["configured_remote"], {"mysql": True})
        self.assertEqual(tool_response.status_code, 200)
        self.assertNotIn("super-secret", tool_response.text)
        self.assertNotIn("dsn", tool_response.json()["result"]["db"])

    def test_tool_catalog_requires_service_token(self) -> None:
        self.assertIn(self.client.get("/api/tools").status_code, {401, 403})

    def test_authorized_tool_call_invokes_native_function(self) -> None:
        response = self.client.post(
            "/api/tools/intent_classify",
            headers={"Authorization": "Bearer test-service-token"},
            json={"arguments": {"text": "昨天 GMV"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["called"], "intent_classify")
        self.assertEqual(response.json()["result"]["arguments"], {"text": "昨天 GMV"})


if __name__ == "__main__":
    unittest.main()
