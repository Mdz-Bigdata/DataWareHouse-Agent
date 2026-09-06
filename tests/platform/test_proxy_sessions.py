import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from platform_gateway import main
from platform_gateway.capabilities import CapabilityRegistry, Subsystem


class ResponseStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"ok": true}'


class ProxySessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_transport_does_not_reuse_another_browsers_cookie(self):
        received = []

        def upstream(request):
            received.append(request.headers.get("cookie"))
            return httpx.Response(200, headers={"set-cookie": "nanzi_data_admin_token=alice; Path=/"})

        async with main.create_http_client(transport=httpx.MockTransport(upstream)) as client:
            await client.post("http://data-api/login", headers={"cookie": "nanzi_data_admin_token=alice"})
            await client.get("http://data-api/private")
            await client.get("http://data-api/private", headers={"cookie": "nanzi_data_admin_token=bob"})
            self.assertEqual(len(client.cookies), 0)
        self.assertEqual(received, ["nanzi_data_admin_token=alice", None, "nanzi_data_admin_token=bob"])

    async def test_concurrent_users_only_forward_their_own_cookie(self):
        import asyncio
        received = {}

        async def upstream(request):
            received[request.url.path] = request.headers.get("cookie")
            await asyncio.sleep(0)
            return httpx.Response(200, headers={"set-cookie": "nanzi_data_admin_token=alice; Path=/"})

        async with main.create_http_client(transport=httpx.MockTransport(upstream)) as client:
            await asyncio.gather(
                client.get("http://data-api/alice", headers={"cookie": "nanzi_data_admin_token=alice"}),
                client.get("http://data-api/bob", headers={"cookie": "nanzi_data_admin_token=bob"}),
            )
            await client.get("http://data-api/anonymous")
        self.assertEqual(received, {"/alice": "nanzi_data_admin_token=alice",
                                    "/bob": "nanzi_data_admin_token=bob", "/anonymous": None})


class ProxyResponseTests(unittest.TestCase):
    def test_proxy_preserves_independent_cookie_headers(self):
        cookies = ["nanzi_data_admin_token=alice; Path=/; HttpOnly", "theme=dark; Path=/"]

        def upstream(request):
            return httpx.Response(200, headers=[("set-cookie", value) for value in cookies], stream=ResponseStream())

        transport = main.create_http_client(transport=httpx.MockTransport(upstream))
        registry = CapabilityRegistry([Subsystem("data-api", "Data", "/platform/data-api", "http://data-api")])
        with patch.object(main, "registry", registry), patch.object(main, "create_http_client", return_value=transport):
            with TestClient(main.app) as client:
                response = client.post("/platform/data-api/api/portal/auth/login")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get_list("set-cookie"), cookies)
        self.assertEqual(response.json(), {"ok": True})
