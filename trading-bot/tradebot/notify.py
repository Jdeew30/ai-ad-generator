"""Notifications et commandes à distance via Telegram."""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger("tradebot.notify")


class Notifier(Protocol):
    def send(self, text: str) -> None: ...
    def poll_commands(self) -> list[str]: ...


class NullNotifier:
    def send(self, text: str) -> None:
        pass

    def poll_commands(self) -> list[str]:
        return []


class TelegramNotifier:
    """
    Envoie les alertes dans une conversation Telegram et lit les commandes
    (/statut, /pause...) envoyées depuis cette même conversation uniquement.
    """

    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str,
                 opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self._open = opener
        self._offset: int | None = None

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        data = urllib.parse.urlencode(params).encode()
        with self._open(self.API.format(token=self.token, method=method), data=data, timeout=15) as resp:
            return json.load(resp)

    def send(self, text: str) -> None:
        try:
            self._call("sendMessage", {"chat_id": self.chat_id, "text": text[:4000],
                                       "disable_web_page_preview": "true"})
        except Exception as e:  # une alerte ratée ne doit jamais arrêter le bot
            log.warning("Échec d'envoi Telegram : %s", e)

    def poll_commands(self) -> list[str]:
        params: dict[str, Any] = {"timeout": 0}
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            updates = self._call("getUpdates", params).get("result", [])
        except Exception as e:
            log.warning("Lecture des commandes Telegram impossible : %s", e)
            return []
        commands = []
        for update in updates:
            self._offset = update["update_id"] + 1
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            if str((message.get("chat") or {}).get("id")) == self.chat_id and text.startswith("/"):
                commands.append(text.split("@")[0].split()[0].lower())
        return commands
