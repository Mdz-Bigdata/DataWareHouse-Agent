"""Validation checks for generated audio data."""

from __future__ import annotations

from typing import Any

from ..config import LAYERS
from ..db import db


def _count(sql: str, params: Any | None = None) -> int:
    row = db.fetch_one(sql, params)
    if row is None:
        return 0
    return int(next(iter(row.values())))


def _require_non_empty(tables: list[str], label: str) -> None:
    empty = [table for table in tables if _count(f"SELECT COUNT(*) FROM `{table}`") == 0]
    if empty:
        raise ValueError(f"{label} contains empty table(s): {', '.join(empty)}")


def _assert_zero(sql: str, message: str) -> None:
    count = _count(sql)
    if count:
        raise ValueError(f"{message}: {count}")


def validate_layer1() -> list[str]:
    _require_non_empty(LAYERS[1]["tables"], "Layer1")
    orphan_categories = _count(
        """
        SELECT COUNT(*)
        FROM dim_audio_category AS child
        LEFT JOIN dim_audio_category AS parent ON parent.id = child.parent_id
        WHERE child.parent_id IS NOT NULL AND parent.id IS NULL
        """
    )
    if orphan_categories:
        raise ValueError("dim_audio_category contains orphan rows")
    orphan_tags = _count(
        """
        SELECT COUNT(*)
        FROM dim_content_tag AS child
        LEFT JOIN dim_content_tag AS parent ON parent.id = child.parent_id
        WHERE child.parent_id IS NOT NULL AND parent.id IS NULL
        """
    )
    if orphan_tags:
        raise ValueError("dim_content_tag contains orphan rows")
    return [
        "Layer1 tables contain data",
        "category and tag trees are closed",
    ]


def validate_layer2() -> list[str]:
    _require_non_empty(LAYERS[2]["tables"], "Layer2")
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM audio_album AS a
        LEFT JOIN audio_track AS t ON t.album_id = a.id
        WHERE a.album_status = 'published'
          AND t.id IS NULL
        """,
        "published audio_album has no tracks",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM audio_track AS t
        LEFT JOIN track_audio_file AS f ON f.track_id = t.id
            AND f.file_status = 'available'
            AND f.is_current = 1
        WHERE t.track_status = 'published'
          AND f.id IS NULL
        """,
        "published audio_track has no current available audio files",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM audio_album AS a
        LEFT JOIN album_narrator_rel AS r ON r.album_id = a.id
        WHERE a.album_status = 'published'
          AND r.id IS NULL
        """,
        "published audio_album has no narrator rel",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM audio_album AS a
        LEFT JOIN album_price_rule AS p ON p.album_id = a.id AND p.yn = 1
        WHERE a.album_status = 'published'
          AND p.id IS NULL
        """,
        "published audio_album has no active price rule",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM album_price_rule AS p1
        JOIN album_price_rule AS p2
          ON p1.album_id = p2.album_id
         AND p1.id < p2.id
         AND p1.yn = 1
         AND p2.yn = 1
         AND p1.effective_from < IFNULL(p2.effective_to, '9999-12-31')
         AND p2.effective_from < IFNULL(p1.effective_to, '9999-12-31')
        """,
        "album_price_rule has overlapping active periods",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM track_audio_file AS f
        JOIN audio_track AS t ON t.id = f.track_id
        WHERE f.file_status = 'available'
          AND (
              f.file_url IS NULL
              OR f.file_size_bytes <= 0
              OR f.duration_seconds <= 0
              OR ABS(f.duration_seconds - t.duration_seconds) > 5
          )
        """,
        "available track_audio_file has invalid media metadata",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_audit_record AS r
        LEFT JOIN audio_album AS a
          ON r.target_type = 'album' AND a.id = r.target_id
        LEFT JOIN audio_track AS t
          ON r.target_type = 'track' AND t.id = r.target_id
        LEFT JOIN track_audio_file AS f
          ON r.target_type = 'audio_file' AND f.id = r.target_id
        LEFT JOIN content_upload_task AS u
          ON r.target_type = 'upload_task' AND u.id = r.target_id
        LEFT JOIN content_comment AS c
          ON r.target_type = 'comment' AND c.id = r.target_id
        LEFT JOIN creator_profile AS cp
          ON r.target_type = 'creator_profile' AND cp.id = r.target_id
        WHERE (r.target_type = 'album' AND a.id IS NULL)
           OR (r.target_type = 'track' AND t.id IS NULL)
           OR (r.target_type = 'audio_file' AND f.id IS NULL)
           OR (r.target_type = 'upload_task' AND u.id IS NULL)
           OR (r.target_type = 'comment' AND c.id IS NULL)
           OR (r.target_type = 'creator_profile' AND cp.id IS NULL)
        """,
        "content_audit_record has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM audio_track AS t
        JOIN audio_album AS a ON a.id = t.album_id
        WHERE t.created_at < a.created_at
           OR (
               t.published_at IS NOT NULL
               AND t.published_at < t.created_at
           )
        """,
        "audio_track time is earlier than album or own creation",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM track_audio_file AS f
        JOIN audio_track AS t ON t.id = f.track_id
        WHERE f.created_at < t.created_at
        """,
        "track_audio_file is earlier than track creation",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM album_price_rule AS p
        JOIN audio_album AS a ON a.id = p.album_id
        WHERE p.effective_from < a.created_at
           OR p.created_at < a.created_at
           OR (
               p.effective_to IS NOT NULL
               AND p.effective_to <= p.effective_from
           )
        """,
        "album_price_rule has invalid business time",
    )
    return [
        "content supply tables contain data",
        "published content has tracks, narrators, audio files and price rules",
        "price rules do not overlap",
        "content audit polymorphic targets exist",
        "content asset time order is valid",
    ]


