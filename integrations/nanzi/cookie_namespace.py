"""Keep the two upstream admin sessions separate on a shared hostname.

Browsers share cookies across ports. Each NanZi container therefore exposes a
different cookie name, while its untouched upstream app still sees admin_token.
The boundary only rewrites headers and never consumes or buffers body messages.
"""

from collections.abc import Awaitable, Callable
import re
from typing import Any


Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_COOKIE_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", flags=re.ASCII)
_ADMIN_COOKIES = {b"admin_token", b"nanzi_data_admin_token", b"nanzi_agents_admin_token"}
_HEADER_MESSAGES = {"http.response.start", "websocket.accept", "websocket.http.response.start"}


class PlatformCookieMiddleware:
    def __init__(self, app: ASGIApp, cookie_name: str) -> None:
        if cookie_name == "admin_token" or not _COOKIE_NAME.fullmatch(cookie_name):
            raise ValueError("PLATFORM_COOKIE_NAME must be a valid, distinct cookie name")
        self.app = app
        self.cookie_name = cookie_name.encode("ascii")
        self._admin_cookies = _ADMIN_COOKIES | {self.cookie_name}

    def _request_cookie(self, value: bytes) -> bytes:
        cookies = []
        # Match Starlette's parser: a semicolon is a separator even inside a
        # malformed quoted value, where it could otherwise hide admin_token.
        for part in value.split(b";"):
            part = part.strip()
            name, separator, token = part.partition(b"=")
            # ASGI header bytes become latin-1 strings in Starlette. Match its
            # whitespace handling as well as its cookie separators.
            name = name.decode("latin-1").strip().encode("latin-1")
            if name == self.cookie_name and separator:
                cookies.append(b"admin_token=" + token)
            elif name not in self._admin_cookies and part:
                cookies.append(part)
        return b"; ".join(cookies)

    def _response_cookie(self, value: bytes) -> bytes:
        name, separator, rest = value.partition(b"=")
        if name.strip() == b"admin_token" and separator:
            return self.cookie_name + b"=" + rest
        return value

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = []
        cookies = []
        for name, value in scope.get("headers", []):
            if name.lower() == b"cookie":
                value = self._request_cookie(value)
                if value:
                    cookies.append(value)
                continue
            headers.append((name, value))
        if cookies:
            # HTTP/2 may split Cookie across fields; Starlette reads only one.
            headers.append((b"cookie", b"; ".join(cookies)))
        upstream_scope = {**scope, "headers": headers}

        async def send_namespaced(message: Message) -> None:
            if message["type"] in _HEADER_MESSAGES and "headers" in message:
                message = {
                    **message,
                    "headers": [
                        (name, self._response_cookie(value) if name.lower() == b"set-cookie" else value)
                        for name, value in message["headers"]
                    ],
                }
            await send(message)

        await self.app(upstream_scope, receive, send_namespaced)
