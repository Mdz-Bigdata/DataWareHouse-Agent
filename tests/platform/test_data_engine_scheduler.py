import unittest

import httpx

from integrations.data_engine.scheduler import AgentSchedulerBridge


class AgentSchedulerBridgeTests(unittest.TestCase):
    def test_submit_creates_a_real_agent_platform_task(self) -> None:
        requests = []

        def upstream(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"data": {"id": 23, "status": 1}})

        client = httpx.Client(transport=httpx.MockTransport(upstream))
        bridge = AgentSchedulerBridge(
            "http://agents:8001/api/v1/tasks/", "sk-private", client=client
        )

        result = bridge.submit({
            "name": "每日 GMV",
            "agent_id": "data-assistant",
            "cron_expr": "0 8 * * *",
            "prompt": "查询昨日 GMV 并告警",
            "dependencies": ["dwd_ord_pay_di"],
            "alert_channels": ["inbox"],
            "idempotency_key": "gmv-daily-v1",
        })

        self.assertTrue(result["ok"])
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["task"]["id"], 23)
        self.assertEqual(requests[0].headers["x-api-key"], "sk-private")
        payload = __import__("json").loads(requests[1].content)
        self.assertEqual(payload["config"]["data_engine_idempotency_key"], "gmv-daily-v1")
        self.assertEqual(payload["config"]["dependencies"], ["dwd_ord_pay_di"])

    def test_submit_is_idempotent_when_task_already_exists(self) -> None:
        def upstream(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, json={"data": [{
                "id": 7,
                "config": {"data_engine_idempotency_key": "same-key"},
            }]})

        bridge = AgentSchedulerBridge(
            "http://agents:8001/api/v1/tasks/",
            "sk-private",
            client=httpx.Client(transport=httpx.MockTransport(upstream)),
        )

        result = bridge.submit({
            "name": "每日 GMV",
            "agent_id": "data-assistant",
            "cron_expr": "0 8 * * *",
            "prompt": "查询昨日 GMV",
            "idempotency_key": "same-key",
        })

        self.assertTrue(result["ok"])
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["task"]["id"], 7)

    def test_submit_requires_executable_task_fields(self) -> None:
        bridge = AgentSchedulerBridge(
            "http://agents:8001/api/v1/tasks/", "sk-private"
        )

        with self.assertRaisesRegex(ValueError, "agent_id"):
            bridge.submit({"name": "incomplete", "cron_expr": "0 8 * * *", "prompt": "run"})


if __name__ == "__main__":
    unittest.main()
