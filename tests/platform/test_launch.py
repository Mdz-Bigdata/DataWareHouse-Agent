import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from platform_gateway import main
from platform_gateway.capabilities import CapabilityRegistry


class PlatformLaunchTests(unittest.TestCase):
    def request(self, path, environment=None, origin="http://warehouse.example:3000", headers=None):
        with patch.dict(os.environ, environment or {}, clear=True):
            registry = CapabilityRegistry.from_environment()
        with patch.object(main, "registry", registry), TestClient(main.app, base_url=origin) as client:
            return client.get(path, follow_redirects=False, headers=headers)

    def test_both_platforms_open_native_ui_on_current_host(self):
        for slug, port in (("data-api", 8020), ("agents", 8030)):
            for suffix in ("", "/"):
                with self.subTest(slug=slug, suffix=suffix):
                    response = self.request(f"/platform/{slug}{suffix}")
                    self.assertEqual(response.status_code, 307)
                    self.assertEqual(response.headers["location"], f"http://warehouse.example:{port}/")

    def test_ipv6_host_is_preserved(self):
        response = self.request("/platform/agents", headers={"host": "[::1]:5173"})
        self.assertEqual(response.headers["location"], "http://[::1]:8030/")

    def test_explicit_public_url_is_used(self):
        response = self.request("/platform/agents", {"PLATFORM_AGENTS_UI_URL": "https://agents.example.com/login"})
        self.assertEqual(response.headers["location"], "https://agents.example.com/login")

    def test_disabled_platform_does_not_launch(self):
        response = self.request("/platform/agents", {"PLATFORM_AGENTS_ENABLED": "false"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("location", response.headers)

    def test_unknown_platform_is_not_redirected(self):
        self.assertEqual(self.request("/platform/unknown").status_code, 404)
