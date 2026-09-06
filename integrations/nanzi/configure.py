"""Create private, persistent integration credentials without changing existing ones."""
from __future__ import annotations

import argparse
import base64
import fcntl
import os
from pathlib import Path
import re
import secrets


def create_config(path: Path) -> bool:
    password = secrets.token_urlsafe(32)
    fields = {
        "PLATFORM_MYSQL_ROOT_PASSWORD": password,
        "DATA_API_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "AGENTS_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "DATA_API_ADMIN_API_KEY": "sk-" + secrets.token_urlsafe(32),
        "AGENTS_ADMIN_API_KEY": "sk-" + secrets.token_urlsafe(32),
        "PLATFORM_DATA_API_UI_URL": "",
        "PLATFORM_AGENTS_UI_URL": "",
        "AGENTS_PUBLIC_URL": "http://localhost:8030",
        "WAREHOUSE_POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "WAREHOUSE_POSTGRES_USER": "warehouse",
        "WAREHOUSE_POSTGRES_DB": "datawarehouse",
        "WAREHOUSE_POSTGRES_PORT": "55432",
    }
    header = (
        "# Private platform configuration. Keep this file with the database volumes.\n"
        "# Admin username: admin. Use the respective *_ADMIN_API_KEY to sign in.\n"
        "# Empty UI URLs open the current browser hostname on ports 8020 / 8030.\n"
    )
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(path, os.O_RDWR)
        created = False
    with os.fdopen(fd, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        os.fchmod(handle.fileno(), 0o600)
        content = handle.read()
        existing = set(re.findall(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", content, re.MULTILINE))
        missing = {name: value for name, value in fields.items() if name not in existing}
        if missing:
            handle.seek(0, os.SEEK_END)
            handle.write(header if created else ("\n" if content and not content.endswith("\n") else ""))
            handle.write("".join(f"{name}={value}\n" for name, value in missing.items()))
            handle.flush()
            os.fsync(handle.fileno())
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".env.platform"))
    args = parser.parse_args()
    created = create_config(args.output)
    print(f"{'Created' if created else 'Preserved existing values and supplied missing defaults in'} private configuration: {args.output}")


if __name__ == "__main__":
    main()
