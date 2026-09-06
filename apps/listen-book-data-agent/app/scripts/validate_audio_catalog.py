from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path

from app.metadata.schema_catalog import (
    load_meta_config,
    parse_mysql_ddl,
    validate_domain_catalog,
)

PROJECT_ROOT = Path(__file__).parents[2]


def main() -> None:
    parser = ArgumentParser(description="Validate audiobook domain metadata")
    parser.add_argument(
        "--ddl",
        type=Path,
        default=PROJECT_ROOT / "tools" / "audio_data" / "sql" / "audio.sql",
    )
    parser.add_argument(
        "--semantics",
        type=Path,
        default=PROJECT_ROOT / "conf" / "domains" / "audio" / "semantics.yaml",
    )
    parser.add_argument(
        "--relationships",
        type=Path,
        default=PROJECT_ROOT / "conf" / "domains" / "audio" / "relationships.yaml",
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=PROJECT_ROOT / "conf" / "domains" / "audio" / "metrics.yaml",
    )
    args = parser.parse_args()

    physical = parse_mysql_ddl(args.ddl)
    config = load_meta_config(args.semantics, args.relationships, args.metrics)
    validation = validate_domain_catalog(physical, config)
    print(
        "audio catalog valid: "
        f"tables={validation.table_count}, "
        f"columns={validation.column_count}, "
        f"physical_relationships={validation.physical_relationship_count}, "
        f"virtual_relationships={validation.virtual_relationship_count}, "
        f"sensitive_columns={validation.sensitive_column_count}, "
        f"metrics={validation.metric_count}"
    )


if __name__ == "__main__":
    main()
