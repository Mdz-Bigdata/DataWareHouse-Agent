"""Database helpers for generation."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from .config import DB_CONFIG


class Database:
    """Small PyMySQL wrapper used by generators."""

    def __init__(self) -> None:
        self._connection = None

    def get_connection(self):
        if self._connection is None or not self._connection.open:
            self._connection = pymysql.connect(**DB_CONFIG)
        return self._connection

    def close(self) -> None:
        if self._connection and self._connection.open:
            self._connection.close()
        self._connection = None

    def current_connection_id(self) -> int | None:
        if self._connection is None or not self._connection.open:
            return None
        return self._connection.thread_id()

    def kill_current_connection(self) -> None:
        connection_id = self.current_connection_id()
        if connection_id is None:
            self._connection = None
            return

        admin_conn = None
        try:
            admin_conn = pymysql.connect(**DB_CONFIG)
            with admin_conn.cursor() as cursor:
                cursor.execute(f"KILL CONNECTION {connection_id}")
            admin_conn.commit()
        except Exception:
            pass
        finally:
            if admin_conn and admin_conn.open:
                admin_conn.close()
            if self._connection:
                try:
                    self._connection.close()
                except Exception:
                    pass
            self._connection = None

    @contextmanager
    def cursor(self, dict_cursor: bool = True):
        conn = self.get_connection()
        cursor = conn.cursor(DictCursor if dict_cursor else None)
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, params: Any | None = None) -> int:
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.rowcount

    def executemany(self, sql: str, params_list: list[tuple[Any, ...]]) -> int:
        if not params_list:
            return 0
        with self.cursor() as cursor:
            cursor.executemany(sql, params_list)
            return cursor.rowcount

    def fetch_one(self, sql: str, params: Any | None = None) -> dict[str, Any] | None:
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def fetch_all(self, sql: str, params: Any | None = None) -> list[dict[str, Any]]:
        with self.cursor() as cursor:
            cursor.execute(sql, params)
            return list(cursor.fetchall())


db = Database()


def init_db() -> None:
    db.get_connection()
    print(
        f"Database connected: {DB_CONFIG['host']}:{DB_CONFIG['port']}/"
        f"{DB_CONFIG['database']}"
    )


def close_db() -> None:
    db.close()
    print("Database connection closed")


def interrupt_db() -> None:
    db.kill_current_connection()
    print("Database connection interrupted and closed")
