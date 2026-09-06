import unittest

from platform_gateway.proxy import build_upstream_url, forwarded_headers


class ProxyRulesTest(unittest.TestCase):
    def test_build_upstream_url_preserves_path_and_query(self) -> None:
        value = build_upstream_url(
            "http://audio:8040/",
            "api/query",
            b"mode=stream&question=%E9%94%80%E5%94%AE",
        )

        self.assertEqual(
            value,
            "http://audio:8040/api/query?mode=stream&question=%E9%94%80%E5%94%AE",
        )

    def test_forwarded_headers_remove_hop_by_hop_and_platform_token(self) -> None:
        headers = forwarded_headers(
            {
                "connection": "keep-alive",
                "host": "localhost:8000",
                "x-platform-token": "secret",
                "authorization": "Bearer browser-token",
                "accept": "text/event-stream",
            },
            trace_id="trace-1",
            subsystem_token="internal-token",
        )

        self.assertNotIn("connection", headers)
        self.assertNotIn("host", headers)
        self.assertNotIn("x-platform-token", headers)
        self.assertEqual(headers["authorization"], "Bearer internal-token")
        self.assertEqual(headers["x-trace-id"], "trace-1")
        self.assertEqual(headers["accept"], "text/event-stream")


if __name__ == "__main__":
    unittest.main()