def validate_layer3() -> list[str]:
    _require_non_empty(LAYERS[3]["tables"], "Layer3")
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_bookshelf AS b
        LEFT JOIN audio_track AS t ON t.id = b.last_track_id
        WHERE b.last_track_id IS NOT NULL AND t.album_id <> b.album_id
        """,
        "user_bookshelf last_track_id does not belong to album",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_bookshelf AS b
        JOIN audio_track AS t ON t.id = b.last_track_id
        WHERE b.last_position_seconds > t.duration_seconds
        """,
        "user_bookshelf position exceeds track duration",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_preference
        WHERE NOT (
            (
                preference_type = 'category'
                AND category_id IS NOT NULL
                AND tag_id IS NULL
            )
            OR (
                preference_type = 'tag'
                AND category_id IS NULL
                AND tag_id IS NOT NULL
            )
            OR (
                preference_type = 'play_setting'
                AND category_id IS NULL
                AND tag_id IS NULL
            )
        )
        """,
        "user_preference has invalid target shape",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_follow AS f
        LEFT JOIN content_narrator AS n
          ON f.target_type = 'narrator' AND n.id = f.target_id
        LEFT JOIN content_author AS a
          ON f.target_type = 'author' AND a.id = f.target_id
        LEFT JOIN content_organization AS o
          ON f.target_type = 'organization' AND o.id = f.target_id
        WHERE (f.target_type = 'narrator' AND n.id IS NULL)
           OR (f.target_type = 'author' AND a.id IS NULL)
           OR (f.target_type = 'organization' AND o.id IS NULL)
        """,
        "user_follow has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_follow AS f
        JOIN user_account AS u ON u.id = f.user_id
        LEFT JOIN content_narrator AS n
          ON f.target_type = 'narrator' AND n.id = f.target_id
        LEFT JOIN content_author AS a
          ON f.target_type = 'author' AND a.id = f.target_id
        LEFT JOIN content_organization AS o
          ON f.target_type = 'organization' AND o.id = f.target_id
        WHERE f.followed_at < u.created_at
           OR (f.target_type = 'narrator' AND f.followed_at < n.created_at)
           OR (f.target_type = 'author' AND f.followed_at < a.created_at)
           OR (f.target_type = 'organization' AND f.followed_at < o.created_at)
           OR (f.cancelled_at IS NOT NULL AND f.cancelled_at < f.followed_at)
        """,
        "user_follow time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_profile AS p
        JOIN user_account AS u ON u.id = p.user_id
        WHERE p.created_at < u.created_at
        """,
        "user_profile is earlier than user registration",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_bookshelf AS b
        JOIN user_account AS u ON u.id = b.user_id
        JOIN audio_album AS a ON a.id = b.album_id
        WHERE b.created_at < u.created_at
           OR b.created_at < IFNULL(a.published_at, a.created_at)
        """,
        "user_bookshelf is earlier than user or album visibility",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_preference AS p
        JOIN user_account AS u ON u.id = p.user_id
        WHERE p.created_at < u.created_at
        """,
        "user_preference is earlier than user registration",
    )
    return [
        "user side tables contain data",
        "bookshelf progress points to album tracks",
        "preference and follow targets are valid",
        "user side time order is valid",
    ]


