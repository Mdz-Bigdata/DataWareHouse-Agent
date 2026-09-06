"""Layer4: trade, entitlement and wallet data."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

from ..config import GENERATION_DEFAULTS, LAYERS
from ..db import db
from ..progress import progress_range, start_table_progress
from .base import BaseGenerator
from .common import decimal_amount, fetch_all, json_dumps
from .seed_importer import SeedImporter
from .validations import validate_layer4


class Layer4Generator(BaseGenerator):
    layer = 4
    layer_name = "交易权益与资金"

    def __init__(self) -> None:
        self.random = random.Random(int(GENERATION_DEFAULTS["seed"]))
        self.now = self.local_now()
        self.seed = SeedImporter()

    def run(self) -> None:
        self.header()
        self.clear_layer_tables()

        counts = {table: 0 for table in LAYERS[self.layer]["tables"]}
        counts["vip_plan"] = self.import_vip_plans()
        counts["wallet_account"] = self.generate_wallets()
        counts["recharge_order"] = self.generate_recharge_orders()
        order_specs = self.generate_content_orders()
        counts["content_order"] = order_specs["orders"]
        counts["content_order_item"] = order_specs["items"]
        counts["payment_record"] = self.generate_payments()
        counts["entitlement_record"] = self.generate_entitlements()
        refund_specs = self.generate_refunds()
        counts["refund_record"] = refund_specs["refunds"]
        counts["refund_record_item"] = refund_specs["items"]
        counts["wallet_ledger"] = self.generate_wallet_ledgers()
        self.update_wallets_and_members()

        self.log_table_counts(counts)
        for check in validate_layer4():
            self.log(f"  [OK] validation: {check}")

    def import_vip_plans(self) -> int:
        rows: list[dict[str, Any]] = []
        plan_start_at = self.now - timedelta(days=365)
        for index, source in enumerate(
            self.seed.load_csv("4_trade/vip_plan.csv"), start=1
        ):
            duration_months = int(source["duration_months"])
            price = decimal_amount(source["price_amount"])
            created_at = plan_start_at + timedelta(days=index * 7)
            if created_at > self.now:
                created_at = self.now - timedelta(days=1)
            rows.append(
                {
                    "plan_code": source["plan_code"],
                    "plan_name": source["plan_name"],
                    "member_level": source["plan_type"],
                    "duration_days": duration_months * 31,
                    "currency_code": source["currency_code"],
                    "sale_price_amount": price,
                    "original_price_amount": (price * Decimal("1.25")).quantize(
                        Decimal("0.01")
                    ),
                    "benefit_payload": json_dumps(
                        {
                            "ad_free": True,
                            "vip_free_albums": True,
                            "download_quota": 300 * duration_months,
                        }
                    ),
                    "yn": int(source["yn"] or 1),
                    "created_at": created_at,
                    "updated_at": self.random_datetime(created_at, self.now),
                }
            )
        return self.insert_rows("vip_plan", rows)

    def generate_wallets(self) -> int:
        users = fetch_all("user_account", "id, created_at")

        def rows():
            for index, user in enumerate(users, start=1):
                opened_at = self.random_datetime(user["created_at"], self.now)
                yield {
                    "user_id": user["id"],
                    "currency_code": "CNY",
                    "wallet_status": "frozen" if index % 97 == 0 else "active",
                    "balance_amount": Decimal("0.00"),
                    "frozen_amount": Decimal("0.00"),
                    "available_amount": Decimal("0.00"),
                    "opened_at": opened_at,
                    "created_at": opened_at,
                    "updated_at": self.random_datetime(opened_at, self.now),
                }

        return self.stream_rows(
            "wallet_account",
            rows(),
            total_rows=len(users),
            build_step_name="build wallet_account",
        )

    def generate_recharge_orders(self) -> int:
        wallets = fetch_all("wallet_account", "id, user_id, currency_code, opened_at")
        channels = fetch_all("dim_channel", "id")
        total = min(int(GENERATION_DEFAULTS["recharge_orders"]), len(wallets) * 3)

        def rows():
            for index in range(1, total + 1):
                wallet = wallets[index % len(wallets)]
                created_at = self.random_datetime(wallet["opened_at"], self.now)
                amount = Decimal(
                    str(self.random.choice((20, 30, 50, 100, 200)))
                ).quantize(Decimal("0.01"))
                status = self.random.choices(
                    ("credited", "paid", "created", "failed", "cancelled"),
                    weights=(76, 7, 6, 5, 6),
                )[0]
                paid_at = (
                    self.random_datetime(created_at, self.now)
                    if status in {"paid", "credited"}
                    else None
                )
                credited_at = (
                    self.random_datetime(paid_at, self.now)
                    if status == "credited" and paid_at
                    else None
                )
                yield {
                    "recharge_no": f"RCH{index:012d}",
                    "user_id": wallet["user_id"],
                    "wallet_id": wallet["id"],
                    "channel_id": channels[index % len(channels)]["id"],
                    "currency_code": wallet["currency_code"],
                    "recharge_amount": amount,
                    "gift_amount": Decimal("5.00") if amount >= 100 else Decimal("0.00"),
                    "payable_amount": amount,
                    "recharge_status": status,
                    "paid_at": paid_at,
                    "credited_at": credited_at,
                    "created_at": created_at,
                    "updated_at": credited_at
                    or paid_at
                    or self.random_datetime(created_at, self.now),
                }

        return self.stream_rows(
            "recharge_order",
            rows(),
            total_rows=total,
            build_step_name="build recharge_order",
        )

    def generate_content_orders(self) -> dict[str, int]:
        users = fetch_all("user_account", "id, created_at")
        channels = fetch_all("dim_channel", "id")
        plans = fetch_all(
            "vip_plan", "id, plan_name, sale_price_amount, duration_days, created_at"
        )
        albums = db.fetch_all(
            """
            SELECT
                a.id,
                a.album_title,
                a.created_at,
                a.published_at,
                p.price_type,
                p.album_price_amount,
                p.track_price_amount,
                p.effective_from
            FROM audio_album AS a
            JOIN album_price_rule AS p ON p.album_id = a.id AND p.yn = 1
            WHERE p.price_type IN ('vip_free', 'album_paid', 'track_paid')
            """
        )
        tracks = fetch_all("audio_track", "id, album_id, track_title, created_at, published_at")
        total = int(GENERATION_DEFAULTS["content_orders"])
        order_rows: list[dict[str, Any]] = []
        item_specs: list[dict[str, Any]] = []
        for index in progress_range("build content_order", total):
            user = users[index % len(users)]
            order_type = self.random.choices(
                ("vip", "album", "track", "bundle"), weights=(30, 34, 28, 8)
            )[0]
            item_type = "vip_plan"
            vip_plan = album = track = None
            item_name = ""
            amount = Decimal("0.00")
            if order_type == "vip":
                vip_plan = plans[index % len(plans)]
                item_name = vip_plan["plan_name"]
                amount = vip_plan["sale_price_amount"]
                available_at = vip_plan["created_at"]
            elif order_type == "album":
                album = albums[index % len(albums)]
                item_type = "album"
                item_name = album["album_title"]
                amount = album["album_price_amount"] or Decimal("19.90")
                available_at = max(
                    album["created_at"],
                    album["published_at"] or album["created_at"],
                    album["effective_from"],
                )
            else:
                track = tracks[index % len(tracks)]
                album = albums[index % len(albums)]
                item_type = "track"
                item_name = track["track_title"]
                amount = album["track_price_amount"] or Decimal("0.99")
                available_at = max(
                    track["created_at"],
                    track["published_at"] or track["created_at"],
                    album["effective_from"],
                )
            discount = Decimal("0.00") if index % 5 else min(amount, Decimal("3.00"))
            payable = max(Decimal("0.00"), amount - discount)
            created_at = self.random_datetime(max(user["created_at"], available_at), self.now)
            paid = index % 10 != 0
            paid_at = self.random_datetime(created_at, self.now) if paid else None
            order_no = f"ORD{index:012d}"
            order_rows.append(
                {
                    "order_no": order_no,
                    "user_id": user["id"],
                    "channel_id": channels[index % len(channels)]["id"],
                    "currency_code": "CNY",
                    "order_type": order_type,
                    "order_status": "paid" if paid else "cancelled",
                    "total_amount": amount,
                    "discount_amount": discount,
                    "payable_amount": payable,
                    "paid_at": paid_at,
                    "created_at": created_at,
                    "updated_at": paid_at or self.random_datetime(created_at, self.now),
                }
            )
            item_specs.append(
                {
                    "order_no": order_no,
                    "item_type": item_type,
                    "vip_plan_id": vip_plan["id"] if vip_plan else None,
                    "album_id": album["id"] if album and item_type == "album" else None,
                    "track_id": track["id"] if track else None,
                    "item_name": item_name,
                    "quantity": 1,
                    "unit_price_amount": amount,
                    "discount_amount": discount,
                    "payable_amount": payable,
                    "created_at": created_at,
                }
            )
        order_count = self.insert_rows("content_order", order_rows)
        order_ids = {
            row["order_no"]: row["id"]
            for row in db.fetch_all("SELECT id, order_no FROM content_order")
        }
        item_rows: list[dict[str, Any]] = []
        for item in item_specs:
            order_no = item["order_no"]
            item_rows.append(
                {
                    "order_id": order_ids[order_no],
                    **{key: value for key, value in item.items() if key != "order_no"},
                }
            )
        item_count = self.insert_rows("content_order_item", item_rows)
        return {"orders": order_count, "items": item_count}

    def generate_payments(self) -> int:
        content_orders = fetch_all(
            "content_order",
            "id, order_no, user_id, currency_code, payable_amount, order_status, paid_at, created_at",
        )
        recharges = fetch_all(
            "recharge_order", "id, recharge_no, currency_code, payable_amount, recharge_status, paid_at, created_at"
        )
        credited_recharges = db.fetch_all(
            """
            SELECT user_id, currency_code, credited_at,
                   recharge_amount + gift_amount AS credited_amount
            FROM recharge_order
            WHERE recharge_status = 'credited'
              AND credited_at IS NOT NULL
            ORDER BY credited_at, id
            """
        )
        external_channels = ("wechat_pay", "alipay", "apple_pay")
        running_balance: dict[tuple[int, str], Decimal] = defaultdict(Decimal)
        rows: list[dict[str, Any]] = []
        index = 1
        recharge_index = 0
        paid_orders = sorted(
            (order for order in content_orders if order["order_status"] == "paid"),
            key=lambda row: (row["paid_at"], row["id"]),
        )
        for order in paid_orders:
            if order["order_status"] != "paid":
                continue
            while (
                recharge_index < len(credited_recharges)
                and credited_recharges[recharge_index]["credited_at"] <= order["paid_at"]
            ):
                recharge = credited_recharges[recharge_index]
                balance_key = (recharge["user_id"], recharge["currency_code"])
                running_balance[balance_key] += recharge["credited_amount"]
                recharge_index += 1

            balance_key = (order["user_id"], order["currency_code"])
            channel = self.random.choice(external_channels)
            if (
                order["payable_amount"] > 0
                and running_balance[balance_key] >= order["payable_amount"]
                and self.random.random() < 0.35
            ):
                channel = "balance"
                running_balance[balance_key] -= order["payable_amount"]
            rows.append(self.payment_row(index, "content_order", order, channel))
            index += 1
        for recharge in recharges:
            if recharge["recharge_status"] not in {"paid", "credited"}:
                continue
            rows.append(
                self.payment_row(
                    index,
                    "recharge_order",
                    recharge,
                    self.random.choice(external_channels),
                )
            )
            index += 1
        return self.insert_rows("payment_record", rows)

    def payment_row(
        self,
        index: int,
        subject_type: str,
        source: dict[str, Any],
        payment_channel: str,
    ) -> dict[str, Any]:
        paid_at = source["paid_at"] or self.random_datetime(source["created_at"], self.now)
        return {
            "payment_no": f"PAY{index:012d}",
            "pay_subject_type": subject_type,
            "pay_subject_id": source["id"],
            "payment_channel": payment_channel,
            "currency_code": source["currency_code"],
            "payment_amount": source["payable_amount"],
            "payment_status": "success",
            "paid_at": paid_at,
            "created_at": source["created_at"],
            "updated_at": paid_at,
        }

    def generate_entitlements(self) -> int:
        items = db.fetch_all(
            """
            SELECT
                o.id AS order_id,
                o.user_id,
                o.paid_at,
                i.item_type,
                i.vip_plan_id,
                i.album_id,
                i.track_id,
                p.duration_days
            FROM content_order AS o
            JOIN content_order_item AS i ON i.order_id = o.id
            LEFT JOIN vip_plan AS p ON p.id = i.vip_plan_id
            WHERE o.order_status = 'paid'
            """
        )
        rows: list[dict[str, Any]] = []
        for item in items:
            if item["item_type"] == "vip_plan":
                target_type = "vip"
                target_id = item["vip_plan_id"]
                valid_to = item["paid_at"] + timedelta(days=int(item["duration_days"]))
                source_type = "vip"
            elif item["item_type"] == "album":
                target_type = "album"
                target_id = item["album_id"]
                valid_to = None
                source_type = "purchase"
            else:
                target_type = "track"
                target_id = item["track_id"]
                valid_to = None
                source_type = "purchase"
            rows.append(
                {
                    "user_id": item["user_id"],
                    "source_type": source_type,
                    "order_id": item["order_id"],
                    "target_type": target_type,
                    "target_id": target_id,
                    "valid_from": item["paid_at"],
                    "valid_to": valid_to,
                    "entitlement_status": "active",
                    "created_at": item["paid_at"],
                    "updated_at": item["paid_at"],
                }
            )
        return self.insert_rows("entitlement_record", rows)

    def generate_refunds(self) -> dict[str, int]:
        payments = db.fetch_all(
            """
            SELECT p.id AS payment_id, p.pay_subject_type, p.pay_subject_id,
                   p.payment_amount, p.paid_at, o.order_status
            FROM payment_record AS p
            LEFT JOIN content_order AS o
              ON o.id = p.pay_subject_id AND p.pay_subject_type = 'content_order'
            WHERE p.payment_status = 'success'
              AND p.payment_amount > 0
              AND p.pay_subject_type = 'content_order'
            LIMIT 3000
            """
        )
        refund_rows: list[dict[str, Any]] = []
        item_specs: list[dict[str, Any]] = []
        for index, payment in enumerate(payments[::12], start=1):
            requested_at = self.random_datetime(payment["paid_at"], self.now)
            status = self.random.choices(
                ("approved", "rejected", "success", "failed"), weights=(25, 25, 40, 10)
            )[0]
            handled_at = self.random_datetime(requested_at, self.now)
            refunded_at = self.random_datetime(handled_at, self.now) if status == "success" else None
            refund_no = f"RFD{index:012d}"
            amount = (payment["payment_amount"] * Decimal("0.8")).quantize(Decimal("0.01"))
            refund_rows.append(
                {
                    "refund_no": refund_no,
                    "refund_subject_type": payment["pay_subject_type"],
                    "refund_subject_id": payment["pay_subject_id"],
                    "payment_id": payment["payment_id"],
                    "refund_reason": "用户申请退款",
                    "refund_amount": amount,
                    "refund_status": status,
                    "requested_at": requested_at,
                    "handled_at": handled_at,
                    "refunded_at": refunded_at,
                    "created_at": requested_at,
                    "updated_at": refunded_at or handled_at,
                }
            )
            item_specs.append(
                {
                    "refund_no": refund_no,
                    "order_id": payment["pay_subject_id"],
                    "refund_amount": amount,
                    "created_at": requested_at,
                }
            )
        refund_count = self.insert_rows("refund_record", refund_rows)
        refund_ids = {
            row["refund_no"]: row["id"]
            for row in db.fetch_all("SELECT id, refund_no FROM refund_record")
        }
        order_items = {
            row["order_id"]: row
            for row in db.fetch_all(
                "SELECT id, order_id, item_type FROM content_order_item"
            )
        }
        item_rows: list[dict[str, Any]] = []
        for spec in item_specs:
            order_item = order_items.get(spec["order_id"])
            if not order_item:
                continue
            item_rows.append(
                {
                    "refund_id": refund_ids[spec["refund_no"]],
                    "order_item_id": order_item["id"],
                    "item_type": order_item["item_type"],
                    "refund_quantity": 1,
                    "refund_amount": spec["refund_amount"],
                    "created_at": spec["created_at"],
                }
            )
        item_count = self.insert_rows("refund_record_item", item_rows)
        db.execute(
            """
            UPDATE entitlement_record AS e
            JOIN refund_record AS r ON r.refund_subject_id = e.order_id
            SET e.entitlement_status = 'revoked', e.updated_at = r.refunded_at
            WHERE r.refund_subject_type = 'content_order'
              AND r.refund_status = 'success'
            """
        )
        return {"refunds": refund_count, "items": item_count}

    def generate_wallet_ledgers(self) -> int:
        start_table_progress("wallet_ledger", 0)
        credited_recharges = db.fetch_all(
            """
            SELECT id, user_id, wallet_id, currency_code, recharge_amount,
                   gift_amount, credited_at, created_at
            FROM recharge_order
            WHERE recharge_status = 'credited'
            """
        )
        balance_payments = db.fetch_all(
            """
            SELECT p.id, o.user_id, w.id AS wallet_id, p.currency_code,
                   p.payment_amount, p.paid_at
            FROM payment_record AS p FORCE INDEX (
                idx_payment_record_status_channel_subject
            )
            JOIN content_order AS o
              ON o.id = p.pay_subject_id
            JOIN wallet_account AS w FORCE INDEX (uk_wallet_account_user_currency)
              ON w.user_id = o.user_id AND w.currency_code = p.currency_code
            WHERE p.payment_status = 'success'
              AND p.payment_channel = 'balance'
              AND p.pay_subject_type = 'content_order'
            """
        )
        balance_refunds = db.fetch_all(
            """
            SELECT r.id, o.user_id, w.id AS wallet_id, p.currency_code,
                   r.refund_amount, r.refunded_at
            FROM refund_record AS r
            JOIN payment_record AS p ON p.id = r.payment_id
            JOIN content_order AS o
              ON o.id = p.pay_subject_id AND p.pay_subject_type = 'content_order'
            JOIN wallet_account AS w
              ON w.user_id = o.user_id AND w.currency_code = p.currency_code
            WHERE r.refund_status = 'success'
              AND p.payment_channel = 'balance'
              AND r.refunded_at IS NOT NULL
            """
        )
        events: list[dict[str, Any]] = []
        running: dict[int, Decimal] = {}
        for recharge in credited_recharges:
            amount = recharge["recharge_amount"] + recharge["gift_amount"]
            events.append(
                {
                    "wallet_id": recharge["wallet_id"],
                    "user_id": recharge["user_id"],
                    "ledger_type": "recharge",
                    "related_type": "recharge_order",
                    "related_id": recharge["id"],
                    "currency_code": recharge["currency_code"],
                    "amount_delta": amount,
                    "frozen_delta": Decimal("0.00"),
                    "created_at": recharge["credited_at"] or recharge["created_at"],
                    "_event_order": 0,
                }
            )
        for payment in balance_payments:
            events.append(
                {
                    "wallet_id": payment["wallet_id"],
                    "user_id": payment["user_id"],
                    "ledger_type": "consume",
                    "related_type": "payment",
                    "related_id": payment["id"],
                    "currency_code": payment["currency_code"],
                    "amount_delta": -payment["payment_amount"],
                    "frozen_delta": Decimal("0.00"),
                    "created_at": payment["paid_at"],
                    "_event_order": 1,
                }
            )
        for refund in balance_refunds:
            events.append(
                {
                    "wallet_id": refund["wallet_id"],
                    "user_id": refund["user_id"],
                    "ledger_type": "refund",
                    "related_type": "refund",
                    "related_id": refund["id"],
                    "currency_code": refund["currency_code"],
                    "amount_delta": refund["refund_amount"],
                    "frozen_delta": Decimal("0.00"),
                    "created_at": refund["refunded_at"],
                    "_event_order": 2,
                }
            )
        sorted_events = sorted(
            events,
            key=lambda item: (
                item["wallet_id"],
                item["created_at"],
                item["_event_order"],
                item["related_id"],
            ),
        )

        def rows():
            for index, event in enumerate(sorted_events, start=1):
                wallet_id = event["wallet_id"]
                balance = running.get(wallet_id, Decimal("0.00")) + event["amount_delta"]
                running[wallet_id] = balance
                yield {
                    "ledger_no": f"LED{index:012d}",
                    **{
                        key: value
                        for key, value in event.items()
                        if not key.startswith("_")
                    },
                    "balance_after": balance,
                    "frozen_after": Decimal("0.00"),
                    "available_after": balance,
                }

        return self.stream_rows(
            "wallet_ledger",
            rows(),
            total_rows=len(sorted_events),
        )

    def update_wallets_and_members(self) -> None:
        db.execute(
            """
            UPDATE wallet_account
            SET balance_amount = 0,
                frozen_amount = 0,
                available_amount = 0
            """
        )
        db.execute(
            """
            UPDATE wallet_account AS w
            JOIN (
                SELECT l.wallet_id, l.balance_after, l.frozen_after, l.available_after
                FROM wallet_ledger AS l
                JOIN (
                    SELECT wallet_id, MAX(id) AS max_id
                    FROM wallet_ledger
                    GROUP BY wallet_id
                ) AS latest ON latest.max_id = l.id
            ) AS last_l ON last_l.wallet_id = w.id
            SET
                w.balance_amount = last_l.balance_after,
                w.frozen_amount = last_l.frozen_after,
                w.available_amount = last_l.available_after
            """
        )
        db.execute(
            """
            UPDATE member_account AS m
            JOIN (
                SELECT
                    user_id,
                    MAX(valid_to) AS valid_to,
                    MAX(valid_from) AS valid_from
                FROM entitlement_record
                WHERE target_type = 'vip'
                  AND entitlement_status = 'active'
                  AND valid_to > %s
                GROUP BY user_id
            ) AS e ON e.user_id = m.user_id
            SET
                m.member_level = 'vip',
                m.member_status = 'active',
                m.valid_from = e.valid_from,
                m.valid_to = e.valid_to,
                m.updated_at = e.valid_from
            """,
            (self.now,),
        )
