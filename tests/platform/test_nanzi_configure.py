import base64
import stat
import tempfile
import unittest
from pathlib import Path

from integrations.nanzi.configure import create_config


class ConfigureTests(unittest.TestCase):
    def test_creates_private_distinct_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.platform"
            self.assertTrue(create_config(path))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            values = dict(line.split("=", 1) for line in path.read_text().splitlines()
                          if line and not line.startswith("#"))
            self.assertNotEqual(values["DATA_API_ADMIN_API_KEY"], values["AGENTS_ADMIN_API_KEY"])
            self.assertNotEqual(values["WAREHOUSE_POSTGRES_PASSWORD"], values["PLATFORM_MYSQL_ROOT_PASSWORD"])
            self.assertEqual(values["WAREHOUSE_POSTGRES_DB"], "datawarehouse")
            for key in ("DATA_API_ENCRYPTION_KEY", "AGENTS_ENCRYPTION_KEY"):
                self.assertEqual(len(base64.urlsafe_b64decode(values[key])), 32)

    def test_never_rotates_existing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.platform"
            original = "# user's preserved configuration\nDATA_API_ADMIN_API_KEY=existing-secret\nexport PLATFORM_MYSQL_ROOT_PASSWORD='old-secret'\n"
            path.write_text(original)
            self.assertFalse(create_config(path))
            upgraded = path.read_text()
            self.assertTrue(upgraded.startswith(original))
            self.assertEqual(upgraded.count("DATA_API_ADMIN_API_KEY="), 1)
            self.assertEqual(upgraded.count("PLATFORM_MYSQL_ROOT_PASSWORD="), 1)
            self.assertIn("WAREHOUSE_POSTGRES_PASSWORD=", upgraded)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(create_config(path))
            self.assertEqual(path.read_text(), upgraded)

    def test_existing_empty_values_are_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.platform"
            path.write_text("WAREHOUSE_POSTGRES_PASSWORD=\n")
            create_config(path)
            self.assertTrue(path.read_text().startswith("WAREHOUSE_POSTGRES_PASSWORD=\n"))
            self.assertEqual(path.read_text().count("WAREHOUSE_POSTGRES_PASSWORD="), 1)
