from __future__ import annotations

from collections.abc import Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy


class RejectResponseCookies(DefaultCookiePolicy):
    """A shared transport must never retain one browser's upstream login."""

    def set_ok(self, cookie, request) -> bool:
        return False

    def return_ok(self, cookie, request) -> bool:
        return False


def stateless_cookie_jar() -> CookieJar:
    return CookieJar(policy=RejectResponseCookies())


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "x-platform-token",
}


def build_upstream_url(base_url: str, path: str, query_string: bytes = b"") -> str:
    base = base_url.rstrip("/")
    clean_path = path.lstrip("/")
    url = f"{base}/{clean_path}" if clean_path else f"{base}/"
    if query_string:
        url = f"{url}?{query_string.decode('ascii')}"
    return url


def forwarded_headers(
    incoming: Mapping[str, str],
    *,
    trace_id: str,
    subsystem_token: str | None = None,
) -> dict[str, str]:
    headers = {
        name.lower(): value
        for name, value in incoming.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
    }
    headers["x-trace-id"] = trace_id
    if subsystem_token:
        headers["authorization"] = f"Bearer {subsystem_token}"
    return headers
