"""Mémoire du bot (SQLite) : annonces vues, prix observés, alertes envoyées."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    watch TEXT, id INTEGER, title TEXT, price REAL, condition TEXT, url TEXT,
    comparable INTEGER, first_seen REAL, notified INTEGER DEFAULT 0,
    PRIMARY KEY (watch, id)
);
CREATE INDEX IF NOT EXISTS items_market ON items (watch, comparable, first_seen);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def known_prices(self, watch: str, ids: list[int]) -> dict[int, float]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._db.execute(f"SELECT id, price FROM items WHERE watch = ? AND id IN ({marks})",
                                    (watch, *ids)).fetchall()
        return dict(rows)

    def record(self, watch: str, listing: Listing, comparable: bool, now: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO items (watch, id, title, price, condition, url, comparable, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (watch, id) DO UPDATE SET "
                "price = excluded.price, title = excluded.title, comparable = excluded.comparable",
                (watch, listing.id, listing.title, listing.price, listing.condition, listing.url,
                 int(comparable), now))

    def market_prices(self, watch: str, since: float) -> list[float]:
        with self._lock:
            rows = self._db.execute("SELECT price FROM items WHERE watch = ? AND comparable = 1 AND first_seen >= ?",
                                    (watch, since)).fetchall()
        return [r[0] for r in rows]

    def was_notified(self, watch: str, item_id: int) -> bool:
        with self._lock:
            row = self._db.execute("SELECT notified FROM items WHERE watch = ? AND id = ?", (watch, item_id)).fetchone()
        return bool(row and row[0])

    def mark_notified(self, watch: str, item_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE items SET notified = 1 WHERE watch = ? AND id = ?", (watch, item_id))

    def counts(self, watch: str) -> tuple[int, int]:
        """(annonces vues, alertes envoyées)."""
        with self._lock:
            row = self._db.execute("SELECT COUNT(*), COALESCE(SUM(notified), 0) FROM items WHERE watch = ?",
                                   (watch,)).fetchone()
        return int(row[0]), int(row[1])

    def prune(self, before: float) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM items WHERE first_seen < ?", (before,))

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def close(self) -> None:
        self._db.close()

    def __del__(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
