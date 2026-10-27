import anthropic

from clipper.check import markdown, run_checks
from clipper.config import Config, Secrets, TikTokPublishConfig, YouTubePublishConfig, build
from clipper.publishers.telegram import TelegramPublisher
from clipper.publishers.tiktok import TikTokPublisher
from clipper.publishers.youtube import YouTubePublisher
from clipper.sources.twitch import TwitchApi

SECRETS = Secrets(
    twitch_client_id="cid", twitch_client_secret="cs", anthropic_api_key="sk-test",
    youtube_client_id="yid", youtube_client_secret="ys", youtube_refresh_token="yr",
    tiktok_client_key="tk", tiktok_client_secret="ts", tiktok_refresh_token="tr",
    telegram_bot_token="123:abc", telegram_chat_id="42",
    gh_secrets_token="ghp_x", github_repository="me/repo",
)  # fmt: skip


def test_every_configured_service_is_tried_and_reported(server, http):
    server.route("POST", "/twitch/token", (200, {}, {"access_token": "t"}))
    server.route("GET", "/helix/users", (200, {}, {"data": [{"id": "1", "login": "squeezie"}]}))
    server.route("GET", "/v1/models/claude-opus-5-5",
                 (200, {}, {"id": "claude-opus-5-5", "type": "model", "display_name": "Claude Opus 5.5", "created_at": "2026-01-01T00:00:00Z"}))
    server.route("POST", "/google/token", (400, {}, {"error": "invalid_grant"}))
    server.route("POST", "/v2/oauth/token/", (200, {}, {"access_token": "a", "refresh_token": "tr"}))
    server.route("POST", "/bot123:abc/sendMessage", (200, {}, {"ok": True, "result": {"message_id": 1}}))
    server.route("GET", "/repos/me/repo/actions/secrets/public-key", (200, {}, {"key_id": "k", "key": "x"}))
    config = build(Config, {"twitch": {"streamers": ["squeezie", "nobody"]}})

    checks = run_checks(
        config, SECRETS, http,
        twitch=TwitchApi("cid", "cs", http, base_url=f"{server.url}/helix", token_url=f"{server.url}/twitch/token"),
        claude=anthropic.Anthropic(api_key="sk-test", base_url=server.url, max_retries=0),
        youtube=YouTubePublisher(SECRETS, YouTubePublishConfig(), http, token_url=f"{server.url}/google/token"),
        tiktok=TikTokPublisher(SECRETS, TikTokPublishConfig(), http, api_url=f"{server.url}/v2"),
        telegram=TelegramPublisher(SECRETS, http, api_url=server.url),
        github_api_url=server.url,
    )  # fmt: skip
    by_name = {check.name: check for check in checks}
    assert by_name["Twitch (API officielle)"].status == "ok" and "introuvables : nobody" in by_name["Twitch (API officielle)"].detail
    assert by_name["Claude"].status == "ok" and "claude-opus-5-5" in by_name["Claude"].detail
    assert by_name["YouTube"].status == "error" and "7 jours" in by_name["YouTube"].detail
    assert by_name["TikTok"].status == "ok" and "brouillon" in by_name["TikTok"].detail
    assert by_name["Telegram"].status == "ok"
    assert by_name["GitHub (GH_SECRETS_TOKEN)"].status == "ok"
    sent = server.calls("POST", "/bot123:abc/sendMessage")[0].form()
    assert sent["chat_id"] == "42" and "connecté" in sent["text"]
    table = markdown(checks)
    assert "| ❌ | YouTube |" in table and "| ✅ | Telegram |" in table


def test_nothing_configured_is_not_an_error(http):
    checks = run_checks(build(Config, {}), Secrets(), http)
    assert {check.status for check in checks} == {"absent"}
    assert "yt-dlp" in checks[0].detail


def test_cli_check_exit_code(monkeypatch, capsys):
    from clipper import cli

    for name in ("TWITCH_CLIENT_ID", "ANTHROPIC_API_KEY", "YOUTUBE_CLIENT_ID", "TIKTOK_CLIENT_KEY", "TELEGRAM_BOT_TOKEN", "GH_SECRETS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda _path: None)
    assert cli.main(["check"]) == 0
    assert "vérification des branchements" in capsys.readouterr().out
