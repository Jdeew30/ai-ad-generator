"""One-time OAuth helpers, run on your own computer: they print the refresh tokens to store as secrets."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .http import Http, HttpError

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
# Inbox drafts only need video.upload; requesting video.publish fails on apps without Direct Post.
TIKTOK_SCOPES = {
    "inbox": ["user.info.basic", "video.upload"],
    "direct": ["user.info.basic", "video.upload", "video.publish"],
}


class AuthError(Exception):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Callback(BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        query = parse_qs(urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {k: v[0] for k, v in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h1>C'est bon !</h1><p>Vous pouvez fermer cet onglet et revenir au terminal.</p>".encode())

    def log_message(self, format: str, *args: Any) -> None:  # silence the default stderr logging
        return


def youtube_refresh_token(
    client_id: str, client_secret: str, http: Http, *, open_browser: bool = True, timeout: float = 300, port: int = 0
) -> dict[str, Any]:
    """Google "installed app" flow with a loopback redirect and PKCE."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    _Callback.result = {}
    server = HTTPServer(("127.0.0.1", port), _Callback)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = GOOGLE_AUTH_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(YOUTUBE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    print("\nOuvrez cette adresse, connectez-vous avec le compte de la chaîne et acceptez :\n")
    print(url + "\n")
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    deadline = time.monotonic() + timeout
    server.timeout = 1
    try:
        while not _Callback.result and time.monotonic() < deadline:
            server.handle_request()  # ignores stray requests such as /favicon.ico
    finally:
        server.server_close()
    result = _Callback.result
    if not result:
        raise AuthError("pas de réponse de Google dans le temps imparti")
    if result.get("error"):
        raise AuthError(f"Google a refusé : {result['error']}")
    if result.get("state") != state:
        raise AuthError("réponse inattendue (state différent) : recommencez")
    try:
        response = http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": result["code"],
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    except HttpError as exc:
        raise AuthError(f"échange du code impossible : {exc}") from exc
    tokens = response.json()
    if not tokens.get("refresh_token"):
        raise AuthError("Google n'a pas renvoyé de refresh token (révoquez l'accès de l'app dans votre compte Google puis recommencez)")
    return tokens


def tiktok_authorize_url(client_key: str, redirect_uri: str, state: str, *, mode: str = "inbox") -> str:
    return TIKTOK_AUTH_URL + "?" + urlencode(
        {
            "client_key": client_key,
            "scope": ",".join(TIKTOK_SCOPES[mode]),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )


def code_from_redirect(pasted: str, state: str | None = None) -> str:
    """Accepts the full redirected URL (or just the code) pasted by the user."""
    pasted = pasted.strip()
    if "code=" not in pasted:
        if not pasted:
            raise AuthError("rien n'a été collé")
        return pasted
    query = parse_qs(urlparse(pasted).query or pasted.split("?", 1)[-1])
    if state is not None and query.get("state", [state])[0] != state:
        raise AuthError("réponse inattendue (state différent) : recommencez")
    if "error" in query:
        raise AuthError(f"TikTok a refusé : {query['error'][0]}")
    return query["code"][0]


def tiktok_tokens(client_key: str, client_secret: str, code: str, redirect_uri: str, http: Http) -> dict[str, Any]:
    try:
        response = http.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except HttpError as exc:
        raise AuthError(f"échange du code impossible : {exc}") from exc
    data = response.json()
    if "refresh_token" not in data:
        raise AuthError(f"{data.get('error', 'erreur')} — {data.get('error_description', '')}")
    return data
