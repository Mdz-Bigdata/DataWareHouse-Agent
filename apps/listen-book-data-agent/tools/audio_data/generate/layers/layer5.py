"""Layer5: listening behavior, interaction and service data."""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from ..config import GENERATION_DEFAULTS, LAYERS
from ..db import db
from .base import BaseGenerator
from .common import fetch_all
from .validations import validate_layer5

COMMENTS = (
    "声音很稳，适合通勤路上听。",
    "剧情节奏不错，更新可以再快一点。",
    "主播演绎有代入感，收藏了。",
    "章节质量稳定，晚上听很合适。",
    "内容完整，分类也比较准确。",
)


class Layer5Generator(BaseGenerator):
    layer = 5
    layer_name = "播放互动与服务"

    def __init__(self) -> None:
        self.random = random.Random(int(GENERATION_DEFAULTS["seed"]))
        self.now = self.local_now()

    def run(self) -> None:
        self.header()
        self.clear_layer_tables()

        counts = {table: 0 for table in LAYERS[self.layer]["tables"]}
        counts["play_session"] = self.generate_play_sessions()
        counts["listening_progress"] = self.generate_listening_progress()
        self.update_finished_bookshelves()
        counts["content_comment"] = self.generate_comments()
        counts["content_rating"] = self.generate_ratings()
        counts["user_reaction"] = self.generate_reactions()
        counts["content_report"] = self.generate_reports()
        counts["user_activity_feed"] = self.generate_activity_feeds()
        counts["support_ticket"] = self.generate_support_tickets()
        counts["user_message"] = self.generate_messages()
        self.update_behavior_stats()

        self.log_table_counts(counts)
        for check in validate_layer5():
            self.log(f"  [OK] validation: {check}")

    def playable_tracks(self) -> list[dict[str, Any]]:
        return db.fetch_all(
            """
            SELECT
                t.id,
                t.album_id,
                t.duration_seconds,
                t.created_at,
                GREATEST(
                    t.created_at,
                    IFNULL(t.published_at, t.created_at),
                    IFNULL(a.published_at, a.created_at)
                ) AS ready_at,
                a.album_title
            FROM audio_track AS t
            JOIN audio_album AS a ON a.id = t.album_id
            JOIN album_price_rule AS p ON p.album_id = a.id AND p.yn = 1
            WHERE t.track_status = 'published'
              AND a.album_status = 'published'
              AND (
                  p.price_type = 'free'
                  OR t.free_flag = 1
                  OR t.track_no <= p.free_track_count
              )
            """
        )

    def generate_play_sessions(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        channels = fetch_all("dim_channel", "id")
        tracks = self.playable_tracks()
        total = int(GENERATION_DEFAULTS["play_sessions"])

        def rows():
            for index in range(1, total + 1):
                user = users[index % len(users)]
                track = tracks[(index * 13) % len(tracks)]
                duration = max(1, int(track["duration_seconds"]))
                start_position = self.random.randint(0, max(0, duration // 3))
                played = self.random.randint(1, duration)
                end_position = min(duration, start_position + played)
                play_start_at = self.random_datetime(
                    max(user["created_at"], track["ready_at"]), self.now
                )
                play_end_at = play_start_at + timedelta(seconds=played)
                if play_end_at > self.now:
                    play_end_at = self.now
                yield {
                    "session_no": f"PLY{index:014d}",
                    "user_id": user["id"],
                    "album_id": track["album_id"],
                    "track_id": track["id"],
                    "channel_id": channels[index % len(channels)]["id"],
                    "start_position_seconds": start_position,
                    "end_position_seconds": end_position,
                    "played_seconds": max(0, int((play_end_at - play_start_at).total_seconds())),
                    "play_start_at": play_start_at,
                    "play_end_at": play_end_at,
                    "play_status": "completed"
                    if end_position >= duration * 0.9
                    else self.random.choice(("interrupted", "failed")),
                    "created_at": play_start_at,
                }

        return self.stream_rows(
            "play_session",
            rows(),
            total_rows=total,
            build_step_name="build play_session",
        )

    def generate_listening_progress(self) -> int:
        rows = db.fetch_all(
            """
            SELECT
                p.user_id,
                p.album_id,
                p.track_id,
                SUBSTRING_INDEX(
                    GROUP_CONCAT(p.end_position_seconds ORDER BY p.play_start_at DESC),
                    ',',
                    1
                ) AS position_seconds,
                MAX(t.duration_seconds) AS duration_seconds,
                MAX(p.play_start_at) AS last_played_at,
                MIN(p.created_at) AS created_at
            FROM play_session AS p
            JOIN audio_track AS t ON t.id = p.track_id
            WHERE p.play_status IN ('completed', 'interrupted')
            GROUP BY p.user_id, p.album_id, p.track_id
            """
        )
        progress_rows: list[dict[str, Any]] = []
        for row in rows:
            position = min(int(row["position_seconds"]), int(row["duration_seconds"]))
            progress_rows.append(
                {
                    "user_id": row["user_id"],
                    "album_id": row["album_id"],
                    "track_id": row["track_id"],
                    "position_seconds": position,
                    "duration_seconds": row["duration_seconds"],
                    "finished_flag": 1
                    if position >= int(row["duration_seconds"]) * 0.9
                    else 0,
                    "last_played_at": row["last_played_at"],
                    "created_at": row["created_at"],
                    "updated_at": row["last_played_at"],
                }
            )
        return self.insert_rows("listening_progress", progress_rows)

    def update_finished_bookshelves(self) -> None:
        db.execute(
            """
            UPDATE user_bookshelf AS b
            JOIN listening_progress AS p
              ON p.user_id = b.user_id AND p.album_id = b.album_id
            SET b.shelf_status = 'finished',
                b.last_track_id = p.track_id,
                b.last_position_seconds = p.position_seconds,
                b.updated_at = p.last_played_at
            WHERE p.finished_flag = 1
              AND b.shelf_status <> 'removed'
            """
        )

    def generate_comments(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        tracks = self.playable_tracks()
        total = int(GENERATION_DEFAULTS["comments"])

        def rows():
            for index in range(1, total + 1):
                user = users[index % len(users)]
                track = tracks[(index * 17) % len(tracks)]
                target_type = "track" if index % 3 == 0 else "album"
                target_id = track["id"] if target_type == "track" else track["album_id"]
                created_at = self.random_datetime(
                    max(user["created_at"], track["ready_at"]), self.now
                )
                yield {
                    "user_id": user["id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "parent_comment_id": None,
                    "comment_text": COMMENTS[index % len(COMMENTS)],
                    "audit_status": self.random.choices(
                        ("approved", "pending", "rejected"), weights=(90, 7, 3)
                    )[0],
                    "like_count": 0,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }

        return self.stream_rows(
            "content_comment",
            rows(),
            total_rows=total,
            build_step_name="build content_comment",
        )

    def generate_ratings(self) -> int:
        played = db.fetch_all(
            """
            SELECT user_id, album_id, MIN(created_at) AS created_at
            FROM listening_progress
            GROUP BY user_id, album_id
            LIMIT 50000
            """
        )
        rows: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for index, row in enumerate(played, start=1):
            key = (row["user_id"], row["album_id"])
            if key in seen or index % 3 == 0:
                continue
            seen.add(key)
            rows.append(
                {
                    "user_id": row["user_id"],
                    "album_id": row["album_id"],
                    "rating_score": round(self.random.uniform(6.5, 10.0), 2),
                    "rating_text": COMMENTS[index % len(COMMENTS)] if index % 5 == 0 else None,
                    "created_at": row["created_at"],
                    "updated_at": self.random_datetime(row["created_at"], self.now),
                }
            )
        return self.insert_rows("content_rating", rows)

    def generate_reactions(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        albums = fetch_all("audio_album", "id, IFNULL(published_at, created_at) AS created_at")
        tracks = fetch_all("audio_track", "id, IFNULL(published_at, created_at) AS created_at")
        narrators = fetch_all("content_narrator", "id, created_at")
        comments = db.fetch_all(
            "SELECT id, created_at FROM content_comment WHERE audit_status = 'approved'"
        )
        targets = (
            [("album", row["id"], row["created_at"]) for row in albums[:5000]]
            + [("track", row["id"], row["created_at"]) for row in tracks[:10000]]
            + [("narrator", row["id"], row["created_at"]) for row in narrators[:3000]]
            + [("comment", row["id"], row["created_at"]) for row in comments[:10000]]
        )
        total = min(len(users) * 4, len(targets) * 3)
        seen: set[tuple[int, str, int, str]] = set()

        def rows():
            for index in range(total):
                user = users[index % len(users)]
                target_type, target_id, target_created_at = targets[
                    (index * 19) % len(targets)
                ]
                reaction_type = self.random.choices(
                    ("like", "share", "forward", "dislike"), weights=(72, 15, 10, 3)
                )[0]
                key = (user["id"], target_type, target_id, reaction_type)
                if key in seen:
                    continue
                seen.add(key)
                created_at = self.random_datetime(
                    max(user["created_at"], target_created_at), self.now
                )
                yield {
                    "user_id": user["id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "reaction_type": reaction_type,
                    "reaction_status": "cancelled" if index % 31 == 0 else "active",
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }

        return self.stream_rows(
            "user_reaction",
            rows(),
            total_rows=None,
            build_step_name="build user_reaction",
        )

    def generate_reports(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        comments = fetch_all("content_comment", "id, created_at")
        albums = fetch_all("audio_album", "id, IFNULL(published_at, created_at) AS created_at")
        targets = [("comment", row["id"], row["created_at"]) for row in comments[:5000]]
        targets.extend(("album", row["id"], row["created_at"]) for row in albums[:2000])
        total = max(1, len(targets) // 20)

        def rows():
            for index in range(1, total + 1):
                user = users[index % len(users)]
                target_type, target_id, target_created_at = targets[index % len(targets)]
                created_at = self.random_datetime(
                    max(user["created_at"], target_created_at), self.now
                )
                handled = index % 4 != 0
                yield {
                    "report_no": f"RPT{index:012d}",
                    "user_id": user["id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "report_reason": self.random.choice(
                        ("copyright", "illegal", "violent", "spam", "other")
                    ),
                    "report_text": "内容需要平台复核。",
                    "handle_status": "accepted" if handled else "pending",
                    "handled_at": self.random_datetime(created_at, self.now) if handled else None,
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }

        return self.stream_rows(
            "content_report",
            rows(),
            total_rows=total,
            build_step_name="build content_report",
        )

    def generate_activity_feeds(self) -> int:
        creators = fetch_all("creator_profile", "id, user_id, created_at")
        updates = fetch_all("album_update_record", "album_id, track_id, update_title, updated_at_event")
        rows: list[dict[str, Any]] = []
        for index, update in enumerate(updates[: max(100, len(creators))], start=1):
            creator = creators[index % len(creators)]
            published_at = max(update["updated_at_event"], creator["created_at"])
            rows.append(
                {
                    "feed_no": f"FED{index:012d}",
                    "actor_user_id": creator["user_id"],
                    "creator_id": creator["id"],
                    "feed_type": "publish_track" if update["track_id"] else "publish_album",
                    "target_type": "track" if update["track_id"] else "album",
                    "target_id": update["track_id"] or update["album_id"],
                    "feed_title": update["update_title"],
                    "feed_content": "内容已更新。",
                    "visibility": "public",
                    "published_at": published_at,
                    "created_at": published_at,
                    "updated_at": self.random_datetime(published_at, self.now),
                }
            )
        return self.insert_rows("user_activity_feed", rows)

    def generate_support_tickets(self) -> int:
        users = fetch_all("user_account", "id, mobile, email, created_at")
        orders = fetch_all("content_order", "id, created_at, updated_at")
        total = max(100, len(users) // 30)

        def rows():
            for index in range(1, total + 1):
                user = users[index % len(users)] if index % 9 != 0 else None
                order = orders[index % len(orders)] if index % 4 == 0 else None
                floor_at = user["created_at"] if user else self.now - timedelta(days=120)
                if order:
                    floor_at = max(floor_at, order["updated_at"] or order["created_at"])
                submitted_at = self.random_datetime(floor_at, self.now)
                handled = index % 5 != 0
                yield {
                    "ticket_no": f"TCK{index:012d}",
                    "user_id": user["id"] if user else None,
                    "ticket_type": "payment_issue" if order else "usage_feedback",
                    "related_type": "content_order" if order else "none",
                    "related_id": order["id"] if order else None,
                    "ticket_title": "听书问题反馈",
                    "ticket_content": "请协助确认相关问题。",
                    "contact_mobile": user["mobile"] if user else f"15{index:09d}",
                    "contact_email": user["email"] if user else f"guest{index}@example.com",
                    "ticket_status": "resolved" if handled else "submitted",
                    "handle_result": "已处理" if handled else None,
                    "submitted_at": submitted_at,
                    "handled_at": self.random_datetime(submitted_at, self.now) if handled else None,
                    "closed_at": self.random_datetime(submitted_at, self.now) if handled else None,
                    "created_at": submitted_at,
                    "updated_at": self.random_datetime(submitted_at, self.now),
                }

        return self.stream_rows(
            "support_ticket",
            rows(),
            total_rows=total,
            build_step_name="build support_ticket",
        )

    def generate_messages(self) -> int:
        users = fetch_all("user_account", "id, created_at")
        tickets = db.fetch_all(
            "SELECT id, user_id, created_at FROM support_ticket WHERE user_id IS NOT NULL"
        )
        orders = fetch_all("content_order", "id, user_id, paid_at, created_at, updated_at")
        rows: list[dict[str, Any]] = []
        index = 1
        for order in orders[: len(users)]:
            floor_at = order["paid_at"] or order["updated_at"] or order["created_at"]
            sent_at = self.random_datetime(floor_at, self.now)
            rows.append(
                {
                    "message_no": f"MSG{index:012d}",
                    "sender_user_id": None,
                    "receiver_user_id": order["user_id"],
                    "message_type": "trade",
                    "target_type": "content_order",
                    "target_id": order["id"],
                    "message_title": "订单通知",
                    "message_content": "您的订单状态已更新。",
                    "read_status": "read" if index % 2 else "unread",
                    "sent_at": sent_at,
                    "read_at": self.random_datetime(sent_at, self.now) if index % 2 else None,
                    "created_at": sent_at,
                    "updated_at": self.random_datetime(sent_at, self.now),
                }
            )
            index += 1
        for ticket in tickets:
            sent_at = self.random_datetime(ticket["created_at"], self.now)
            rows.append(
                {
                    "message_no": f"MSG{index:012d}",
                    "sender_user_id": None,
                    "receiver_user_id": ticket["user_id"],
                    "message_type": "system",
                    "target_type": "support_ticket",
                    "target_id": ticket["id"],
                    "message_title": "工单通知",
                    "message_content": "您的工单已有处理进展。",
                    "read_status": "unread",
                    "sent_at": sent_at,
                    "read_at": None,
                    "created_at": sent_at,
                    "updated_at": sent_at,
                }
            )
            index += 1
        return self.insert_rows("user_message", rows)

    def update_behavior_stats(self) -> None:
        db.execute(
            """
            UPDATE audio_track AS t
            LEFT JOIN (
                SELECT track_id, COUNT(*) AS c
                FROM play_session
                WHERE play_status IN ('completed', 'interrupted')
                GROUP BY track_id
            ) AS p ON p.track_id = t.id
            SET t.play_count = GREATEST(t.play_count, IFNULL(p.c, 0))
            """
        )
        db.execute(
            """
            UPDATE audio_album AS a
            LEFT JOIN (
                SELECT album_id, COUNT(*) AS c
                FROM play_session
                WHERE play_status IN ('completed', 'interrupted')
                GROUP BY album_id
            ) AS p ON p.album_id = a.id
            LEFT JOIN (
                SELECT album_id, AVG(rating_score) AS score
                FROM content_rating
                GROUP BY album_id
            ) AS r ON r.album_id = a.id
            SET
                a.play_count = GREATEST(a.play_count, IFNULL(p.c, 0)),
                a.rating_score = IFNULL(ROUND(r.score, 2), a.rating_score)
            """
        )
        db.execute(
            """
            UPDATE content_comment AS c
            LEFT JOIN (
                SELECT target_id, COUNT(*) AS likes
                FROM user_reaction
                WHERE target_type = 'comment'
                  AND reaction_type = 'like'
                  AND reaction_status = 'active'
                GROUP BY target_id
            ) AS r ON r.target_id = c.id
            SET c.like_count = IFNULL(r.likes, 0)
            """
        )
