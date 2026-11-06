import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from clipper import auth
from clipper.auth import AuthError, code_from_redirect, pkce_pair, tiktok_authorize_url, youtube_refresh_token
from clipper.cli import main


def test_pkce_pair_follows_rfc7636():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_code_from_redirect():
    url = "https://example.com/callback?code=abc%2A123&scopes=video.upload&state=xyz"
    assert code_from_redirect(url, "xyz") == "abc*123"
    assert code_from_redirect("  just-the-code ") == "just-the-code"
    with pytest.raises(AuthError, match="state"):
        code_from_redirect(url, "other")
    with pytest.raises(AuthError, match="access_denied"):
        code_from_redirect("https://example.com/cb?error=access_denied&code=", None)


def test_tiktok_authorize_url():
    query = parse_qs(urlparse(tiktok_authorize_url("key", "https://me.example/cb", "st")).query)
    assert query["client_key"] == ["key"] and query["response_type"] == ["code"]
    assert query["scope"] == ["user.info.basic,video.upload"] and query["redirect_uri"] == ["https://me.example/cb"]
    direct = parse_qs(urlparse(tiktok_authorize_url("key", "https://me.example/cb", "st", mode="direct")).query)
    assert direct["scope"] == ["user.info.basic,video.upload,video.publish"]


def test_youtube_loopback_flow(server, http, monkeypatch):
    server.route("POST", "/token", (200, {}, {"access_token": "a", "refresh_token": "1//refresh"}))
    monkeypatch.setattr(auth, "GOOGLE_TOKEN_URL", f"{server.url}/token")

    def browser(url: str) -> None:  # plays the user: consent, then Google redirects to the loopback server
        query = parse_qs(urlparse(url).query)
        assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
        redirect = query["redirect_uri"][0]
        requests.get(f"{redirect}/favicon.ico", timeout=10)
        requests.get(redirect, params={"code": "4/code", "state": query["state"][0]}, timeout=10)

    monkeypatch.setattr(auth.webbrowser, "open", browser)
    tokens = youtube_refresh_token("cid", "csecret", http, timeout=20)
    assert tokens["refresh_token"] == "1//refresh"
    form = server.calls("POST", "/token")[0].form()
    assert form["code"] == "4/code" and form["grant_type"] == "authorization_code" and len(form["code_verifier"]) >= 43
    assert form["redirect_uri"].startswith("http://127.0.0.1:")


def test_cli_doctor_and_help(capsys):
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "configuration valide" in output and "ffmpeg avec libass" in output
    with pytest.raises(SystemExit):
        main(["--help"])


def test_cli_reports_config_errors(tmp_path, capsys):
    bad = tmp_path / "config.yaml"
    bad.write_text("shorts:\n  layout: diagonal\n", encoding="utf-8")
    assert main(["--config", str(bad), "run", "--dry-run"]) == 2


def test_cli_lists_the_other_clips_of_a_moment(monkeypatch, capsys):
    from clipper.sources import twitch

    class FakeApi:
        def __init__(self, *args, **kwargs):
            pass

        def clip(self, slug):
            return {"id": slug, "broadcaster_id": "7", "broadcaster_name": "Inoxtag", "title": "annonce skin",
                    "created_at": "2026-10-24T19:31:23Z", "duration": 30, "view_count": 1334, "url": f"https://clips.twitch.tv/{slug}"}  # fmt: skip

        def clips(self, broadcaster_id, started_at, ended_at, first):
            return [{"id": "Long60", "created_at": "2026-10-24T19:31:50Z", "duration": 60.2, "view_count": 400,
                     "title": "le skin | en entier", "url": "https://clips.twitch.tv/Long60", "video_id": "", "vod_offset": None}]  # fmt: skip

    monkeypatch.setattr(twitch, "TwitchApi", FakeApi)
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    assert main(["clips", "https://www.twitch.tv/inoxtag/clip/Short30"]) == 0
    out = capsys.readouterr().out
    assert "| 2026-10-24 19:31:23 | 30 s | 1334 | — | annonce skin ← ce clip |" in out
    assert "| 2026-10-24 19:31:50 | 60 s | 400 | — | le skin / en entier | https://clips.twitch.tv/Long60 |" in out
    monkeypatch.delenv("TWITCH_CLIENT_ID")
    assert main(["clips", "Short30"]) == 2


def test_a_scheduled_run_does_nothing_when_another_run_just_did_the_slot(monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone

    from clipper import cli

    now = datetime(2026, 11, 6, 9, 7, tzinfo=timezone.utc)
    ran: list[dict] = []

    class FakeClipper:
        def __init__(self, last):
            self.now, self.last = now, last

        def last_shorts_run(self):
            return self.last

        def run_shorts(self, **kwargs):
            ran.append(kwargs)
            return type("Report", (), {"markdown": lambda self: "## Clipper — shorts", "errors": []})()

    def run(last, *flags):
        monkeypatch.setattr(cli, "_clipper", lambda args: FakeClipper(last))
        return main(["run", "--dry-run", *flags])

    # The backup schedule, 30 min after the regular run: nothing to do.
    assert run(now - timedelta(minutes=30), "--skip-if-ran-within", "60") == 0 and ran == []
    assert "Rien à faire : un passage a déjà commencé il y a 30 min." in capsys.readouterr().out
    # GitHub dropped the regular run (the last one is 2 h old), or never ran, or a run by hand: the work is done.
    assert run(now - timedelta(minutes=150), "--skip-if-ran-within", "60") == 0 and len(ran) == 1
    assert run(None, "--skip-if-ran-within", "60") == 0 and len(ran) == 2
    assert run(now - timedelta(minutes=5)) == 0 and len(ran) == 3
