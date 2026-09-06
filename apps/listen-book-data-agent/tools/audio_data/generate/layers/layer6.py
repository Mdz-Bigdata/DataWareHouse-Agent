"""Layer6: operation, recommendation and search data."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from typing import Any

from ..config import GENERATION_DEFAULTS, LAYERS
from ..db import db
from .base import BaseGenerator
from .common import fetch_all, fetch_id_map, to_int
from .seed_importer import SeedImporter
from .validations import validate_layer6


class Layer6Generator(BaseGenerator):
    layer = 6
    layer_name = "运营推荐与搜索"

    def __init__(self) -> None:
        self.random = random.Random(int(GENERATION_DEFAULTS["seed"]))
        self.now = self.local_now()
        self.seed = SeedImporter()

    def run(self) -> None:
        self.header()
        self.clear_layer_tables()

        counts = {table: 0 for table in LAYERS[self.layer]["tables"]}
        counts["ranking_list"] = self.import_ranking_lists()
        counts["ranking_item"] = self.import_ranking_items()
        counts["recommend_slot"] = self.import_recommend_slots()
        counts["content_topic"] = self.import_content_topics()
        counts["recommend_item"] = self.import_recommend_items()
        counts["content_topic_item"] = self.import_topic_items()
        counts["search_query_log"] = self.generate_search_logs()
        counts["search_keyword_stat"] = self.generate_search_stats()

        self.log_table_counts(counts)
        for check in validate_layer6():
            self.log(f"  [OK] validation: {check}")

    def album_ids_by_source(self) -> dict[tuple[str, str], int]:
        album_codes = {
            (str(row["source_type"]), str(row["source_id"])): str(row["album_code"])
            for row in self.seed.load_csv("2_content/audio_album.csv")
        }
        id_by_code = fetch_id_map("audio_album", "album_code")
        return {
            key: id_by_code[code]
            for key, code in album_codes.items()
            if code in id_by_code
        }

    def import_ranking_lists(self) -> int:
        rows: list[dict[str, Any]] = []
        for index, source in enumerate(
            self.seed.load_csv("6_operation/ranking_list.csv"), start=1
        ):
            created_at = self.now - timedelta(days=180 - index % 30)
            rows.append(
                {
                    "ranking_code": source["ranking_code"],
                    "ranking_name": source["ranking_name"],
                    "ranking_type": source["ranking_type"],
                    "category_id": None,
                    "period_type": source["period_type"],
                    "yn": int(source["yn"] or 1),
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("ranking_list", rows)

    def import_ranking_items(self) -> int:
        rankings = {
            row["ranking_code"]: row
            for row in fetch_all("ranking_list", "id, ranking_code, created_at")
        }
        albums = self.albums_by_source()
        rows: list[dict[str, Any]] = []
        for source in self.seed.load_csv("6_operation/ranking_item.csv"):
            ranking = rankings.get(str(source["ranking_code"]))
            album = albums.get((str(source["source_type"]), str(source["source_id"])))
            if not ranking or not album:
                continue
            stat_floor = max(
                ranking["created_at"].date(),
                (album["published_at"] or album["created_at"]).date(),
            )
            stat_ceiling = self.now.date()
            if stat_floor > stat_ceiling:
                stat_date = stat_ceiling
            else:
                stat_date = stat_floor + timedelta(
                    days=self.random.randint(0, max(0, (stat_ceiling - stat_floor).days))
                )
            created_at = self.stat_created_at(stat_date)
            play_count = max(0, 100000 - int(source["rank_no"]) * 1800)
            rows.append(
                {
                    "ranking_id": ranking["id"],
                    "stat_date": stat_date,
                    "target_type": "album",
                    "target_id": album["id"],
                    "rank_no": int(source["rank_no"]),
                    "score_value": max(1, 10000 - int(source["rank_no"]) * 25),
                    "play_count": play_count,
                    "favorite_count": play_count // 80,
                    "order_count": play_count // 2000,
                    "created_at": created_at,
                }
            )
        return self.insert_rows("ranking_item", rows)

    def import_recommend_slots(self) -> int:
        rows: list[dict[str, Any]] = []
        for index, source in enumerate(
            self.seed.load_csv("6_operation/recommend_slot.csv"), start=1
        ):
            created_at = self.now - timedelta(days=max(1, 180 - index))
            rows.append(
                {
                    "slot_code": source["slot_code"],
                    "slot_name": source["slot_name"],
                    "page_code": source["page_code"],
                    "slot_type": source["slot_type"],
                    "max_item_count": int(source["max_item_count"] or 1),
                    "yn": 1,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("recommend_slot", rows)

    def import_content_topics(self) -> int:
        rows: list[dict[str, Any]] = []
        for index, source in enumerate(
            self.seed.load_csv("6_operation/content_topic.csv"), start=1
        ):
            published_at = self.random_datetime(self.now - timedelta(days=180), self.now)
            topic_title = source.get("topic_title") or f"精选听单专题{index:03d}"
            rows.append(
                {
                    "topic_code": source["topic_code"],
                    "topic_title": topic_title,
                    "topic_type": source["topic_type"],
                    "cover_url": source.get("cover_url"),
                    "summary": source.get("summary"),
                    "topic_status": "published",
                    "published_at": published_at,
                    "created_at": published_at - timedelta(days=self.random.randint(1, 15)),
                    "updated_at": published_at,
                }
            )
        return self.insert_rows("content_topic", rows)

    def import_recommend_items(self) -> int:
        slots = {
            row["slot_code"]: row
            for row in fetch_all("recommend_slot", "id, slot_code, created_at")
        }
        albums = self.albums_by_source()
        topics = {
            row["topic_code"]: row
            for row in fetch_all("content_topic", "id, topic_code, created_at, published_at")
        }
        rankings = {
            row["ranking_code"]: row
            for row in fetch_all("ranking_list", "id, ranking_code, created_at")
        }
        rows: list[dict[str, Any]] = []
        for source in self.seed.load_csv("6_operation/recommend_item.csv"):
            slot = slots.get(str(source["slot_code"]))
            if not slot:
                continue
            target_type = source["target_type"]
            target_id = None
            target_ready_at = slot["created_at"]
            if target_type == "album":
                album = albums.get((str(source["source_type"]), str(source["source_id"])))
                if album:
                    target_id = album["id"]
                    target_ready_at = album["published_at"] or album["created_at"]
            elif target_type == "topic":
                topic = topics.get(str(source.get("source_id")))
                if topic:
                    target_id = topic["id"]
                    target_ready_at = topic["published_at"] or topic["created_at"]
            elif target_type == "ranking":
                ranking = rankings.get(str(source.get("source_id")))
                if ranking:
                    target_id = ranking["id"]
                    target_ready_at = ranking["created_at"]
            if target_type != "url" and target_id is None:
                continue
            effective_floor = max(slot["created_at"], target_ready_at)
            effective_from = self.random_datetime(effective_floor, self.now)
            rows.append(
                {
                    "slot_id": slot["id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "title": source.get("title"),
                    "image_url": source.get("image_url"),
                    "jump_url": source.get("jump_url"),
                    "sort_no": int(source["sort_no"] or 0),
                    "effective_from": effective_from,
                    "effective_to": None,
                    "yn": 1,
                    "created_at": effective_from,
                    "updated_at": self.random_datetime(effective_from, self.now),
                }
            )
        return self.insert_rows("recommend_item", rows)

    def import_topic_items(self) -> int:
        topics = {
            row["topic_code"]: row
            for row in fetch_all("content_topic", "id, topic_code, created_at, published_at")
        }
        albums = self.albums_by_source()
        rows: list[dict[str, Any]] = []
        seen: set[tuple[int, str, int]] = set()
        for source in self.seed.load_csv("6_operation/content_topic_item.csv"):
            topic = topics.get(str(source["topic_code"]))
            album = albums.get((str(source["source_type"]), str(source["source_id"])))
            if not topic or not album:
                continue
            key = (topic["id"], "album", album["id"])
            if key in seen:
                continue
            seen.add(key)
            ready_at = max(
                topic["published_at"] or topic["created_at"],
                album["published_at"] or album["created_at"],
            )
            created_at = self.random_datetime(ready_at, self.now)
            rows.append(
                {
                    "topic_id": topic["id"],
                    "target_type": "album",
                    "target_id": album["id"],
                    "title": source.get("title"),
                    "summary": None,
                    "image_url": None,
                    "sort_no": int(source["sort_no"] or 0),
                    "yn": 1,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("content_topic_item", rows)

    def generate_search_logs(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        channels = fetch_all("dim_channel", "id, created_at")
        albums = fetch_all("audio_album", "id, album_title, published_at, created_at")
        narrators = fetch_all("content_narrator", "id, narrator_name, created_at")
        topics = fetch_all("content_topic", "id, topic_title, published_at, created_at")
        keywords = [row["album_title"][:12] for row in albums[:300]]
        keywords.extend(row["narrator_name"][:12] for row in narrators[:200])
        keywords.extend(row["topic_title"][:12] for row in topics[:100])
        keywords.extend(["悬疑", "都市", "评书", "儿童故事", "睡前", "历史", "玄幻"])
        total = int(GENERATION_DEFAULTS["search_logs"])

        def rows():
            for index in range(1, total + 1):
                user = users[index % len(users)] if index % 8 != 0 else None
                keyword = keywords[index % len(keywords)]
                clicked = index % 3 != 0
                clicked_type = "none"
                clicked_id = None
                clicked_ready_at = None
                if clicked:
                    if index % 7 == 0:
                        target = narrators[index % len(narrators)]
                        clicked_type = "narrator"
                        clicked_id = target["id"]
                        clicked_ready_at = target["created_at"]
                    elif index % 11 == 0:
                        target = topics[index % len(topics)]
                        clicked_type = "topic"
                        clicked_id = target["id"]
                        clicked_ready_at = target["published_at"] or target["created_at"]
                    else:
                        target = albums[index % len(albums)]
                        clicked_type = "album"
                        clicked_id = target["id"]
                        clicked_ready_at = target["published_at"] or target["created_at"]
                channel = channels[index % len(channels)]
                created_floor = max(
                    user["created_at"] if user else self.now - timedelta(days=180),
                    channel["created_at"],
                    clicked_ready_at or self.now - timedelta(days=180),
                )
                created_at = self.random_datetime(created_floor, self.now)
                yield {
                    "query_no": f"SRH{index:014d}",
                    "user_id": user["id"] if user else None,
                    "channel_id": channel["id"],
                    "keyword": keyword,
                    "search_type": self.random.choice(("all", "book", "program", "narrator")),
                    "result_count": self.random.randint(0, 200),
                    "clicked_flag": 1 if clicked else 0,
                    "clicked_target_type": clicked_type,
                    "clicked_target_id": clicked_id,
                    "created_at": created_at,
                }

        return self.stream_rows(
            "search_query_log",
            rows(),
            total_rows=total,
            build_step_name="build search_query_log",
        )

    def generate_search_stats(self) -> int:
        logs = db.fetch_all(
            """
            SELECT
                DATE(created_at) AS stat_date,
                channel_id,
                keyword,
                COUNT(*) AS search_count,
                SUM(clicked_flag) AS result_click_count,
                SUM(clicked_target_type = 'album') AS album_click_count,
                SUM(clicked_target_type = 'narrator') AS narrator_click_count
            FROM search_query_log
            GROUP BY DATE(created_at), channel_id, keyword
            """
        )
        rows: list[dict[str, Any]] = []
        for log in logs:
            created_at = self.stat_created_at(log["stat_date"])
            rows.append(
                {
                    "stat_date": log["stat_date"],
                    "channel_id": log["channel_id"],
                    "keyword": log["keyword"],
                    "search_count": to_int(log["search_count"]),
                    "result_click_count": to_int(log["result_click_count"]),
                    "album_click_count": to_int(log["album_click_count"]),
                    "narrator_click_count": to_int(log["narrator_click_count"]),
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("search_keyword_stat", rows)

    def albums_by_source(self) -> dict[tuple[str, str], dict[str, Any]]:
        album_codes = {
            (str(row["source_type"]), str(row["source_id"])): str(row["album_code"])
            for row in self.seed.load_csv("2_content/audio_album.csv")
        }
        rows = db.fetch_all(
            """
            SELECT id, album_code, created_at, published_at
            FROM audio_album
            """
        )
        albums_by_code = {str(row["album_code"]): row for row in rows}
        return {
            key: albums_by_code[code]
            for key, code in album_codes.items()
            if code in albums_by_code
        }

    def stat_created_at(self, stat_date) -> datetime:
        created_at = datetime.combine(stat_date + timedelta(days=1), time(hour=2))
        created_at = created_at + timedelta(minutes=self.random.randint(0, 180))
        if created_at > self.now:
            return self.now
        return created_at
