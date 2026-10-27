from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"clipper/{__version__}"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class Http:
    """requests.Session with timeouts and bounded retries.

    Only idempotent calls are retried by default (GET/HEAD/PUT/DELETE): replaying a POST could
    publish a video twice, so callers opt in with `retry=True` where a replay is harmless.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def request(
        self,
        method: str,
        url: str,
        *,
        ok: tuple[int, ...] = (200,),
        retry: bool | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        method = method.upper()
        retry = method in {"GET", "HEAD", "PUT", "DELETE"} if retry is None else retry
        attempts = self.retries + 1 if retry else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.session.request(method, url, timeout=timeout or self.timeout, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    delay = min(2 ** attempt * 2, 30)
                    log.warning("Réseau : %s %s a échoué (%s), nouvel essai dans %ss", method, _short(url), exc, delay)
                    self.sleep(delay)
                    continue
                raise HttpError(f"{method} {_short(url)} : erreur réseau ({exc})") from exc
            if response.status_code in ok:
                return response
            if response.status_code in RETRY_STATUSES and attempt + 1 < attempts:
                delay = _retry_delay(response, attempt)
                log.warning("%s %s -> HTTP %s, nouvel essai dans %.0fs", method, _short(url), response.status_code, delay)
                self.sleep(delay)
                continue
            raise HttpError(
                f"{method} {_short(url)} -> HTTP {response.status_code} : {_snippet(response)}",
                status=response.status_code,
                body=response.text[:4000],
            )
        raise HttpError(f"{method} {_short(url)} : échec ({last_error})")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", url, **kwargs)


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 120.0)
        except ValueError:
            pass
    reset = response.headers.get("Ratelimit-Reset")  # Twitch: epoch seconds
    if reset:
        try:
            return min(max(float(reset) - time.time(), 1.0), 120.0)
        except ValueError:
            pass
    return float(min(2 ** attempt * 2, 30))


def _short(url: str) -> str:
    return url.split("?", 1)[0]


def _snippet(response: requests.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    return text[:500] if text else response.reason or ""
