"""
Tableau de bord web (lecture seule + pause / reprise / tout fermer).

Protégé par mot de passe (authentification HTTP Basic). Il écoute par défaut
sur 127.0.0.1 uniquement : pour y accéder à distance, passez par un tunnel SSH
ou un reverse proxy HTTPS, jamais en HTTP simple sur Internet.
Le tableau de bord ne passe aucun ordre lui-même : il dépose des commandes
que le moteur exécute à son prochain tour.
"""
from __future__ import annotations

import base64
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from .store import Store

COMMANDS = {"pause", "resume", "close_all"}


def summary(store: Store, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    day_start = int(datetime.fromtimestamp(now, tz=timezone.utc)
                    .replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    trades = store.trades()
    return {
        "status": store.get("status") or {},
        "realized_pnl": sum(t.pnl for t in trades),
        "today_pnl": sum(t.pnl for t in trades if t.closed_at >= day_start),
        "trades": len(trades),
        "win_rate": 100 * sum(t.pnl > 0 for t in trades) / len(trades) if trades else 0.0,
    }


def _handler(store: Store, password: str, page: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "TradeBot"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(given.encode(), password.encode())

        def _send(self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(code)
            headers = {
                "Content-Type": content_type, "Content-Length": str(len(body)),
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                           "style-src 'unsafe-inline'; connect-src 'self'; img-src data:",
            } | (extra or {})
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, code: int = 200) -> None:
            self._send(code, json.dumps(data).encode(), "application/json; charset=utf-8")

        def _guard(self) -> bool:
            if self._authorized():
                return True
            time.sleep(0.5)  # ralentit les essais de mot de passe
            self._send(401, b"Authentification requise", "text/plain; charset=utf-8",
                       {"WWW-Authenticate": 'Basic realm="TradeBot", charset="UTF-8"'})
            return False

        def do_GET(self) -> None:
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/summary":
                self._json(summary(store))
            elif path == "/api/trades":
                self._json([t.__dict__ | {"pnl_pct": t.pnl_pct} for t in store.trades(limit=100)])
            elif path == "/api/equity":
                self._json(store.equity_curve())
            elif path == "/api/events":
                self._json(store.events(limit=100))
            else:
                self._json({"error": "introuvable"}, 404)

        def do_POST(self) -> None:
            if not self._guard():
                return
            # Un formulaire d'un autre site ne peut pas ajouter cet en-tête : protection CSRF.
            if self.headers.get("X-Requested-With") != "tradebot":
                return self._json({"error": "requête refusée"}, 403)
            if urlsplit(self.path).path != "/api/command":
                return self._json({"error": "introuvable"}, 404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(min(length, 1024)) or b"{}")
                command = payload.get("command")
            except (ValueError, AttributeError):
                return self._json({"error": "JSON invalide"}, 400)
            if command not in COMMANDS:
                return self._json({"error": "commande inconnue"}, 400)
            store.push_command(command)
            self._json({"ok": True, "command": command})

    return Handler


class Dashboard:
    def __init__(self, store: Store, password: str, host: str = "127.0.0.1", port: int = 8080,
                 brand: str = "TradeBot") -> None:
        if not password:
            raise ValueError("Le tableau de bord exige un mot de passe")
        template = resources.files("tradebot").joinpath("web/dashboard.html").read_text(encoding="utf-8")
        page = template.replace("{{BRAND}}", escape(brand)).encode()
        self.server = ThreadingHTTPServer((host, port), _handler(store, password, page))
        self.server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"

    def start(self) -> Dashboard:
        self._thread = threading.Thread(target=self.server.serve_forever, name="dashboard", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
