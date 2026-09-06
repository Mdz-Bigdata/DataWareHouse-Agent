#!/usr/bin/env python3
"""Initialize the isolated PostgreSQL warehouse using WAREHOUSE_DATABASE_URL."""
from pathlib import Path
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.service.warehouse_migration import WarehouseMigrationError, migrate_url  # noqa: E402


def main() -> int:
    database_url = os.environ.get("WAREHOUSE_DATABASE_URL", "")
    if not database_url:
        print("PostgreSQL 数仓迁移缺少 WAREHOUSE_DATABASE_URL。", file=sys.stderr)
        return 1
    try:
        result = migrate_url(database_url)
    except WarehouseMigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # SQLAlchemy errors contain credentials/query values; never echo them.
        print("PostgreSQL 数仓迁移失败，事务已回滚；请检查数据库可用性及迁移配置。", file=sys.stderr)
        return 1
    print("PostgreSQL warehouse: " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
