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
    assert query["scope"] == ["user.info.basic,video.upload,video.publish"] and query["redirect_uri"] == ["https://me.example/cb"]


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
