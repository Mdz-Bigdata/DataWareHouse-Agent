"""Container entrypoint; /integration and /app must both be on PYTHONPATH."""

import os

from app.main import app as upstream_app
from cookie_namespace import PlatformCookieMiddleware


app = PlatformCookieMiddleware(upstream_app, os.environ["PLATFORM_COOKIE_NAME"])
