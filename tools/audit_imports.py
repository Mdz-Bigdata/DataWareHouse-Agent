from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def verify_manifest(root: Path, manifest: Path) -> list[str]:
    failures: list[str] = []
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        digest, relative_path = raw_line.split("  ", 1)
        target = root / relative_path
        if not target.is_file():
            failures.append(f"missing: {relative_path}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            failures.append(f"modified: {relative_path}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify imported source snapshots")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    apps_root = repo_root / "apps"
    provenance_root = repo_root / "provenance"
    failures: list[str] = []
    for manifest in sorted(provenance_root.glob("*.sha256")):
        app_name = manifest.stem
        for failure in verify_manifest(apps_root / app_name, manifest):
            failures.append(f"{app_name}: {failure}")
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(f"verified {len(list(provenance_root.glob('*.sha256')))} imported snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

