"""Layer1: dimensions and main subjects."""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from ..config import GENERATION_DEFAULTS, LAYERS
from ..db import db
from .base import BaseGenerator
from .common import fetch_all, to_int
from .seed_importer import SeedImporter
from .validations import validate_layer1

FIRST_NAMES = (
    "赵",
    "钱",
    "孙",
    "李",
    "周",
    "吴",
    "郑",
    "王",
    "冯",
    "陈",
    "刘",
    "杨",
    "黄",
    "林",
    "何",
    "高",
    "郭",
    "马",
    "罗",
    "梁",
)
GIVEN_NAMES = (
    "明",
    "华",
    "芳",
    "娜",
    "静",
    "磊",
    "洋",
    "敏",
    "强",
    "丽",
    "杰",
    "婷",
    "宇",
    "欣",
    "晨",
    "然",
    "安",
    "宁",
    "乐",
    "悦",
)


class Layer1Generator(BaseGenerator):
    layer = 1
    layer_name = "基础维度与主体主数据"

    def __init__(self) -> None:
        self.random = random.Random(int(GENERATION_DEFAULTS["seed"]))
        self.now = self.local_now()
        self.start_at = self.now - timedelta(days=900)

    def run(self) -> None:
        self.header()
        self.clear_layer_tables()

        counts = {table: 0 for table in LAYERS[self.layer]["tables"]}
        counts.update(SeedImporter().import_layer1_seeds())
        self.stamp_seed_tables()
        counts["user_account"] = self.generate_users()
        counts["content_narrator"] = self.import_narrators()

        self.log_table_counts(counts)
        for check in validate_layer1():
            self.log(f"  [OK] validation: {check}")

    def name(self) -> str:
        return (
            f"{self.random.choice(FIRST_NAMES)}"
            f"{self.random.choice(GIVEN_NAMES)}"
            f"{self.random.choice(GIVEN_NAMES)}"
        )

    def generate_users(self) -> int:
        channels = fetch_all("dim_channel", "id")
        total = int(GENERATION_DEFAULTS["users"])
        user_start_at = self.now - timedelta(days=1460)
        user_end_at = self.now - timedelta(days=30)

        def rows():
            for index in range(1, total + 1):
                created_at = self.random_datetime(user_start_at, user_end_at)
                last_login_at = (
                    self.random_datetime(created_at, self.now)
                    if self.random.random() < 0.86
                    else None
                )
                yield {
                    "user_no": f"USR{index:010d}",
                    "nickname": f"听友{index:06d}",
                    "avatar_url": f"https://cdn.example.com/audio/avatar/{index % 2000:04d}.png",
                    "mobile": f"13{index:09d}" if index <= total * 0.8 else None,
                    "email": f"user{index:06d}@example.com"
                    if index <= total * 0.35
                    else None,
                    "register_channel_id": channels[index % len(channels)]["id"],
                    "account_status": self.random.choices(
                        ("normal", "muted", "disabled", "cancelled"),
                        weights=(96, 2, 1, 1),
                    )[0],
                    "last_login_at": last_login_at,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }

        return self.stream_rows(
            "user_account",
            rows(),
            total_rows=total,
            build_step_name="build user_account",
        )

    def stamp_seed_tables(self) -> None:
        dimension_start_at = self.now - timedelta(days=1460)
        dimension_end_at = self.now - timedelta(days=365)
        subject_start_at = self.now - timedelta(days=1460)
        subject_end_at = self.now - timedelta(days=30)
        for table in (
            "dim_audio_category",
            "dim_content_tag",
            "dim_channel",
            "dim_language",
            "dim_currency",
        ):
            self.stamp_table_rows(table, dimension_start_at, dimension_end_at)
        for table in ("content_organization", "content_author"):
            self.stamp_table_rows(table, subject_start_at, subject_end_at)

    def stamp_table_rows(self, table_name: str, start_at, end_at) -> None:
        rows = fetch_all(table_name, "id")
        params: list[tuple[Any, ...]] = []
        for row in rows:
            created_at = self.random_datetime(start_at, end_at)
            updated_at = self.random_datetime(created_at, self.now)
            params.append((created_at, updated_at, row["id"]))
        db.executemany(
            f"""
            UPDATE {table_name}
            SET created_at = %s,
                updated_at = %s
            WHERE id = %s
            """,
            params,
        )

    def import_narrators(self) -> int:
        orgs = fetch_all("content_organization", "id")
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, source in enumerate(
            SeedImporter().load_csv("2_content/content_narrator.csv"), start=1
        ):
            code = str(source["narrator_code"])
            if code in seen:
                continue
            seen.add(code)
            org_id = orgs[index % len(orgs)]["id"] if orgs and index % 4 == 0 else None
            narrator_name = (
                source.get("narrator_name")
                or source.get("profile_name")
                or f"懒人主播{index:06d}"
            )
            created_at = self.random_datetime(
                self.now - timedelta(days=1460), self.now - timedelta(days=30)
            )
            rows.append(
                {
                    "narrator_code": code,
                    "narrator_name": narrator_name,
                    "avatar_url": source.get("avatar_url"),
                    "organization_id": org_id,
                    "contract_type": self.random.choices(
                        ("exclusive", "signed", "open", "official"),
                        weights=(8, 25, 62, 5),
                    )[0],
                    "intro": source.get("profile_summary"),
                    "follower_count": to_int(source.get("follower_count")),
                    "album_count": to_int(source.get("album_count")),
                    "yn": 1,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("content_narrator", rows)
