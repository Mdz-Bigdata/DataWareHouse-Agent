"""Base class for data generators."""

from __future__ import annotations

import random as random_module
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import LAYERS
from ..db import db
from ..insert_support import insert_dict_rows, insert_dict_rows_stream
from ..progress import (
    complete_progress_tasks,
    console_print,
    is_table_completed,
    reset_progress_tasks,
)

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class BaseGenerator(ABC):
    layer: int = 0
    layer_name: str = ""
    random: random_module.Random

    def local_now(self) -> datetime:
        return datetime.now(LOCAL_TZ).replace(tzinfo=None, microsecond=0)

    def log(self, message: str) -> None:
        console_print(message)

    def header(self) -> None:
        reset_progress_tasks()
        name = self.layer_name or LAYERS[self.layer]["name"]
        console_print(f"\n{'=' * 64}")
        console_print(f"Layer {self.layer}: {name}")
        console_print(f"{'=' * 64}")

    def clear_layer_tables(self) -> None:
        db.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in reversed(LAYERS[self.layer]["tables"]):
                db.execute(f"TRUNCATE TABLE `{table}`")
        finally:
            db.execute("SET FOREIGN_KEY_CHECKS = 1")

    def insert_rows(self, table_name: str, rows: list[dict]) -> int:
        return insert_dict_rows(table_name, rows)

    def stream_rows(
        self,
        table_name: str,
        rows,
        *,
        total_rows: int | None = None,
        build_step_name: str | None = None,
    ) -> int:
        return insert_dict_rows_stream(
            table_name,
            rows,
            total_rows=total_rows,
            build_step_name=build_step_name,
        )

    def log_table_counts(self, counts: dict[str, int]) -> None:
        for table in LAYERS[self.layer]["tables"]:
            if not is_table_completed(table):
                console_print(f"  [OK] {table}: {counts.get(table, 0)} rows")
        complete_progress_tasks()

    def random_datetime(
        self, start: datetime | date, end: datetime | date | None = None
    ) -> datetime:
        start_dt = (
            datetime.combine(start, datetime.min.time())
            if isinstance(start, date) and not isinstance(start, datetime)
            else start
        )
        end_value = end or self.local_now()
        end_dt = (
            datetime.combine(end_value, datetime.min.time())
            if isinstance(end_value, date) and not isinstance(end_value, datetime)
            else end_value
        )
        seconds = max(0, int((end_dt - start_dt).total_seconds()))
        return start_dt + timedelta(seconds=self.random.randint(0, seconds))

    @abstractmethod
    def run(self) -> None:
        """Run generator."""
