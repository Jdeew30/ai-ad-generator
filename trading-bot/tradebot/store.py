"""Persistance SQLite : positions, historique des trades, capital, journal, état."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Position, Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY, qty REAL, entry_price REAL, stop REAL, target REAL,
    highest REAL, opened_at INTEGER, entry_fee REAL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, qty REAL, entry_price REAL,
    exit_price REAL, opened_at INTEGER, closed_at INTEGER, pnl REAL, reason TEXT, fees REAL
);
CREATE TABLE IF NOT EXISTS equity (ts INTEGER PRIMARY KEY, value REAL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, level TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    """Accès thread-safe : le moteur écrit, le tableau de bord lit."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _write(self, sql: str, args: tuple = ()) -> None:
        with self._lock, self._db:
            self._db.execute(sql, args)

    # --- positions ---
    def positions(self) -> list[Position]:
        return [Position(**dict(r)) for r in self._query("SELECT * FROM positions ORDER BY opened_at")]

    def get_position(self, symbol: str) -> Position | None:
        rows = self._query("SELECT * FROM positions WHERE symbol = ?", (symbol,))
        return Position(**dict(rows[0])) if rows else None

    def save_position(self, pos: Position) -> None:
        d = asdict(pos)
        self._write(f"INSERT OR REPLACE INTO positions ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
                    tuple(d.values()))

    def delete_position(self, symbol: str) -> None:
        self._write("DELETE FROM positions WHERE symbol = ?", (symbol,))

    # --- trades ---
    def add_trade(self, trade: Trade) -> None:
        d = asdict(trade)
        self._write(f"INSERT INTO trades ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})", tuple(d.values()))

    def trades(self, limit: int | None = None, since: int | None = None) -> list[Trade]:
        sql, args = "SELECT * FROM trades", ()
        if since is not None:
            sql, args = sql + " WHERE closed_at >= ?", (since,)
        sql += " ORDER BY id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [Trade(**{k: r[k] for k in r.keys() if k != "id"}) for r in self._query(sql, args)]

    # --- capital ---
    def add_equity(self, ts: int, value: float) -> None:
        self._write("INSERT OR REPLACE INTO equity (ts, value) VALUES (?, ?)", (ts, value))

    def equity_curve(self, max_points: int = 500) -> list[tuple[int, float]]:
        rows = [(r["ts"], r["value"]) for r in self._query("SELECT ts, value FROM equity ORDER BY ts")]
        if len(rows) <= max_points:
            return rows
        step = len(rows) / max_points
        return [rows[int(i * step)] for i in range(max_points)] + [rows[-1]]

    # --- journal ---
    def log_event(self, level: str, message: str) -> None:
        self._write("INSERT INTO events (ts, level, message) VALUES (?, ?, ?)", (now_ms(), level, message))
        self._write("DELETE FROM events WHERE id <= (SELECT MAX(id) - 2000 FROM events)")

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self._query("SELECT ts, level, message FROM events ORDER BY id DESC LIMIT ?",
                                             (limit,))]

    # --- état clé/valeur ---
    def get(self, key: str, default: Any = None) -> Any:
        rows = self._query("SELECT value FROM state WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set(self, key: str, value: Any) -> None:
        self._write("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # --- commandes (tableau de bord -> moteur) ---
    def push_command(self, command: str) -> None:
        self._write("INSERT INTO commands (command) VALUES (?)", (command,))

    def pop_commands(self) -> list[str]:
        with self._lock, self._db:
            rows = self._db.execute("SELECT id, command FROM commands ORDER BY id").fetchall()
            self._db.execute("DELETE FROM commands")
        return [r["command"] for r in rows]

    def close(self) -> None:
        self._db.close()

    def __del__(self) -> None:
        try:
            self._db.close()
        except Exception:
            pass