def validate_layer4() -> list[str]:
    _require_non_empty(LAYERS[4]["tables"], "Layer4")
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_order AS o
        JOIN (
            SELECT order_id, SUM(payable_amount) AS item_amount
            FROM content_order_item
            GROUP BY order_id
        ) AS i ON i.order_id = o.id
        WHERE ABS(o.payable_amount - i.item_amount) > 0.01
        """,
        "content_order payable amount differs from item rows",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM payment_record AS p
        LEFT JOIN content_order AS o
          ON p.pay_subject_type = 'content_order' AND o.id = p.pay_subject_id
        LEFT JOIN recharge_order AS r
          ON p.pay_subject_type = 'recharge_order' AND r.id = p.pay_subject_id
        WHERE (p.pay_subject_type = 'content_order' AND o.id IS NULL)
           OR (p.pay_subject_type = 'recharge_order' AND r.id IS NULL)
           OR p.pay_subject_type NOT IN ('content_order', 'recharge_order')
        """,
        "payment_record has invalid pay subject",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM payment_record AS p
        LEFT JOIN content_order AS o
          ON p.pay_subject_type = 'content_order' AND o.id = p.pay_subject_id
        LEFT JOIN recharge_order AS r
          ON p.pay_subject_type = 'recharge_order' AND r.id = p.pay_subject_id
        WHERE p.payment_status = 'success'
          AND (
              (
                  p.pay_subject_type = 'content_order'
                  AND (
                      o.order_status <> 'paid'
                      OR ABS(p.payment_amount - o.payable_amount) > 0.01
                      OR p.currency_code <> o.currency_code
                  )
              )
              OR (
                  p.pay_subject_type = 'recharge_order'
                  AND (
                      r.recharge_status NOT IN ('paid', 'credited', 'refunded')
                      OR ABS(p.payment_amount - r.payable_amount) > 0.01
                      OR p.currency_code <> r.currency_code
                  )
              )
          )
        """,
        "successful payment does not match subject amount, currency or status",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM payment_record
        WHERE (
                pay_subject_type = 'recharge_order'
                AND payment_channel NOT IN ('wechat_pay', 'alipay', 'apple_pay')
              )
           OR (
                pay_subject_type = 'content_order'
                AND payment_channel NOT IN (
                    'wechat_pay', 'alipay', 'apple_pay', 'balance', 'coupon'
                )
              )
        """,
        "payment_record channel is invalid for subject type",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_order AS o
        LEFT JOIN payment_record AS p
          ON p.pay_subject_type = 'content_order'
         AND p.pay_subject_id = o.id
         AND p.payment_status = 'success'
        WHERE o.order_status = 'paid'
          AND p.id IS NULL
        """,
        "paid content_order has no successful payment",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_order AS o
        LEFT JOIN entitlement_record AS e
          ON e.order_id = o.id
        WHERE o.order_status = 'paid'
          AND e.id IS NULL
        """,
        "paid content_order has no entitlement",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM entitlement_record AS e
        LEFT JOIN vip_plan AS vp
          ON e.target_type = 'vip' AND vp.id = e.target_id
        LEFT JOIN audio_album AS a
          ON e.target_type = 'album' AND a.id = e.target_id
        LEFT JOIN audio_track AS t
          ON e.target_type = 'track' AND t.id = e.target_id
        WHERE (e.target_type = 'vip' AND vp.id IS NULL)
           OR (e.target_type = 'album' AND a.id IS NULL)
           OR (e.target_type = 'track' AND t.id IS NULL)
           OR e.target_type NOT IN ('vip', 'album', 'track')
        """,
        "entitlement_record has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM refund_record AS r
        JOIN payment_record AS p ON p.id = r.payment_id
        WHERE r.refund_subject_type <> p.pay_subject_type
           OR r.refund_subject_id <> p.pay_subject_id
           OR r.refund_amount > p.payment_amount
        """,
        "refund_record does not match payment subject or amount",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM payment_record AS p
        JOIN (
            SELECT payment_id, SUM(refund_amount) AS refund_amount
            FROM refund_record
            WHERE refund_status = 'success'
            GROUP BY payment_id
        ) AS r ON r.payment_id = p.id
        WHERE r.refund_amount > p.payment_amount
        """,
        "successful refund amount exceeds payment amount",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM refund_record AS r
        JOIN (
            SELECT refund_id, SUM(refund_amount) AS item_amount
            FROM refund_record_item
            GROUP BY refund_id
        ) AS i ON i.refund_id = r.id
        WHERE r.refund_subject_type = 'content_order'
          AND ABS(r.refund_amount - i.item_amount) > 0.01
        """,
        "refund_record amount differs from refund items",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM wallet_ledger AS l
        LEFT JOIN recharge_order AS ro
          ON l.related_type = 'recharge_order' AND ro.id = l.related_id
        LEFT JOIN content_order AS co
          ON l.related_type = 'content_order' AND co.id = l.related_id
        LEFT JOIN payment_record AS p
          ON l.related_type = 'payment' AND p.id = l.related_id
        LEFT JOIN refund_record AS r
          ON l.related_type = 'refund' AND r.id = l.related_id
        WHERE (l.related_type = 'recharge_order' AND ro.id IS NULL)
           OR (l.related_type = 'content_order' AND co.id IS NULL)
           OR (l.related_type = 'payment' AND p.id IS NULL)
           OR (l.related_type = 'refund' AND r.id IS NULL)
        """,
        "wallet_ledger has invalid related object",
    )
    _assert_zero(
        """
        WITH ledger_seq AS (
            SELECT
                wallet_id,
                amount_delta,
                frozen_delta,
                balance_after,
                frozen_after,
                available_after,
                LAG(balance_after, 1, 0) OVER (
                    PARTITION BY wallet_id ORDER BY id
                ) AS prev_balance,
                LAG(frozen_after, 1, 0) OVER (
                    PARTITION BY wallet_id ORDER BY id
                ) AS prev_frozen
            FROM wallet_ledger
        )
        SELECT COUNT(*)
        FROM ledger_seq
        WHERE ROUND(prev_balance + amount_delta, 2) <> balance_after
           OR ROUND(prev_frozen + frozen_delta, 2) <> frozen_after
           OR ROUND(balance_after - frozen_after, 2) <> available_after
        """,
        "wallet_ledger balance sequence is not continuous",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM wallet_ledger
        WHERE balance_after < 0
           OR frozen_after < 0
           OR available_after < 0
        """,
        "wallet_ledger balance is negative",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM wallet_account AS w
        JOIN (
            SELECT l.wallet_id, l.balance_after, l.frozen_after, l.available_after
            FROM wallet_ledger AS l
            JOIN (
                SELECT wallet_id, MAX(id) AS max_id
                FROM wallet_ledger
                GROUP BY wallet_id
            ) AS x ON x.max_id = l.id
        ) AS last_l ON last_l.wallet_id = w.id
        WHERE ABS(w.balance_amount - last_l.balance_after) > 0.01
           OR ABS(w.frozen_amount - last_l.frozen_after) > 0.01
           OR ABS(w.available_amount - last_l.available_after) > 0.01
        """,
        "wallet_account balance does not match latest ledger",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM wallet_account
        WHERE balance_amount < 0
           OR frozen_amount < 0
           OR available_amount < 0
           OR ROUND(balance_amount - frozen_amount, 2) <> available_amount
        """,
        "wallet_account balance is negative or inconsistent",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM payment_record AS p
        LEFT JOIN wallet_ledger AS l
          ON l.related_type = 'payment'
         AND l.related_id = p.id
         AND l.ledger_type = 'consume'
        WHERE p.payment_status = 'success'
          AND p.pay_subject_type = 'content_order'
          AND p.payment_channel = 'balance'
          AND l.id IS NULL
        """,
        "successful balance payment has no wallet consume ledger",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM refund_record AS r
        JOIN payment_record AS p ON p.id = r.payment_id
        LEFT JOIN wallet_ledger AS l
          ON l.related_type = 'refund'
         AND l.related_id = r.id
         AND l.ledger_type = 'refund'
        WHERE r.refund_status = 'success'
          AND p.payment_channel = 'balance'
          AND l.id IS NULL
        """,
        "successful balance refund has no wallet refund ledger",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_order AS o
        JOIN user_account AS u ON u.id = o.user_id
        WHERE o.created_at < u.created_at
           OR (o.paid_at IS NOT NULL AND o.paid_at < o.created_at)
        """,
        "content_order is earlier than user registration or payment precedes order",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM recharge_order AS r
        JOIN wallet_account AS w ON w.id = r.wallet_id
        WHERE r.created_at < w.opened_at
           OR (r.paid_at IS NOT NULL AND r.paid_at < r.created_at)
           OR (r.credited_at IS NOT NULL AND r.credited_at < r.paid_at)
        """,
        "recharge_order time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM payment_record AS p
        LEFT JOIN content_order AS o
          ON p.pay_subject_type = 'content_order' AND o.id = p.pay_subject_id
        LEFT JOIN recharge_order AS r
          ON p.pay_subject_type = 'recharge_order' AND r.id = p.pay_subject_id
        WHERE p.paid_at < p.created_at
           OR (p.pay_subject_type = 'content_order' AND p.created_at < o.created_at)
           OR (p.pay_subject_type = 'recharge_order' AND p.created_at < r.created_at)
        """,
        "payment_record time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM refund_record AS r
        JOIN payment_record AS p ON p.id = r.payment_id
        WHERE r.requested_at < p.paid_at
           OR r.created_at < r.requested_at
           OR (r.handled_at IS NOT NULL AND r.handled_at < r.requested_at)
           OR (r.refunded_at IS NOT NULL AND r.refunded_at < r.handled_at)
        """,
        "refund_record time order is invalid",
    )
    _assert_zero(
        """
        WITH ledger_seq AS (
            SELECT
                wallet_id,
                id,
                created_at,
                LAG(created_at) OVER (
                    PARTITION BY wallet_id ORDER BY id
                ) AS prev_created_at
            FROM wallet_ledger
        )
        SELECT COUNT(*)
        FROM ledger_seq
        WHERE prev_created_at IS NOT NULL
          AND created_at < prev_created_at
        """,
        "wallet_ledger time is not ordered by wallet",
    )
    return [
        "trade tables contain data",
        "content order totals match order items",
        "payments, refunds, entitlements and wallet ledgers are closed",
        "trade and wallet time order is valid",
    ]


