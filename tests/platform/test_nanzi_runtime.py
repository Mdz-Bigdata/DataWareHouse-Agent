"""Exercise the integration boundary without importing either NanZi app."""

import asyncio
import os
from pathlib import Path
import runpy
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from integrations.nanzi.cookie_namespace import PlatformCookieMiddleware


class PlatformCookieMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_each_platform_receives_only_its_own_admin_cookie(self):
        for selected, expected in (
            ("nanzi_data_admin_token", b"data-token"),
            ("nanzi_agents_admin_token", b"agents-token"),
        ):
            with self.subTest(selected=selected):
                original = {
                    "type": "http",
                    "path": "/api/portal/auth/me",
                    "headers": [
                        (b"x-api-key", b"explicit-header-token"),
                        (
                            b"cookie",
                            b"theme=dark; admin_token=legacy; "
                            b"nanzi_data_admin_token=data-token; "
                            b"nanzi_agents_admin_token=agents-token",
                        ),
                    ],
                }
                seen = []

                async def upstream(scope, receive, send):
                    seen.append(scope)

                await PlatformCookieMiddleware(upstream, selected)(original, None, None)
                self.assertEqual(
                    seen[0]["headers"],
                    [
                        (b"x-api-key", b"explicit-header-token"),
                        (b"cookie", b"theme=dark; admin_token=" + expected),
                    ],
                )
                self.assertIsNot(seen[0], original)
                self.assertIn(b"legacy", original["headers"][1][1])

    async def test_missing_own_cookie_does_not_authenticate_with_other_platform(self):
        seen = []

        async def upstream(scope, receive, send):
            seen.extend(scope["headers"])

        await PlatformCookieMiddleware(upstream, "nanzi_data_admin_token")(
            {
                "type": "http",
                "headers": [
                    (b"cookie", b"admin_token=legacy; nanzi_agents_admin_token=other"),
                    (b"cookie", b"theme=light; locale=zh-CN"),
                ],
            },
            None,
            None,
        )
        self.assertEqual(seen, [(b"cookie", b"theme=light; locale=zh-CN")])

    async def test_repeated_cookie_headers_are_combined_for_upstream_parser(self):
        seen = []

        async def upstream(scope, receive, send):
            seen.extend(scope["headers"])

        await PlatformCookieMiddleware(upstream, "nanzi_data_admin_token")(
            {
                "type": "http",
                "headers": [
                    (b"cookie", b'note="a b"; session=abc=='),
                    (b"cookie", b"nanzi_data_admin_token=token=="),
                ],
            },
            None,
            None,
        )
        self.assertEqual(
            seen,
            [
                (b"cookie", b'note="a b"; session=abc==; admin_token=token=='),
            ],
        )

    async def test_malformed_quoted_cookie_cannot_hide_a_raw_admin_token(self):
        seen = []

        async def upstream(scope, receive, send):
            seen.extend(scope["headers"])

        await PlatformCookieMiddleware(upstream, "nanzi_data_admin_token")(
            {
                "type": "http",
                "headers": [(b"cookie", b'note="a;admin_token=legacy"; \xa0admin_token=hidden; locale=zh-CN')],
            },
            None,
            None,
        )
        self.assertEqual(seen, [(b"cookie", b'note="a; locale=zh-CN')])

    async def test_set_cookie_flags_deletion_and_multiple_headers_are_preserved(self):
        start = {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"set-cookie", b"admin_token=abc==; HttpOnly; Path=/; SameSite=lax; Secure"),
                (b"set-cookie", b"theme=dark; Path=/"),
                (
                    b"set-cookie",
                    b'admin_token=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/',
                ),
            ],
        }
        sent = []

        async def upstream(scope, receive, send):
            await send(start)

        async def capture(message):
            sent.append(message)

        await PlatformCookieMiddleware(upstream, "nanzi_agents_admin_token")(
            {"type": "http", "headers": []}, None, capture
        )
        self.assertEqual(
            sent[0]["headers"],
            [
                (b"content-type", b"application/json"),
                (b"set-cookie", b"nanzi_agents_admin_token=abc==; HttpOnly; Path=/; SameSite=lax; Secure"),
                (b"set-cookie", b"theme=dark; Path=/"),
                (
                    b"set-cookie",
                    b'nanzi_agents_admin_token=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/',
                ),
            ],
        )
        self.assertEqual(start["headers"][1][1].split(b"=", 1)[0], b"admin_token")

    async def test_sse_chunks_are_forwarded_immediately_without_reading_request(self):
        first_chunk = asyncio.Event()
        finish_stream = asyncio.Event()
        body = {"type": "http.response.body", "body": b"data: first\n\n", "more_body": True}
        final = {"type": "http.response.body", "body": b"data: done\n\n", "more_body": False}
        received = []

        async def receive():
            raise AssertionError("Middleware must not read the request body")

        async def upstream(scope, upstream_receive, send):
            self.assertIs(upstream_receive, receive)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(body)
            await finish_stream.wait()
            await send(final)

        async def capture(message):
            received.append(message)
            if message is body:
                first_chunk.set()

        task = asyncio.create_task(
            PlatformCookieMiddleware(upstream, "nanzi_data_admin_token")(
                {"type": "http", "headers": []}, receive, capture
            )
        )
        try:
            await asyncio.wait_for(first_chunk.wait(), timeout=1)
            self.assertFalse(task.done())
            self.assertIs(received[1], body)
        finally:
            finish_stream.set()
            await asyncio.wait_for(task, timeout=1)
        self.assertIs(received[2], final)

    async def test_websocket_scope_handshake_and_frames_are_preserved(self):
        scope = {
            "type": "websocket",
            "path": "/api/ws",
            "subprotocols": ["chat"],
            "headers": [(b"cookie", b"nanzi_agents_admin_token=agent")],
        }
        frame = {"type": "websocket.send", "text": "unchanged"}
        sent = []

        async def upstream(mapped_scope, receive, send):
            self.assertEqual(mapped_scope["subprotocols"], ["chat"])
            self.assertEqual(mapped_scope["path"], scope["path"])
            self.assertEqual(mapped_scope["headers"], [(b"cookie", b"admin_token=agent")])
            await send({"type": "websocket.accept", "headers": [(b"set-cookie", b"admin_token=new; Path=/")]})
            await send(frame)

        async def capture(message):
            sent.append(message)

        await PlatformCookieMiddleware(upstream, "nanzi_agents_admin_token")(scope, None, capture)
        self.assertEqual(sent[0]["headers"], [(b"set-cookie", b"nanzi_agents_admin_token=new; Path=/")])
        self.assertIs(sent[1], frame)

    async def test_websocket_http_denial_cookie_is_namespaced(self):
        sent = []

        async def upstream(scope, receive, send):
            await send({
                "type": "websocket.http.response.start",
                "status": 401,
                "headers": [(b"set-cookie", b"admin_token=; Max-Age=0; Path=/")],
            })

        async def capture(message):
            sent.append(message)

        await PlatformCookieMiddleware(upstream, "nanzi_agents_admin_token")(
            {"type": "websocket", "headers": []}, None, capture
        )
        self.assertEqual(sent[0]["headers"], [(b"set-cookie", b"nanzi_agents_admin_token=; Max-Age=0; Path=/")])

    async def test_lifespan_is_passed_through_without_wrapping(self):
        original = {"type": "lifespan", "state": {"startup": True}}
        receive = object()
        send = object()

        async def upstream(scope, upstream_receive, upstream_send):
            self.assertIs(scope, original)
            self.assertIs(upstream_receive, receive)
            self.assertIs(upstream_send, send)

        await PlatformCookieMiddleware(upstream, "nanzi_data_admin_token")(original, receive, send)

    def test_unsafe_or_unnamespaced_cookie_names_fail_at_configuration_time(self):
        for name in ("", "admin_token", "bad name", "bad;name", "bad=name", "bad\r\nname", "非ASCII"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                PlatformCookieMiddleware(None, name)


class RuntimeEntrypointTest(unittest.TestCase):
    def test_container_entrypoint_wraps_the_upstream_app_with_environment_name(self):
        integration = Path(__file__).resolve().parents[2] / "integrations" / "nanzi"
        upstream_module = ModuleType("app.main")
        upstream_module.app = object()
        with (
            patch.dict(sys.modules, {"app": ModuleType("app"), "app.main": upstream_module}),
            patch.dict(os.environ, {"PLATFORM_COOKIE_NAME": "nanzi_data_admin_token"}),
            patch.object(sys, "path", [str(integration), *sys.path]),
        ):
            entry = runpy.run_path(str(integration / "app_entry.py"))
        self.assertIs(entry["app"].app, upstream_module.app)
        self.assertEqual(entry["app"].cookie_name, b"nanzi_data_admin_token")


if __name__ == "__main__":
    unittest.main()
