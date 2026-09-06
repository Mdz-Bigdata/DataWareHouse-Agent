"""Seed import helpers."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ..config import SEEDS_DIR
from ..db import db
from ..insert_support import insert_dict_rows


class SeedImporter:
    """Imports crawler CSV seeds and resolves business codes to primary keys."""

    def __init__(self, seeds_dir: Path | None = None) -> None:
        self.seeds_dir = seeds_dir or SEEDS_DIR

    def load_csv(self, relative_path: str) -> list[dict[str, Any]]:
        path = self.seeds_dir / relative_path
        if not path.exists():
            raise FileNotFoundError(f"missing seed file: {path}")

        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                rows.append(
                    {
                        key: None if value in {"", "NULL"} else value
                        for key, value in row.items()
                    }
                )
        if not rows:
            raise ValueError(f"seed file is empty: {path}")
        return rows

    def insert_rows(self, table_name: str, rows: list[dict[str, Any]]) -> int:
        return insert_dict_rows(table_name, rows)

    def fetch_id_map(self, table_name: str, code_column: str) -> dict[str, int]:
        rows = db.fetch_all(f"SELECT id, `{code_column}` AS code FROM `{table_name}`")
        return {str(row["code"]): int(row["id"]) for row in rows}

    def import_dim_audio_category(self) -> int:
        source_rows = self.load_csv("1_foundation/dim_audio_category.csv")
        count = 0
        code_by_source = {
            str(row["source_category_id"]): str(row["category_code"])
            for row in source_rows
            if row.get("source_category_id")
        }
        code_to_id: dict[str, int] = {}

        for level in (1, 2, 3):
            rows: list[dict[str, Any]] = []
            for source in source_rows:
                if int(source["category_level"]) != level:
                    continue
                parent_source_id = source.get("parent_source_category_id")
                parent_code = code_by_source.get(str(parent_source_id))
                rows.append(
                    {
                        "parent_id": code_to_id.get(parent_code or ""),
                        "category_code": source["category_code"],
                        "category_name": source["category_name"],
                        "category_level": int(source["category_level"]),
                        "category_type": source["category_type"],
                        "sort_no": int(source["sort_no"] or 0),
                        "yn": int(source["yn"] or 1),
                    }
                )
            count += self.insert_rows("dim_audio_category", rows)
            code_to_id.update(self.fetch_id_map("dim_audio_category", "category_code"))
        return count

    def import_dim_content_tag(self) -> int:
        source_rows = self.load_csv("1_foundation/dim_content_tag.csv")
        count = 0
        code_to_id: dict[str, int] = {}
        for parent_only in (True, False):
            rows: list[dict[str, Any]] = []
            for source in source_rows:
                is_parent = source.get("parent_tag_code") is None
                if is_parent != parent_only:
                    continue
                rows.append(
                    {
                        "parent_id": code_to_id.get(str(source["parent_tag_code"])),
                        "tag_code": source["tag_code"],
                        "tag_name": source["tag_name"],
                        "tag_type": source["tag_type"],
                        "sort_no": int(source["sort_no"] or 0),
                        "yn": int(source["yn"] or 1),
                    }
                )
            count += self.insert_rows("dim_content_tag", rows)
            code_to_id.update(self.fetch_id_map("dim_content_tag", "tag_code"))
        return count

    def import_simple_table(
        self,
        table_name: str,
        relative_path: str,
        columns: tuple[str, ...],
        defaults: dict[str, Any] | None = None,
    ) -> int:
        defaults = defaults or {}
        rows: list[dict[str, Any]] = []
        for source in self.load_csv(relative_path):
            rows.append(
                {
                    column: source.get(column, defaults.get(column))
                    for column in columns
                }
            )
        return self.insert_rows(table_name, rows)

    def import_layer1_seeds(self) -> dict[str, int]:
        return {
            "dim_audio_category": self.import_dim_audio_category(),
            "dim_content_tag": self.import_dim_content_tag(),
            "dim_channel": self.import_simple_table(
                "dim_channel",
                "1_foundation/dim_channel.csv",
                ("channel_code", "channel_name", "channel_type", "yn"),
            ),
            "dim_language": self.import_simple_table(
                "dim_language",
                "1_foundation/dim_language.csv",
                ("language_code", "language_name", "sort_no", "yn"),
            ),
            "dim_currency": self.import_simple_table(
                "dim_currency",
                "1_foundation/dim_currency.csv",
                ("currency_code", "currency_name", "symbol", "precision_scale", "yn"),
            ),
            "content_organization": self.import_simple_table(
                "content_organization",
                "1_foundation/content_organization.csv",
                (
                    "organization_code",
                    "organization_name",
                    "organization_type",
                    "intro",
                    "yn",
                ),
                {"yn": 1},
            ),
            "content_author": self.import_simple_table(
                "content_author",
                "2_content/content_author.csv",
                ("author_code", "author_name", "author_type", "intro", "yn"),
                {"yn": 1},
            ),
        }
