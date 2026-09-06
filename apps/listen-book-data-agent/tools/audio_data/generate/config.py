"""Configuration for audio data generation."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT_DIR.parents[1]
SEEDS_DIR = ROOT_DIR / "seeds"

load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(ROOT_DIR / ".env", override=False)


def _env(primary: str, fallback: str, default: str) -> str:
    return os.getenv(primary) or os.getenv(fallback) or default

DB_CONFIG = {
    "host": _env("AUDIO_DB_HOST", "DB_HOST", "127.0.0.1"),
    "port": int(_env("AUDIO_DB_PORT", "DB_PORT", "23306")),
    "user": _env("AUDIO_DB_USER", "DB_USER", "root"),
    "password": _env("AUDIO_DB_PASSWORD", "DB_PASSWORD", ""),
    "database": _env("AUDIO_DB_NAME", "DB_NAME", "audio"),
    "charset": "utf8mb4",
    "autocommit": False,
}

LAYERS: dict[int, dict[str, Any]] = {
    1: {
        "name": "基础维度与主体主数据",
        "tables": [
            "dim_audio_category",
            "dim_content_tag",
            "dim_channel",
            "dim_language",
            "dim_currency",
            "user_account",
            "content_organization",
            "content_author",
            "content_narrator",
        ],
    },
    2: {
        "name": "内容供给与内容资产",
        "tables": [
            "creator_profile",
            "creator_apply_record",
            "audio_album",
            "album_organization_rel",
            "album_author_rel",
            "album_narrator_rel",
            "album_tag_rel",
            "audio_track",
            "track_audio_file",
            "content_upload_task",
            "content_audit_record",
            "album_update_record",
            "album_price_rule",
        ],
    },
    3: {
        "name": "用户会员与偏好",
        "tables": [
            "user_profile",
            "member_account",
            "user_follow",
            "user_bookshelf",
            "user_preference",
        ],
    },
    4: {
        "name": "交易权益与资金",
        "tables": [
            "vip_plan",
            "wallet_account",
            "recharge_order",
            "content_order",
            "content_order_item",
            "payment_record",
            "refund_record",
            "refund_record_item",
            "entitlement_record",
            "wallet_ledger",
        ],
    },
    5: {
        "name": "播放互动与服务",
        "tables": [
            "play_session",
            "listening_progress",
            "content_comment",
            "content_rating",
            "user_reaction",
            "content_report",
            "user_activity_feed",
            "support_ticket",
            "user_message",
        ],
    },
    6: {
        "name": "运营推荐与搜索",
        "tables": [
            "ranking_list",
            "ranking_item",
            "recommend_slot",
            "content_topic",
            "recommend_item",
            "content_topic_item",
            "search_query_log",
            "search_keyword_stat",
        ],
    },
}

GENERATION_PROFILES: dict[str, dict[str, int]] = {
    "full": {
        "seed": 42,
        "batch_size": 5000,
        "users": 20000,
        "creator_users": 1500,
        "bookshelf_rows": 90000,
        "play_sessions": 300000,
        "content_orders": 60000,
        "recharge_orders": 20000,
        "comments": 50000,
        "search_logs": 120000,
    },
    "smoke": {
        "seed": 42,
        "batch_size": 1000,
        "users": 500,
        "creator_users": 120,
        "bookshelf_rows": 1200,
        "play_sessions": 3000,
        "content_orders": 800,
        "recharge_orders": 300,
        "comments": 800,
        "search_logs": 2000,
    },
}

GENERATION_DEFAULTS = dict(GENERATION_PROFILES["full"])


@contextmanager
def generation_profile(profile: str):
    original = dict(GENERATION_DEFAULTS)
    GENERATION_DEFAULTS.clear()
    GENERATION_DEFAULTS.update(GENERATION_PROFILES[profile])
    try:
        yield GENERATION_DEFAULTS
    finally:
        GENERATION_DEFAULTS.clear()
        GENERATION_DEFAULTS.update(original)