def validate_layer5() -> list[str]:
    _require_non_empty(LAYERS[5]["tables"], "Layer5")
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM listening_progress
        WHERE position_seconds > duration_seconds
        """,
        "listening_progress position exceeds duration",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM play_session AS p
        JOIN audio_track AS t ON t.id = p.track_id
        WHERE t.album_id <> p.album_id
        """,
        "play_session track does not belong to album",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM play_session AS p
        JOIN audio_track AS t ON t.id = p.track_id
        JOIN album_price_rule AS pr ON pr.album_id = p.album_id AND pr.yn = 1
        LEFT JOIN member_account AS m
          ON m.user_id = p.user_id
         AND m.member_status = 'active'
         AND m.valid_from <= p.play_start_at
         AND m.valid_to >= p.play_start_at
        LEFT JOIN entitlement_record AS album_e
          ON album_e.user_id = p.user_id
         AND album_e.target_type = 'album'
         AND album_e.target_id = p.album_id
         AND album_e.entitlement_status = 'active'
         AND album_e.valid_from <= p.play_start_at
         AND (album_e.valid_to IS NULL OR album_e.valid_to >= p.play_start_at)
        LEFT JOIN entitlement_record AS track_e
          ON track_e.user_id = p.user_id
         AND track_e.target_type = 'track'
         AND track_e.target_id = p.track_id
         AND track_e.entitlement_status = 'active'
         AND track_e.valid_from <= p.play_start_at
         AND (track_e.valid_to IS NULL OR track_e.valid_to >= p.play_start_at)
        WHERE NOT (
            pr.price_type = 'free'
            OR t.free_flag = 1
            OR t.track_no <= pr.free_track_count
            OR (pr.price_type = 'vip_free' AND m.id IS NOT NULL)
            OR album_e.id IS NOT NULL
            OR track_e.id IS NOT NULL
        )
        """,
        "play_session has no valid playback entitlement",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_comment AS c
        LEFT JOIN audio_album AS a
          ON c.target_type = 'album' AND a.id = c.target_id
        LEFT JOIN audio_track AS t
          ON c.target_type = 'track' AND t.id = c.target_id
        WHERE (c.target_type = 'album' AND a.id IS NULL)
           OR (c.target_type = 'track' AND t.id IS NULL)
        """,
        "content_comment has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_reaction AS r
        LEFT JOIN audio_album AS a
          ON r.target_type = 'album' AND a.id = r.target_id
        LEFT JOIN audio_track AS t
          ON r.target_type = 'track' AND t.id = r.target_id
        LEFT JOIN content_comment AS c
          ON r.target_type = 'comment' AND c.id = r.target_id
        LEFT JOIN content_narrator AS n
          ON r.target_type = 'narrator' AND n.id = r.target_id
        WHERE (r.target_type = 'album' AND a.id IS NULL)
           OR (r.target_type = 'track' AND t.id IS NULL)
           OR (r.target_type = 'comment' AND c.id IS NULL)
           OR (r.target_type = 'narrator' AND n.id IS NULL)
        """,
        "user_reaction has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_report AS r
        LEFT JOIN audio_album AS a
          ON r.target_type = 'album' AND a.id = r.target_id
        LEFT JOIN audio_track AS t
          ON r.target_type = 'track' AND t.id = r.target_id
        LEFT JOIN content_comment AS c
          ON r.target_type = 'comment' AND c.id = r.target_id
        LEFT JOIN content_narrator AS n
          ON r.target_type = 'narrator' AND n.id = r.target_id
        WHERE (r.target_type = 'album' AND a.id IS NULL)
           OR (r.target_type = 'track' AND t.id IS NULL)
           OR (r.target_type = 'comment' AND c.id IS NULL)
           OR (r.target_type = 'narrator' AND n.id IS NULL)
        """,
        "content_report has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM user_message AS m
        LEFT JOIN audio_album AS a
          ON m.target_type = 'album' AND a.id = m.target_id
        LEFT JOIN audio_track AS t
          ON m.target_type = 'track' AND t.id = m.target_id
        LEFT JOIN content_comment AS c
          ON m.target_type = 'comment' AND c.id = m.target_id
        LEFT JOIN content_order AS co
          ON m.target_type = 'content_order' AND co.id = m.target_id
        LEFT JOIN recharge_order AS ro
          ON m.target_type = 'recharge_order' AND ro.id = m.target_id
        LEFT JOIN content_upload_task AS u
          ON m.target_type = 'upload_task' AND u.id = m.target_id
        LEFT JOIN support_ticket AS st
          ON m.target_type = 'support_ticket' AND st.id = m.target_id
        WHERE (m.target_type = 'none' AND m.target_id IS NOT NULL)
           OR (m.target_type <> 'none' AND m.target_id IS NULL)
           OR (m.target_type = 'album' AND a.id IS NULL)
           OR (m.target_type = 'track' AND t.id IS NULL)
           OR (m.target_type = 'comment' AND c.id IS NULL)
           OR (m.target_type = 'content_order' AND co.id IS NULL)
           OR (m.target_type = 'recharge_order' AND ro.id IS NULL)
           OR (m.target_type = 'upload_task' AND u.id IS NULL)
           OR (m.target_type = 'support_ticket' AND st.id IS NULL)
        """,
        "user_message has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM play_session AS p
        JOIN user_account AS u ON u.id = p.user_id
        JOIN audio_track AS t ON t.id = p.track_id
        JOIN audio_album AS a ON a.id = p.album_id
        WHERE p.play_start_at < u.created_at
           OR p.play_start_at < GREATEST(
               t.created_at,
               IFNULL(t.published_at, t.created_at),
               IFNULL(a.published_at, a.created_at)
           )
           OR (p.play_end_at IS NOT NULL AND p.play_end_at < p.play_start_at)
        """,
        "play_session time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_comment AS c
        JOIN user_account AS u ON u.id = c.user_id
        LEFT JOIN audio_album AS a
          ON c.target_type = 'album' AND a.id = c.target_id
        LEFT JOIN audio_track AS t
          ON c.target_type = 'track' AND t.id = c.target_id
        WHERE c.created_at < u.created_at
           OR (
               c.target_type = 'album'
               AND c.created_at < IFNULL(a.published_at, a.created_at)
           )
           OR (
               c.target_type = 'track'
               AND c.created_at < IFNULL(t.published_at, t.created_at)
           )
        """,
        "content_comment time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM support_ticket AS t
        LEFT JOIN user_account AS u ON u.id = t.user_id
        LEFT JOIN content_order AS o
          ON t.related_type = 'content_order' AND o.id = t.related_id
        WHERE (u.id IS NOT NULL AND t.submitted_at < u.created_at)
           OR (
               t.related_type = 'content_order'
               AND t.submitted_at < IFNULL(o.updated_at, o.created_at)
           )
           OR (t.handled_at IS NOT NULL AND t.handled_at < t.submitted_at)
           OR (t.closed_at IS NOT NULL AND t.closed_at < t.submitted_at)
        """,
        "support_ticket time order is invalid",
    )
    return [
        "behavior tables contain data",
        "listening progress is bounded",
        "playback authorization and polymorphic behavior targets are valid",
        "behavior and service time order is valid",
    ]


