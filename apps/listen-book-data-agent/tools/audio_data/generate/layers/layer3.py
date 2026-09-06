"""Layer3: user membership, bookshelf and preferences."""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from ..config import GENERATION_DEFAULTS, LAYERS
from ..db import db
from .base import BaseGenerator
from .common import fetch_all, json_dumps
from .validations import validate_layer3

PROVINCES = (
    ("广东省", "深圳市"),
    ("北京市", "北京市"),
    ("上海市", "上海市"),
    ("浙江省", "杭州市"),
    ("江苏省", "南京市"),
    ("四川省", "成都市"),
    ("湖北省", "武汉市"),
    ("陕西省", "西安市"),
)
OCCUPATIONS = ("学生", "白领", "自由职业", "教师", "司机", "工程师", "运营", "退休")


class Layer3Generator(BaseGenerator):
    layer = 3
    layer_name = "用户会员与偏好"

    def __init__(self) -> None:
        self.random = random.Random(int(GENERATION_DEFAULTS["seed"]))
        self.now = self.local_now()

    def run(self) -> None:
        self.header()
        self.clear_layer_tables()

        counts = {table: 0 for table in LAYERS[self.layer]["tables"]}
        counts["user_profile"] = self.generate_user_profiles()
        counts["member_account"] = self.generate_member_accounts()
        counts["user_follow"] = self.generate_user_follows()
        counts["user_bookshelf"] = self.generate_bookshelves()
        counts["user_preference"] = self.generate_preferences()
        self.update_follow_and_favorite_counts()

        self.log_table_counts(counts)
        for check in validate_layer3():
            self.log(f"  [OK] validation: {check}")

    def generate_user_profiles(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        rows: list[dict[str, Any]] = []
        for index, user in enumerate(users, start=1):
            province, city = PROVINCES[index % len(PROVINCES)]
            birthday = (self.now - timedelta(days=self.random.randint(18 * 365, 55 * 365))).date()
            rows.append(
                {
                    "user_id": user["id"],
                    "gender": self.random.choices(
                        ("male", "female", "unknown"), weights=(45, 45, 10)
                    )[0],
                    "birthday": birthday,
                    "province": province,
                    "city": city,
                    "occupation": OCCUPATIONS[index % len(OCCUPATIONS)],
                    "listening_scene_payload": json_dumps(
                        self.random.sample(
                            ["commute", "before_sleep", "exercise", "housework", "study"],
                            k=2,
                        )
                    ),
                    "created_at": user["created_at"],
                    "updated_at": self.random_datetime(user["created_at"], self.now),
                }
            )
        return self.insert_rows("user_profile", rows)

    def generate_member_accounts(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        rows: list[dict[str, Any]] = []
        for index, user in enumerate(users, start=1):
            is_history_vip = index % 17 == 0
            valid_from = (
                self.random_datetime(user["created_at"], self.now - timedelta(days=30))
                if is_history_vip
                else None
            )
            valid_to = valid_from + timedelta(days=30) if valid_from else None
            rows.append(
                {
                    "user_id": user["id"],
                    "member_level": "vip" if is_history_vip else "normal",
                    "member_status": "expired" if is_history_vip else "inactive",
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "points_balance": self.random.randint(0, 500),
                    "growth_value": self.random.randint(0, 3000),
                    "created_at": user["created_at"],
                    "updated_at": self.random_datetime(user["created_at"], self.now),
                }
            )
        return self.insert_rows("member_account", rows)

    def generate_user_follows(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        narrators = fetch_all("content_narrator", "id, created_at")
        authors = fetch_all("content_author", "id, created_at")
        orgs = fetch_all("content_organization", "id, created_at")
        targets = (
            [("narrator", row["id"], row["created_at"]) for row in narrators]
            + [("author", row["id"], row["created_at"]) for row in authors]
            + [("organization", row["id"], row["created_at"]) for row in orgs]
        )
        seen: set[tuple[int, str, int]] = set()
        total = min(len(users) * 3, len(users) + len(targets) * 6)

        def rows():
            for index in range(total):
                user = users[index % len(users)]
                target_type, target_id, target_created_at = targets[
                    (index * 7) % len(targets)
                ]
                key = (user["id"], target_type, target_id)
                if key in seen:
                    continue
                seen.add(key)
                followed_at = self.random_datetime(
                    max(user["created_at"], target_created_at), self.now
                )
                cancelled = index % 19 == 0
                yield {
                    "user_id": user["id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "follow_status": "cancelled" if cancelled else "following",
                    "followed_at": followed_at,
                    "cancelled_at": self.random_datetime(followed_at, self.now)
                    if cancelled
                    else None,
                    "created_at": followed_at,
                    "updated_at": self.random_datetime(followed_at, self.now),
                }

        return self.stream_rows(
            "user_follow",
            rows(),
            total_rows=None,
            build_step_name="build user_follow",
        )

    def generate_bookshelves(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        albums = fetch_all("audio_album", "id, published_at, created_at")
        tracks = db.fetch_all(
            """
            SELECT album_id, id, duration_seconds
            FROM audio_track
            WHERE track_no = 1
            """
        )
        first_track_by_album = {row["album_id"]: row for row in tracks}
        total = int(GENERATION_DEFAULTS["bookshelf_rows"])
        seen: set[tuple[int, int]] = set()

        def rows():
            cursor = 0
            emitted = 0
            while emitted < total and cursor < total * 5:
                user = users[cursor % len(users)]
                album = albums[(cursor * 11) % len(albums)]
                cursor += 1
                key = (user["id"], album["id"])
                if key in seen:
                    continue
                track = first_track_by_album.get(album["id"])
                if not track:
                    continue
                seen.add(key)
                created_at = self.random_datetime(
                    max(
                        user["created_at"],
                        album["published_at"] or album["created_at"],
                    ),
                    self.now,
                )
                emitted += 1
                yield {
                    "user_id": user["id"],
                    "album_id": album["id"],
                    "shelf_status": self.random.choices(
                        ("favorited", "subscribed", "removed"), weights=(55, 40, 5)
                    )[0],
                    "last_track_id": track["id"],
                    "last_position_seconds": self.random.randint(
                        0, max(1, int(track["duration_seconds"]) - 1)
                    ),
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }

        return self.stream_rows(
            "user_bookshelf",
            rows(),
            total_rows=total,
            build_step_name="build user_bookshelf",
        )

    def generate_preferences(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        categories = db.fetch_all(
            """
            SELECT c.id
            FROM dim_audio_category AS c
            LEFT JOIN dim_audio_category AS child ON child.parent_id = c.id
            WHERE c.yn = 1 AND child.id IS NULL
            """
        )
        if not categories:
            categories = db.fetch_all("SELECT id FROM dim_audio_category WHERE yn = 1")
        tags = db.fetch_all("SELECT id FROM dim_content_tag WHERE parent_id IS NOT NULL")
        rows: list[dict[str, Any]] = []
        for index, user in enumerate(users, start=1):
            created_at = self.random_datetime(user["created_at"], self.now)
            rows.append(
                {
                    "user_id": user["id"],
                    "category_id": categories[index % len(categories)]["id"],
                    "tag_id": None,
                    "preference_type": "category",
                    "preference_payload": None,
                    "weight_score": round(self.random.uniform(20, 95), 2),
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
            rows.append(
                {
                    "user_id": user["id"],
                    "category_id": None,
                    "tag_id": tags[index % len(tags)]["id"],
                    "preference_type": "tag",
                    "preference_payload": None,
                    "weight_score": round(self.random.uniform(20, 95), 2),
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
            rows.append(
                {
                    "user_id": user["id"],
                    "category_id": None,
                    "tag_id": None,
                    "preference_type": "play_setting",
                    "preference_payload": json_dumps(
                        {
                            "speed": self.random.choice([1.0, 1.25, 1.5]),
                            "auto_play_next": self.random.choice([True, False]),
                        }
                    ),
                    "weight_score": 0,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("user_preference", rows)

    def update_follow_and_favorite_counts(self) -> None:
        db.execute(
            """
            UPDATE content_narrator AS n
            LEFT JOIN (
                SELECT target_id, COUNT(*) AS c
                FROM user_follow
                WHERE target_type = 'narrator' AND follow_status = 'following'
                GROUP BY target_id
            ) AS f ON f.target_id = n.id
            SET n.follower_count = GREATEST(n.follower_count, IFNULL(f.c, 0))
            """
        )
        db.execute(
            """
            UPDATE audio_album AS a
            LEFT JOIN (
                SELECT album_id, COUNT(*) AS c
                FROM user_bookshelf
                WHERE shelf_status IN ('favorited', 'subscribed', 'finished')
                GROUP BY album_id
            ) AS b ON b.album_id = a.id
            SET a.favorite_count = GREATEST(a.favorite_count, IFNULL(b.c, 0))
            """
        )
