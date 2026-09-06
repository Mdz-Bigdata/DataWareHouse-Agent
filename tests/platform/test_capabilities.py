import os
import unittest
from unittest.mock import patch

from platform_gateway.capabilities import CapabilityRegistry, Subsystem


class CapabilityRegistryTest(unittest.TestCase):
    def test_default_registry_exposes_all_four_applications(self) -> None:
        registry = CapabilityRegistry.from_environment()

        self.assertEqual(
            {item.slug for item in registry.all()},
            {"core", "data-api", "agents", "audio"},
        )
        self.assertEqual(registry.get("data-api").ui_url, "")
        self.assertEqual(registry.get("data-api").ui_port, 8020)
        self.assertEqual(registry.get("agents").ui_port, 8030)

    def test_environment_can_disable_optional_subsystem(self) -> None:
        with patch.dict(os.environ, {"PLATFORM_AUDIO_ENABLED": "false"}, clear=False):
            registry = CapabilityRegistry.from_environment()

        self.assertFalse(registry.get("audio").enabled)

    def test_duplicate_route_prefix_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "route prefix"):
            CapabilityRegistry(
                [
                    Subsystem("one", "One", "/same", "http://one"),
                    Subsystem("two", "Two", "/same", "http://two"),
                ]
            )

    def test_unknown_subsystem_is_rejected(self) -> None:
        registry = CapabilityRegistry.from_environment()

        with self.assertRaisesRegex(KeyError, "unknown subsystem"):
            registry.get("missing")

    def test_public_url_overrides_native_port(self):
        with patch.dict(os.environ, {"PLATFORM_AGENTS_UI_URL": "https://agents.example.com/"}):
            registry = CapabilityRegistry.from_environment()
        self.assertEqual(registry.get("agents").ui_url, "https://agents.example.com/")

    def test_unsafe_public_url_is_rejected(self):
        with patch.dict(os.environ, {"PLATFORM_AGENTS_UI_URL": "javascript:alert(1)"}):
            with self.assertRaisesRegex(ValueError, "HTTP"):
                CapabilityRegistry.from_environment()


if __name__ == "__main__":
    unittest.main()