def validate_layer6() -> list[str]:
    _require_non_empty(LAYERS[6]["tables"], "Layer6")
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM search_keyword_stat AS s
        LEFT JOIN (
            SELECT
                DATE(created_at) AS stat_date,
                channel_id,
                keyword,
                COUNT(*) AS search_count
            FROM search_query_log
            GROUP BY DATE(created_at), channel_id, keyword
        ) AS l
          ON l.stat_date = s.stat_date
         AND l.channel_id = s.channel_id
         AND l.keyword = s.keyword
        WHERE s.search_count <> IFNULL(l.search_count, 0)
        """,
        "search_keyword_stat does not match search_query_log",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM ranking_item AS i
        LEFT JOIN audio_album AS a
          ON i.target_type = 'album' AND a.id = i.target_id
        LEFT JOIN content_narrator AS n
          ON i.target_type = 'narrator' AND n.id = i.target_id
        WHERE (i.target_type = 'album' AND a.id IS NULL)
           OR (i.target_type = 'narrator' AND n.id IS NULL)
        """,
        "ranking_item has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM recommend_item AS r
        LEFT JOIN audio_album AS a
          ON r.target_type = 'album' AND a.id = r.target_id
        LEFT JOIN content_narrator AS n
          ON r.target_type = 'narrator' AND n.id = r.target_id
        LEFT JOIN content_topic AS t
          ON r.target_type = 'topic' AND t.id = r.target_id
        LEFT JOIN ranking_list AS rl
          ON r.target_type = 'ranking' AND rl.id = r.target_id
        WHERE (r.target_type = 'url' AND r.jump_url IS NULL)
           OR (r.target_type <> 'url' AND r.target_id IS NULL)
           OR (r.target_type = 'album' AND a.id IS NULL)
           OR (r.target_type = 'narrator' AND n.id IS NULL)
           OR (r.target_type = 'topic' AND t.id IS NULL)
           OR (r.target_type = 'ranking' AND rl.id IS NULL)
        """,
        "recommend_item has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM recommend_item AS r1
        JOIN recommend_item AS r2
          ON r1.slot_id = r2.slot_id
         AND r1.sort_no = r2.sort_no
         AND r1.id < r2.id
         AND r1.yn = 1
         AND r2.yn = 1
         AND r1.effective_from < IFNULL(r2.effective_to, '9999-12-31')
         AND r2.effective_from < IFNULL(r1.effective_to, '9999-12-31')
        """,
        "recommend_item has overlapping active slot sort periods",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM content_topic_item AS i
        LEFT JOIN audio_album AS a
          ON i.target_type = 'album' AND a.id = i.target_id
        LEFT JOIN content_narrator AS n
          ON i.target_type = 'narrator' AND n.id = i.target_id
        LEFT JOIN ranking_list AS r
          ON i.target_type = 'ranking' AND r.id = i.target_id
        WHERE (i.target_type = 'album' AND a.id IS NULL)
           OR (i.target_type = 'narrator' AND n.id IS NULL)
           OR (i.target_type = 'ranking' AND r.id IS NULL)
        """,
        "content_topic_item has invalid polymorphic target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM search_query_log AS s
        LEFT JOIN audio_album AS a
          ON s.clicked_target_type = 'album' AND a.id = s.clicked_target_id
        LEFT JOIN audio_track AS t
          ON s.clicked_target_type = 'track' AND t.id = s.clicked_target_id
        LEFT JOIN content_narrator AS n
          ON s.clicked_target_type = 'narrator' AND n.id = s.clicked_target_id
        LEFT JOIN content_organization AS o
          ON s.clicked_target_type = 'organization' AND o.id = s.clicked_target_id
        LEFT JOIN content_topic AS ct
          ON s.clicked_target_type = 'topic' AND ct.id = s.clicked_target_id
        WHERE (s.clicked_target_type = 'none' AND s.clicked_target_id IS NOT NULL)
           OR (s.clicked_target_type <> 'none' AND s.clicked_target_id IS NULL)
           OR (s.clicked_target_type = 'album' AND a.id IS NULL)
           OR (s.clicked_target_type = 'track' AND t.id IS NULL)
           OR (s.clicked_target_type = 'narrator' AND n.id IS NULL)
           OR (s.clicked_target_type = 'organization' AND o.id IS NULL)
           OR (s.clicked_target_type = 'topic' AND ct.id IS NULL)
        """,
        "search_query_log has invalid clicked target",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM ranking_item AS i
        LEFT JOIN audio_album AS a
          ON i.target_type = 'album' AND a.id = i.target_id
        LEFT JOIN content_narrator AS n
          ON i.target_type = 'narrator' AND n.id = i.target_id
        WHERE (i.target_type = 'album' AND i.stat_date < DATE(IFNULL(a.published_at, a.created_at)))
           OR (i.target_type = 'narrator' AND i.stat_date < DATE(n.created_at))
           OR DATE(i.created_at) < i.stat_date
        """,
        "ranking_item time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM recommend_item AS r
        JOIN recommend_slot AS s ON s.id = r.slot_id
        LEFT JOIN audio_album AS a
          ON r.target_type = 'album' AND a.id = r.target_id
        LEFT JOIN content_narrator AS n
          ON r.target_type = 'narrator' AND n.id = r.target_id
        LEFT JOIN content_topic AS t
          ON r.target_type = 'topic' AND t.id = r.target_id
        LEFT JOIN ranking_list AS rl
          ON r.target_type = 'ranking' AND rl.id = r.target_id
        WHERE r.effective_from < s.created_at
           OR (r.target_type = 'album' AND r.effective_from < IFNULL(a.published_at, a.created_at))
           OR (r.target_type = 'narrator' AND r.effective_from < n.created_at)
           OR (r.target_type = 'topic' AND r.effective_from < IFNULL(t.published_at, t.created_at))
           OR (r.target_type = 'ranking' AND r.effective_from < rl.created_at)
           OR r.created_at < r.effective_from
           OR (r.effective_to IS NOT NULL AND r.effective_to <= r.effective_from)
        """,
        "recommend_item time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM search_query_log AS s
        JOIN dim_channel AS ch ON ch.id = s.channel_id
        LEFT JOIN user_account AS u ON u.id = s.user_id
        LEFT JOIN audio_album AS a
          ON s.clicked_target_type = 'album' AND a.id = s.clicked_target_id
        LEFT JOIN audio_track AS t
          ON s.clicked_target_type = 'track' AND t.id = s.clicked_target_id
        LEFT JOIN content_narrator AS n
          ON s.clicked_target_type = 'narrator' AND n.id = s.clicked_target_id
        LEFT JOIN content_organization AS o
          ON s.clicked_target_type = 'organization' AND o.id = s.clicked_target_id
        LEFT JOIN content_topic AS ct
          ON s.clicked_target_type = 'topic' AND ct.id = s.clicked_target_id
        WHERE s.created_at < ch.created_at
           OR (u.id IS NOT NULL AND s.created_at < u.created_at)
           OR (s.clicked_target_type = 'album' AND s.created_at < IFNULL(a.published_at, a.created_at))
           OR (s.clicked_target_type = 'track' AND s.created_at < IFNULL(t.published_at, t.created_at))
           OR (s.clicked_target_type = 'narrator' AND s.created_at < n.created_at)
           OR (s.clicked_target_type = 'organization' AND s.created_at < o.created_at)
           OR (s.clicked_target_type = 'topic' AND s.created_at < IFNULL(ct.published_at, ct.created_at))
        """,
        "search_query_log time order is invalid",
    )
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM search_keyword_stat
        WHERE DATE(created_at) < stat_date
           OR updated_at < created_at
        """,
        "search_keyword_stat time order is invalid",
    )
    return [
        "operation tables contain data",
        "search keyword stats match logs",
        "operation polymorphic targets and delivery periods are valid",
        "operation time order is valid",
    ]


