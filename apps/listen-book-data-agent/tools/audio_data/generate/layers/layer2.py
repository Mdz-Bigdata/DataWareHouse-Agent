"""Layer2: content supply and audio assets."""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..config import GENERATION_DEFAULTS, LAYERS
from ..db import db
from .base import BaseGenerator
from .common import fetch_all, fetch_id_map, parse_datetime, to_int
from .seed_importer import SeedImporter
from .validations import validate_layer2


class Layer2Generator(BaseGenerator):
    layer = 2
    layer_name = "内容供给与内容资产"

    def __init__(self) -> None:
        self.random = random.Random(int(GENERATION_DEFAULTS["seed"]))
        self.now = self.local_now()
        self.start_at = self.now - timedelta(days=720)
        self.seed = SeedImporter()

    def run(self) -> None:
        self.header()
        self.clear_layer_tables()

        counts = {table: 0 for table in LAYERS[self.layer]["tables"]}
        counts["creator_profile"] = self.generate_creator_profiles()
        counts["creator_apply_record"] = self.generate_creator_apply_records()
        counts["audio_album"] = self.import_albums()
        counts["album_organization_rel"] = self.generate_album_organization_rels()
        counts["album_author_rel"] = self.generate_album_author_rels()
        counts["album_narrator_rel"] = self.generate_album_narrator_rels()
        counts["album_tag_rel"] = self.generate_album_tag_rels()
        counts["audio_track"] = self.import_tracks()
        self.update_album_track_stats()
        counts["track_audio_file"] = self.generate_track_audio_files()
        counts["content_upload_task"] = self.generate_upload_tasks()
        counts["content_audit_record"] = self.generate_audit_records()
        counts["album_update_record"] = self.generate_album_update_records()
        counts["album_price_rule"] = self.generate_price_rules()

        self.log_table_counts(counts)
        for check in validate_layer2():
            self.log(f"  [OK] validation: {check}")

    def generate_creator_profiles(self) -> int:
        users = fetch_all("user_account", "id, user_no, nickname, created_at")
        narrators = fetch_all(
            "content_narrator", "id, narrator_name, organization_id, created_at"
        )
        orgs = fetch_all("content_organization", "id, organization_name, created_at")
        total = min(
            int(GENERATION_DEFAULTS["creator_users"]),
            len(users),
            max(1, len(narrators) + len(orgs)),
        )
        rows: list[dict[str, Any]] = []
        for index in range(total):
            user = users[index]
            narrator = narrators[index] if index < len(narrators) else None
            org = None
            creator_type = "individual"
            creator_name = user["nickname"]
            if narrator:
                org_id = narrator.get("organization_id")
                org = {"id": org_id} if org_id else None
                creator_name = narrator["narrator_name"]
                creator_type = "official" if index % 11 == 0 else "individual"
            elif orgs:
                org = orgs[index % len(orgs)]
                creator_name = org["organization_name"]
                creator_type = "organization"
            created_at = max(user["created_at"], self.start_at)
            settled_at = self.random_datetime(created_at, self.now)
            rows.append(
                {
                    "user_id": user["id"],
                    "creator_no": f"CRT{index + 1:08d}",
                    "creator_name": creator_name,
                    "creator_type": creator_type,
                    "narrator_id": narrator["id"] if narrator else None,
                    "organization_id": org["id"] if org else None,
                    "certification_status": "certified",
                    "creator_intro": f"{creator_name}的有声内容创作者主页。",
                    "homepage_url": f"https://www.lrts.me/user/{index + 1}",
                    "settled_at": settled_at,
                    "yn": 1,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(settled_at, self.now),
                }
            )
        return self.insert_rows("creator_profile", rows)

    def generate_creator_apply_records(self) -> int:
        creators = fetch_all("creator_profile", "id, user_id, organization_id, created_at")
        rows: list[dict[str, Any]] = []
        for index, creator in enumerate(creators, start=1):
            submitted_at = self.random_datetime(creator["created_at"], self.now)
            reviewed_at = self.random_datetime(submitted_at, self.now)
            rows.append(
                {
                    "apply_no": f"CAP{index:010d}",
                    "user_id": creator["user_id"],
                    "creator_id": creator["id"],
                    "organization_id": creator["organization_id"],
                    "apply_type": "creator_settle",
                    "apply_payload": None,
                    "apply_status": "approved",
                    "reject_reason": None,
                    "submitted_at": submitted_at,
                    "reviewed_at": reviewed_at,
                    "created_at": submitted_at,
                    "updated_at": reviewed_at,
                }
            )
        return self.insert_rows("creator_apply_record", rows)

    def import_albums(self) -> int:
        category_rows = db.fetch_all(
            "SELECT id, category_name, category_type FROM dim_audio_category"
        )
        category_by_name = {row["category_name"]: row for row in category_rows}
        fallback_categories = category_rows
        language_id = fetch_id_map("dim_language", "language_code")["zh-CN"]
        orgs = fetch_all("content_organization", "id")
        rows: list[dict[str, Any]] = []
        for index, source in enumerate(
            self.seed.load_csv("2_content/audio_album.csv"), start=1
        ):
            category = category_by_name.get(source.get("category_name")) or (
                fallback_categories[index % len(fallback_categories)]
            )
            published_at = parse_datetime(source.get("last_update"), self.now)
            published_at = min(published_at, self.now)
            created_at = min(published_at, self.random_datetime(self.start_at, self.now))
            rows.append(
                {
                    "album_code": source["album_code"],
                    "album_title": source["album_title"],
                    "album_type": source.get("album_type") or category["category_type"],
                    "category_id": category["id"],
                    "language_id": language_id,
                    "organization_id": orgs[index % len(orgs)]["id"] if orgs else None,
                    "cover_url": source.get("cover_url"),
                    "summary": source.get("summary"),
                    "album_status": "published",
                    "publish_status": "completed"
                    if index % 5 == 0
                    else "serializing",
                    "age_rating": "children"
                    if "儿" in str(source.get("category_name"))
                    else "all",
                    "track_count": to_int(source.get("track_count")),
                    "total_duration_seconds": to_int(source.get("duration_text")),
                    "play_count": to_int(source.get("play_count_text")),
                    "favorite_count": max(0, to_int(source.get("play_count_text")) // 80),
                    "rating_score": Decimal(str(round(self.random.uniform(7.6, 9.8), 2))),
                    "published_at": published_at,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("audio_album", rows)

    def generate_album_organization_rels(self) -> int:
        albums = fetch_all("audio_album", "id, organization_id, published_at, created_at")
        orgs = fetch_all("content_organization", "id")
        rows: list[dict[str, Any]] = []
        for index, album in enumerate(albums, start=1):
            org_id = album["organization_id"] or orgs[index % len(orgs)]["id"]
            created_at = album["created_at"]
            rows.append(
                {
                    "album_id": album["id"],
                    "organization_id": org_id,
                    "organization_role": "publisher",
                    "authorization_status": "valid",
                    "effective_from": created_at,
                    "effective_to": None,
                    "sort_no": 1,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("album_organization_rel", rows)

    def generate_album_author_rels(self) -> int:
        albums = fetch_all("audio_album", "id, album_type, created_at")
        authors = fetch_all("content_author", "id")
        rows: list[dict[str, Any]] = []
        for index, album in enumerate(albums, start=1):
            rows.append(
                {
                    "album_id": album["id"],
                    "author_id": authors[index % len(authors)]["id"],
                    "author_role": "original_author"
                    if album["album_type"] == "audiobook"
                    else "columnist",
                    "sort_no": 1,
                    "created_at": album["created_at"],
                }
            )
        return self.insert_rows("album_author_rel", rows)

    def generate_album_narrator_rels(self) -> int:
        albums = fetch_all("audio_album", "id, album_type, created_at")
        narrators = fetch_all("content_narrator", "id")
        rows: list[dict[str, Any]] = []
        for index, album in enumerate(albums, start=1):
            rows.append(
                {
                    "album_id": album["id"],
                    "narrator_id": narrators[index % len(narrators)]["id"],
                    "narrator_role": "host"
                    if album["album_type"] in {"program", "podcast", "radio", "course"}
                    else "main",
                    "sort_no": 1,
                    "created_at": album["created_at"],
                }
            )
        return self.insert_rows("album_narrator_rel", rows)

    def generate_album_tag_rels(self) -> int:
        albums = fetch_all("audio_album", "id, category_id, created_at")
        tags = db.fetch_all("SELECT id FROM dim_content_tag WHERE parent_id IS NOT NULL")
        rows: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for index, album in enumerate(albums, start=1):
            for offset in range(2):
                tag_id = tags[(index + offset) % len(tags)]["id"]
                key = (album["id"], tag_id)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "album_id": album["id"],
                        "tag_id": tag_id,
                        "sort_no": offset + 1,
                        "created_at": album["created_at"],
                    }
                )
        return self.insert_rows("album_tag_rel", rows)

    def album_code_by_source(self) -> dict[tuple[str, str], str]:
        return {
            (str(row["source_type"]), str(row["source_id"])): str(row["album_code"])
            for row in self.seed.load_csv("2_content/audio_album.csv")
        }

    def import_tracks(self) -> int:
        albums = db.fetch_all(
            "SELECT id, album_code, created_at, published_at FROM audio_album"
        )
        album_by_code = {str(row["album_code"]): row for row in albums}
        source_album_codes = self.album_code_by_source()
        rows: list[dict[str, Any]] = []
        for source in self.seed.load_csv("2_content/audio_track.csv"):
            album_code = source_album_codes.get(
                (str(source["source_album_type"]), str(source["source_album_id"]))
            )
            album = album_by_code.get(album_code or "")
            if not album:
                continue
            duration = to_int(source.get("duration_seconds"))
            if duration <= 0:
                duration = self.random.randint(420, 1800)
            published_at = parse_datetime(source.get("last_update"), self.now)
            published_at = min(max(published_at, album["created_at"]), self.now)
            created_at = self.random_datetime(album["created_at"], published_at)
            free_flag = 1 if source.get("pay_type") == "free" else 0
            rows.append(
                {
                    "album_id": album["id"],
                    "track_no": to_int(source["track_no"], 1),
                    "track_title": source["track_title"],
                    "track_type": "normal",
                    "duration_seconds": duration,
                    "free_flag": free_flag,
                    "trial_seconds": duration if free_flag else min(300, duration),
                    "track_status": "published",
                    "play_count": to_int(source.get("play_count")),
                    "published_at": published_at,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("audio_track", rows)

    def update_album_track_stats(self) -> None:
        db.execute(
            """
            UPDATE audio_album AS a
            JOIN (
                SELECT
                    album_id,
                    COUNT(*) AS track_count,
                    SUM(duration_seconds) AS duration_seconds,
                    SUM(play_count) AS play_count
                FROM audio_track
                GROUP BY album_id
            ) AS t ON t.album_id = a.id
            SET
                a.track_count = t.track_count,
                a.total_duration_seconds = t.duration_seconds,
                a.play_count = GREATEST(a.play_count, t.play_count)
            """
        )

    def generate_track_audio_files(self) -> int:
        tracks = fetch_all("audio_track", "id, duration_seconds, created_at")
        rows: list[dict[str, Any]] = []
        for index, track in enumerate(tracks, start=1):
            bitrate = 64 if index % 5 else 128
            duration = int(track["duration_seconds"])
            rows.append(
                {
                    "track_id": track["id"],
                    "file_code": f"AF{index:012d}",
                    "file_url": f"https://audio.example.com/lrts/{track['id']}.mp3",
                    "file_format": "mp3",
                    "bitrate_kbps": bitrate,
                    "sample_rate_hz": 44100,
                    "file_size_bytes": max(1024, duration * bitrate * 1000 // 8),
                    "duration_seconds": duration,
                    "file_status": "available",
                    "created_at": track["created_at"],
                    "updated_at": self.random_datetime(track["created_at"], self.now),
                }
            )
        return self.insert_rows("track_audio_file", rows)

    def generate_upload_tasks(self) -> int:
        creators = fetch_all("creator_profile", "id, created_at")
        albums = fetch_all("audio_album", "id, created_at")
        files = fetch_all("track_audio_file", "id, track_id, file_size_bytes, created_at")
        rows: list[dict[str, Any]] = []
        index = 1
        for album in albums:
            creator = creators[index % len(creators)]
            submitted_at = max(album["created_at"], creator["created_at"])
            rows.append(
                {
                    "upload_no": f"UPL{index:012d}",
                    "creator_id": creator["id"],
                    "album_id": album["id"],
                    "track_id": None,
                    "file_id": None,
                    "upload_type": "album",
                    "source_file_name": None,
                    "source_file_url": None,
                    "file_size_bytes": None,
                    "process_status": "processed",
                    "failure_reason": None,
                    "submitted_at": submitted_at,
                    "processed_at": self.random_datetime(submitted_at, self.now),
                    "created_at": submitted_at,
                    "updated_at": self.random_datetime(submitted_at, self.now),
                }
            )
            index += 1
        for file in files[: len(albums)]:
            creator = creators[index % len(creators)]
            submitted_at = max(file["created_at"], creator["created_at"])
            rows.append(
                {
                    "upload_no": f"UPL{index:012d}",
                    "creator_id": creator["id"],
                    "album_id": None,
                    "track_id": file["track_id"],
                    "file_id": file["id"],
                    "upload_type": "audio_file",
                    "source_file_name": f"{file['track_id']}.mp3",
                    "source_file_url": f"https://upload.example.com/{file['track_id']}.mp3",
                    "file_size_bytes": file["file_size_bytes"],
                    "process_status": "processed",
                    "failure_reason": None,
                    "submitted_at": submitted_at,
                    "processed_at": self.random_datetime(submitted_at, self.now),
                    "created_at": submitted_at,
                    "updated_at": self.random_datetime(submitted_at, self.now),
                }
            )
            index += 1
        return self.insert_rows("content_upload_task", rows)

    def generate_audit_records(self) -> int:
        uploads = fetch_all(
            "content_upload_task", "id, upload_type, album_id, file_id, created_at"
        )
        creators = fetch_all("creator_profile", "id, created_at")
        rows: list[dict[str, Any]] = []
        index = 1
        for upload in uploads:
            target_type = "audio_file" if upload["upload_type"] == "audio_file" else "album"
            target_id = upload["file_id"] if target_type == "audio_file" else upload["album_id"]
            if target_id is None:
                target_id = upload["id"]
            audited_at = self.random_datetime(upload["created_at"], self.now)
            rows.append(
                {
                    "audit_no": f"AUD{index:012d}",
                    "upload_task_id": upload["id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "audit_type": "machine" if index % 4 else "manual",
                    "audit_status": "approved",
                    "audit_reason": None,
                    "audit_payload": None,
                    "auditor_name": "系统审核" if index % 4 else "内容审核员",
                    "audited_at": audited_at,
                    "created_at": upload["created_at"],
                    "updated_at": audited_at,
                }
            )
            index += 1
        for creator in creators[: max(1, len(creators) // 5)]:
            audited_at = self.random_datetime(creator["created_at"], self.now)
            rows.append(
                {
                    "audit_no": f"AUD{index:012d}",
                    "upload_task_id": None,
                    "target_type": "creator_profile",
                    "target_id": creator["id"],
                    "audit_type": "manual",
                    "audit_status": "approved",
                    "audit_reason": None,
                    "audit_payload": None,
                    "auditor_name": "资质审核员",
                    "audited_at": audited_at,
                    "created_at": creator["created_at"],
                    "updated_at": audited_at,
                }
            )
            index += 1
        return self.insert_rows("content_audit_record", rows)

    def generate_album_update_records(self) -> int:
        albums = fetch_all("audio_album", "id, album_title, published_at, created_at")
        latest_tracks = db.fetch_all(
            """
            SELECT t.album_id, t.id AS track_id, t.track_title, t.duration_seconds, t.published_at
            FROM audio_track AS t
            JOIN (
                SELECT album_id, MAX(track_no) AS max_track_no
                FROM audio_track
                GROUP BY album_id
            ) AS x ON x.album_id = t.album_id AND x.max_track_no = t.track_no
            """
        )
        latest_by_album = {row["album_id"]: row for row in latest_tracks}
        creators = fetch_all("creator_profile", "id")
        rows: list[dict[str, Any]] = []
        for index, album in enumerate(albums, start=1):
            creator_id = creators[index % len(creators)]["id"]
            rows.append(
                {
                    "album_id": album["id"],
                    "track_id": None,
                    "creator_id": creator_id,
                    "update_type": "album_published",
                    "update_title": f"{album['album_title']} 上架",
                    "update_summary": "专辑已发布。",
                    "track_count_delta": 0,
                    "duration_delta_seconds": 0,
                    "updated_at_event": album["published_at"] or album["created_at"],
                    "created_at": album["created_at"],
                }
            )
            track = latest_by_album.get(album["id"])
            if track:
                rows.append(
                    {
                        "album_id": album["id"],
                        "track_id": track["track_id"],
                        "creator_id": creator_id,
                        "update_type": "track_published",
                        "update_title": f"更新 {track['track_title']}",
                        "update_summary": "新章节已发布。",
                        "track_count_delta": 1,
                        "duration_delta_seconds": track["duration_seconds"],
                        "updated_at_event": track["published_at"] or album["created_at"],
                        "created_at": track["published_at"] or album["created_at"],
                    }
                )
        return self.insert_rows("album_update_record", rows)

    def generate_price_rules(self) -> int:
        albums = fetch_all("audio_album", "id, track_count, created_at")
        rows: list[dict[str, Any]] = []
        for index, album in enumerate(albums, start=1):
            if index % 7 == 0:
                price_type = "free"
                album_price = Decimal("0.00")
                track_price = Decimal("0.00")
                free_count = int(album["track_count"])
            elif index % 3 == 0:
                price_type = "vip_free"
                album_price = Decimal("0.00")
                track_price = Decimal("0.00")
                free_count = min(5, int(album["track_count"]))
            elif index % 2 == 0:
                price_type = "album_paid"
                album_price = Decimal(str(self.random.choice((9.9, 19.9, 29.9, 39.9))))
                track_price = Decimal("0.00")
                free_count = min(3, int(album["track_count"]))
            else:
                price_type = "track_paid"
                album_price = Decimal("0.00")
                track_price = Decimal(str(self.random.choice((0.39, 0.59, 0.99))))
                free_count = min(3, int(album["track_count"]))
            rows.append(
                {
                    "album_id": album["id"],
                    "price_type": price_type,
                    "currency_code": "CNY",
                    "album_price_amount": album_price,
                    "track_price_amount": track_price,
                    "free_track_count": free_count,
                    "effective_from": album["created_at"],
                    "effective_to": None,
                    "yn": 1,
                    "created_at": album["created_at"],
                    "updated_at": self.random_datetime(album["created_at"], self.now),
                }
            )
        return self.insert_rows("album_price_rule", rows)
