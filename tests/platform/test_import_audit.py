import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.audit_imports import verify_manifest


class ImportAuditTest(unittest.TestCase):
    def test_manifest_accepts_unchanged_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("print('ok')\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            manifest = root / "manifest.sha256"
            manifest.write_text(f"{digest}  app.py\n", encoding="utf-8")

            self.assertEqual(verify_manifest(root, manifest), [])

    def test_manifest_reports_modified_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("changed\n", encoding="utf-8")
            manifest = root / "manifest.sha256"
            manifest.write_text(f"{'0' * 64}  app.py\n{'1' * 64}  missing.py\n", encoding="utf-8")

            failures = verify_manifest(root, manifest)

            self.assertEqual(failures, ["modified: app.py", "missing: missing.py"])


if __name__ == "__main__":
    unittest.main()