def validate_acceptance() -> list[str]:
    all_tables = [table for layer in LAYERS.values() for table in layer["tables"]]
    _require_non_empty(all_tables, "acceptance")
    _assert_zero(
        """
        SELECT COUNT(*)
        FROM (
            SELECT created_at, updated_at FROM dim_audio_category
            UNION ALL SELECT created_at, updated_at FROM dim_content_tag
            UNION ALL SELECT created_at, updated_at FROM dim_channel
            UNION ALL SELECT created_at, updated_at FROM dim_language
            UNION ALL SELECT created_at, updated_at FROM dim_currency
            UNION ALL SELECT created_at, updated_at FROM user_account
            UNION ALL SELECT created_at, updated_at FROM user_profile
            UNION ALL SELECT created_at, updated_at FROM member_account
            UNION ALL SELECT created_at, updated_at FROM content_organization
            UNION ALL SELECT created_at, updated_at FROM content_author
            UNION ALL SELECT created_at, updated_at FROM content_narrator
            UNION ALL SELECT created_at, updated_at FROM creator_profile
            UNION ALL SELECT created_at, updated_at FROM audio_album
            UNION ALL SELECT created_at, updated_at FROM audio_track
            UNION ALL SELECT created_at, updated_at FROM track_audio_file
            UNION ALL SELECT created_at, updated_at FROM content_upload_task
            UNION ALL SELECT created_at, updated_at FROM content_audit_record
            UNION ALL SELECT created_at, updated_at FROM album_price_rule
            UNION ALL SELECT created_at, updated_at FROM recommend_slot
            UNION ALL SELECT created_at, updated_at FROM content_topic
            UNION ALL SELECT created_at, updated_at FROM recommend_item
            UNION ALL SELECT created_at, updated_at FROM content_topic_item
            UNION ALL SELECT created_at, updated_at FROM search_keyword_stat
        ) AS t
        WHERE updated_at < created_at
        """,
        "global updated_at is earlier than created_at",
    )
    return [
        "all planned tables contain data",
        "layer validations passed during generation",
        "global timestamp order is valid",
    ]
