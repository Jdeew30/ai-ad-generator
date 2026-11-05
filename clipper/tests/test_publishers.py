import base64
from pathlib import Path

import pytest

from clipper.config import Config, Secrets, TikTokPublishConfig, YouTubePublishConfig, build
from clipper.github_secrets import update_secret
from clipper.models import CompilationMeta, PublishResult, ShortMeta
from clipper.publishers import PublishError, build_publishers
from clipper.publishers import telegram as telegram_module
from clipper.publishers import youtube as youtube_module
from clipper.publishers.telegram import TelegramPublisher
from clipper.publishers.tiktok import TikTokPublisher, caption, chunk_plan
from clipper.publishers.youtube import YouTubePublisher, short_description

from .conftest import make_candidate

META = ShortMeta(hook="IL CRAQUE", title="Squeezie craque en live 😂", description="Il n'en peut plus.", hashtags=["squeezie", "clip"])
SECRETS = Secrets(
    youtube_client_id="yid", youtube_client_secret="ysecret", youtube_refresh_token="yrefresh",
    tiktok_client_key="tkey", tiktok_client_secret="tsecret", tiktok_refresh_token="trefresh",
    telegram_bot_token="123:abc", telegram_chat_id="42",
)  # fmt: skip


@pytest.fixture
def video(tmp_path: Path) -> Path:
    path = tmp_path / "short.mp4"
    path.write_bytes(bytes(range(256)) * 4096)  # 1 MiB of recognisable bytes
    return path


def youtube(server, http, **settings) -> YouTubePublisher:
    return YouTubePublisher(
        SECRETS, YouTubePublishConfig(**settings), http, token_url=f"{server.url}/token",
        upload_url=f"{server.url}/upload/youtube/v3/videos", thumbnail_url=f"{server.url}/upload/youtube/v3/thumbnails/set",
    )  # fmt: skip


def test_youtube_resumable_upload_survives_an_interruption(server, http, video, monkeypatch):
    monkeypatch.setattr(youtube_module, "CHUNK", 256 * 1024)
    server.route("POST", "/token", (200, {}, {"access_token": "ya29"}))
    server.route("POST", "/upload/youtube/v3/videos", (200, {"Location": f"{server.url}/session/1"}, {}))
    received = bytearray()
    state = {"failed": False}

    def session(request):
        content_range = request.headers["content-range"]
        if content_range.startswith("bytes */"):  # status query after the failure
            return 308, {"Range": f"bytes=0-{len(received) - 1}"}, b""
        start = int(content_range.split(" ")[1].split("-")[0])
        if start == 512 * 1024 and not state["failed"]:
            state["failed"] = True
            return 503, {}, "backend error"
        assert start == len(received)
        received.extend(request.body)
        if len(received) == video.stat().st_size:
            return 200, {}, {"id": "vid123", "status": {"privacyStatus": "public", "uploadStatus": "uploaded"}}
        return 308, {"Range": f"bytes=0-{len(received) - 1}"}, b""

    server.route("PUT", "/session/1", session)
    result = youtube(server, http).publish_short(video, META, make_candidate("twitch:Slug", "squeezie", clipper="viewer42"))

    assert result == PublishResult("youtube", True, "vid123", "https://www.youtube.com/shorts/vid123", None)
    assert bytes(received) == video.read_bytes()
    [token] = server.calls("POST", "/token")
    assert token.form() == {"client_id": "yid", "client_secret": "ysecret", "refresh_token": "yrefresh", "grant_type": "refresh_token"}
    [init] = server.calls("POST", "/upload/youtube/v3/videos")
    assert init.query["uploadType"] == ["resumable"] and init.headers["x-upload-content-length"] == str(video.stat().st_size)
    body = init.json()
    assert body["snippet"]["title"] == "Squeezie craque en live 😂" and body["snippet"]["categoryId"] == "20"
    assert "twitch.tv/squeezie" in body["snippet"]["description"] and "#shorts" in body["snippet"]["description"]
    assert "clip de viewer42" in body["snippet"]["description"]
    assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False, "embeddable": True}


def test_youtube_explains_an_expired_refresh_token(server, http, video):
    server.route("POST", "/token", (400, {}, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(PublishError, match="7 jours"):
        youtube(server, http).publish_short(video, META, make_candidate())


def test_youtube_quota_error_is_readable(server, http, video):
    server.route("POST", "/token", (200, {}, {"access_token": "ya29"}))
    server.route("POST", "/upload/youtube/v3/videos", (403, {}, {"error": {"errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(PublishError, match="quota quotidien"):
        youtube(server, http).publish_short(video, META, make_candidate())


def test_youtube_compilation_sets_the_thumbnail_and_reports_a_private_lock(server, http, video, tmp_path):
    server.route("POST", "/token", (200, {}, {"access_token": "ya29"}))
    server.route("POST", "/upload/youtube/v3/videos", (200, {"Location": f"{server.url}/session/2"}, {}))
    server.route("PUT", "/session/2", (200, {}, {"id": "comp1", "status": {"privacyStatus": "private"}}))
    server.route("POST", "/upload/youtube/v3/thumbnails/set", (200, {}, {"items": []}))
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpeg")
    meta = CompilationMeta(title="TOP 12", description="d", hashtags=["bestof"], thumbnail_text="TOP 12")
    result = youtube(server, http).publish_compilation(video, meta, "chapitres…", thumb)
    assert result.url == "https://www.youtube.com/watch?v=comp1" and "non audité" in result.detail
    [thumb_call] = server.calls("POST", "/upload/youtube/v3/thumbnails/set")
    assert thumb_call.query["videoId"] == ["comp1"] and thumb_call.body == b"\xff\xd8jpeg"


def test_short_description_credits_the_creator():
    text = short_description(META, make_candidate("twitch:Slug", "gotaga", clipper="fan"))
    assert "Moment original : Gotaga — https://www.twitch.tv/gotaga (clip de fan)" in text
    assert "https://clips.twitch.tv/Slug" in text and text.endswith("#squeezie #clip #shorts")


def tiktok(server, http, **settings) -> TikTokPublisher:
    return TikTokPublisher(SECRETS, TikTokPublishConfig(**settings), http, api_url=f"{server.url}/v2", poll_interval=0)


def tiktok_ok(data: dict) -> dict:
    return {"data": data, "error": {"code": "ok", "message": "", "log_id": "L"}}


def test_tiktok_inbox_upload_and_refresh_token_rotation(server, http, video):
    server.route("POST", "/v2/oauth/token/", (200, {}, {"access_token": "act", "refresh_token": "rotated", "expires_in": 86400}))
    server.route("POST", "/v2/post/publish/inbox/video/init/", (200, {}, tiktok_ok({"publish_id": "p1", "upload_url": f"{server.url}/up/p1"})))
    server.route("PUT", "/up/p1", (201, {}, b""))
    statuses = iter(["PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"])
    server.route("POST", "/v2/post/publish/status/fetch/", lambda _r: (200, {}, tiktok_ok({"status": next(statuses)})))
    saved: list[str] = []
    publisher = tiktok(server, http)
    publisher.on_new_refresh_token = saved.append

    result = publisher.publish_short(video, META, make_candidate("twitch:S", "squeezie"))
    assert result.ok and result.id == "p1" and "brouillon" in result.detail
    assert saved == ["rotated"]
    [token] = server.calls("POST", "/v2/oauth/token/")
    assert token.form()["refresh_token"] == "trefresh" and token.form()["grant_type"] == "refresh_token"
    [init] = server.calls("POST", "/v2/post/publish/inbox/video/init/")
    size = video.stat().st_size
    assert init.json() == {"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}
    assert init.headers["authorization"] == "Bearer act"
    [upload] = server.calls("PUT", "/up/p1")
    assert upload.headers["content-range"] == f"bytes 0-{size - 1}/{size}" and upload.body == video.read_bytes()


def test_tiktok_direct_post_checks_privacy_and_explains_errors(server, http, video):
    server.route("POST", "/v2/oauth/token/", (200, {}, {"access_token": "act", "refresh_token": "trefresh"}))
    server.route("POST", "/v2/post/publish/creator_info/query/", (200, {}, tiktok_ok({"privacy_level_options": ["SELF_ONLY"], "duet_disabled": True})))
    with pytest.raises(PublishError, match="PUBLIC_TO_EVERYONE indisponible"):
        tiktok(server, http, mode="direct").publish_short(video, META, make_candidate())

    server.route("POST", "/v2/post/publish/creator_info/query/", (200, {}, tiktok_ok({"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]})))
    server.route("POST", "/v2/post/publish/video/init/", (403, {}, {"data": {}, "error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "", "log_id": "x"}}))
    with pytest.raises(PublishError, match="inbox"):
        tiktok(server, http, mode="direct").publish_short(video, META, make_candidate())
    post_info = server.calls("POST", "/v2/post/publish/video/init/")[0].json()["post_info"]
    assert post_info["privacy_level"] == "PUBLIC_TO_EVERYONE" and post_info["is_aigc"] is False
    assert post_info["title"].startswith("Squeezie craque en live") and "#squeezie #clip" in post_info["title"]


def test_tiktok_direct_post_success(server, http, video):
    server.route("POST", "/v2/oauth/token/", (200, {}, {"access_token": "act"}))
    server.route("POST", "/v2/post/publish/creator_info/query/", (200, {}, tiktok_ok({"privacy_level_options": ["PUBLIC_TO_EVERYONE"], "stitch_disabled": True})))
    server.route("POST", "/v2/post/publish/video/init/", (200, {}, tiktok_ok({"publish_id": "p2", "upload_url": f"{server.url}/up/p2"})))
    server.route("PUT", "/up/p2", (201, {}, b""))
    server.route("POST", "/v2/post/publish/status/fetch/", (200, {}, tiktok_ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7311]})))
    result = tiktok(server, http, mode="direct").publish_short(video, META, make_candidate())
    assert result.url == "https://www.tiktok.com/video/7311"
    post_info = server.calls("POST", "/v2/post/publish/video/init/")[0].json()["post_info"]
    assert post_info["disable_stitch"] is True and post_info["disable_duet"] is False


def test_tiktok_chunk_plan():
    assert chunk_plan(1000) == (1000, 1)
    assert chunk_plan(64 * 1024 * 1024) == (64 * 1024 * 1024, 1)
    size = 64 * 1024 * 1024 + 5
    chunk, count = chunk_plan(size)
    assert chunk == 10 * 1024 * 1024 and count == 6 and size - chunk * (count - 1) <= 128 * 1024 * 1024
    with pytest.raises(PublishError):
        chunk_plan(0)
    assert caption(META, make_candidate("twitch:a", "squeezie")).endswith("🎥 twitch.tv/squeezie")


def test_telegram_sends_the_video_with_a_ready_to_paste_caption(server, http, video, monkeypatch):
    monkeypatch.setattr(telegram_module, "probe", lambda _p: type("I", (), {"width": 1080, "height": 1920, "duration": 30.0})())
    server.route("POST", "/bot123:abc/sendVideo", (200, {}, {"ok": True, "result": {"message_id": 77}}))
    server.route("POST", "/bot123:abc/sendMessage", (200, {}, {"ok": True, "result": {"message_id": 78}}))
    publisher = TelegramPublisher(SECRETS, http, api_url=server.url)
    published = [PublishResult("youtube", True, "v", "https://www.youtube.com/shorts/v"), PublishResult("tiktok", False, detail="x")]
    result = publisher.publish_short(video, META, make_candidate("twitch:a", "squeezie"), published)
    assert result.ok and result.id == "77"
    [call] = server.calls("POST", "/bot123:abc/sendVideo")
    body = call.body
    assert b'name="chat_id"' in body and b"42" in body and b'filename="squeezie.mp4"' in body
    assert "👤 Squeezie · Twitch · twitch.tv/squeezie".encode() in body and "📝 « Il rage quit en plein live »".encode() in body
    assert "🔗 https://clips.twitch.tv/a".encode() in body and b"youtube : https://www.youtube.com/shorts/v" in body
    # The text to paste comes next, in a block Telegram copies with a tap.
    [paste] = server.calls("POST", "/bot123:abc/sendMessage")
    assert paste.form()["parse_mode"] == "HTML" and paste.form()["text"].endswith(
        "<pre>Squeezie craque en live 😂\n\n#squeezie #clip\n🎥 twitch.tv/squeezie</pre>")


def test_telegram_card_tells_who_where_when_how_much_and_why(server, http, video, monkeypatch):
    from dataclasses import replace

    from .conftest import NOW

    monkeypatch.setattr(telegram_module, "probe", lambda _p: type("I", (), {"width": 1080, "height": 1920, "duration": 40.0})())
    server.route("POST", "/bot123:abc/sendVideo", (200, {}, {"ok": True, "result": {"message_id": 1}}))
    server.route("POST", "/bot123:abc/sendMessage", (200, {}, {"ok": True, "result": {"message_id": 2}}))
    extract = make_candidate("youtube:ZA_3eoib31s@3862-3912", "gotaga", source="youtube", streamer="Gotaga",
                             url="https://www.youtube.com/watch?v=ZA_3eoib31s", channel_url="https://www.youtube.com/@Gotaga",
                             title="QUI SERA LE MEILLEUR MENTEUR ?", views=2_619_615, likes=98_000, comments=1_204,
                             start=3862.0, end=3912.0, created_at=NOW)  # fmt: skip
    meta = replace(META, ai=True, score=72, reason="Joyca, en enfant, lâche la pire insulte : fou rire général.")
    TelegramPublisher(SECRETS, http, api_url=server.url).publish_short(video, meta, extract, [])
    caption = server.calls("POST", "/bot123:abc/sendVideo")[0].body.decode("utf-8", "replace")  # the video is in it too
    for line in ("👤 Gotaga · YouTube · youtube.com/@Gotaga", "📝 « QUI SERA LE MEILLEUR MENTEUR ? »",
                 f"📅 {NOW.astimezone(telegram_module.PARIS):%d/%m/%Y %H:%M} · ⏱ 50 s · passage 1:04:22–1:05:12",
                 "👁 2\u202f619\u202f615 vues · 👍 98\u202f000 · 💬 1\u202f204",
                 "🔥 Score viral estimé : 72/100 — Joyca, en enfant, lâche la pire insulte : fou rire général.",
                 "🔗 https://www.youtube.com/watch?v=ZA_3eoib31s&t=3862s"):  # fmt: skip
        assert line in caption


def test_the_card_says_to_post_a_fresh_moment_right_away():
    from datetime import timedelta

    from clipper.publishers.telegram import card

    from .conftest import NOW

    fresh = make_candidate(created_at=NOW - timedelta(hours=2))
    assert "⚡ Tout frais (il y a 2 h) : à poster maintenant, avant les autres comptes" in card(META, fresh, now=NOW)
    old = make_candidate(created_at=NOW - timedelta(hours=30))
    assert not any(line.startswith("⚡") for line in card(META, old, now=NOW))


def test_telegram_waits_out_flood_control(server, http, video, monkeypatch):
    monkeypatch.setattr(telegram_module, "probe", lambda _p: type("I", (), {"width": 1080, "height": 1920, "duration": 30.0})())
    replies = [(429, {}, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7",
                          "parameters": {"retry_after": 7}}),
               (200, {}, {"ok": True, "result": {"message_id": 9}})]  # fmt: skip
    server.route("POST", "/bot123:abc/sendVideo", lambda _request: replies.pop(0))
    server.route("POST", "/bot123:abc/sendMessage", (200, {}, {"ok": True, "result": {"message_id": 10}}))
    pauses: list[float] = []
    http.sleep = pauses.append
    assert TelegramPublisher(SECRETS, http, api_url=server.url).publish_short(video, META, make_candidate(), []).id == "9"
    assert pauses == [7.0] and len(server.calls("POST", "/bot123:abc/sendVideo")) == 2


def test_a_local_bot_api_server_takes_videos_up_to_2_gb(http):
    assert TelegramPublisher(SECRETS, http).max_upload == telegram_module.MAX_UPLOAD
    local = TelegramPublisher(SECRETS, http, api_url="http://localhost:8081")
    assert local.max_upload == telegram_module.LOCAL_MAX_UPLOAD and local.base.startswith("http://localhost:8081/bot")


def test_telegram_flags_youtube_extracts_for_tiktok(server, http, video, monkeypatch):
    monkeypatch.setattr(telegram_module, "probe", lambda _p: type("I", (), {"width": 1080, "height": 1920, "duration": 30.0})())
    server.route("POST", "/bot123:abc/sendVideo", (200, {}, {"ok": True, "result": {"message_id": 78}}))
    server.route("POST", "/bot123:abc/sendMessage", (200, {}, {"ok": True, "result": {"message_id": 79}}))
    extract = make_candidate("youtube:abc@10-40", "squeezie", source="youtube")
    TelegramPublisher(SECRETS, http, api_url=server.url).publish_short(video, META, extract, [])
    [call] = server.calls("POST", "/bot123:abc/sendVideo")
    assert "Extrait d'une vidéo YouTube : poste-le plutôt sur TikTok".encode() in call.body


def test_telegram_errors_are_reported(server, http, video, monkeypatch):
    monkeypatch.setattr(telegram_module, "probe", lambda _p: type("I", (), {"width": 1080, "height": 1920, "duration": 30.0})())
    server.route("POST", "/bot123:abc/sendVideo", (200, {}, {"ok": False, "description": "Bad Request: chat not found"}))
    with pytest.raises(PublishError, match="chat not found"):
        TelegramPublisher(SECRETS, http, api_url=server.url).publish_short(video, META, make_candidate(), [])



def test_telegram_announces_the_best_of_with_the_texts_to_paste(server, http, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "moi/clips")
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    server.route("POST", "/bot123:abc/sendPhoto", (200, {}, {"ok": True, "result": {"message_id": 5}}))
    server.route("POST", "/bot123:abc/sendMessage", (200, {}, {"ok": True, "result": {"message_id": 6}}))
    thumbnail = tmp_path / "comp.jpg"
    thumbnail.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    meta = CompilationMeta(title="TOP 12 de la semaine", description="d", hashtags=["bestof"], thumbnail_text="TOP 12")
    publisher = TelegramPublisher(SECRETS, http, api_url=server.url)

    assert publisher.publish_compilation(tmp_path / "comp.mp4", meta, "0:00 Intro\n1:02 #12 Alpha", thumbnail, []).id == "5"
    [photo] = server.calls("POST", "/bot123:abc/sendPhoto")
    assert "TOP 12 de la semaine".encode() in photo.body and "son lien de téléchargement arrive".encode() in photo.body
    [texts] = server.calls("POST", "/bot123:abc/sendMessage")
    assert texts.form()["text"] == "À coller sur YouTube\n\nTitre :\nTOP 12 de la semaine\n\nDescription :\n0:00 Intro\n1:02 #12 Alpha"

    on_youtube = [PublishResult("youtube", True, "c", "https://www.youtube.com/watch?v=c")]
    publisher.publish_compilation(tmp_path / "comp.mp4", meta, "0:00 Intro", thumbnail, on_youtube)
    assert b"youtube : https://www.youtube.com/watch?v=c" in server.calls("POST", "/bot123:abc/sendPhoto")[1].body
    assert len(server.calls("POST", "/bot123:abc/sendMessage")) == 1  # already online: nothing to paste

def test_telegram_chat_discovery(server, http):
    server.route("GET", "/bot123:abc/getUpdates", (200, {}, {"ok": True, "result": [{"message": {"chat": {"id": 42, "first_name": "Moi"}}}]}))
    assert TelegramPublisher(SECRETS, http, api_url=server.url).chats() == [("42", "Moi")]


def test_build_publishers_auto_and_required():
    config = build(Config, {})
    assert set(build_publishers(config, SECRETS, None)) == {"youtube", "tiktok", "telegram"}
    assert build_publishers(config, Secrets(), None) == {}
    with pytest.raises(PublishError, match="YouTube est activé"):
        build_publishers(build(Config, {"publish": {"youtube": {"enabled": True}}}), Secrets(), None)
    assert "tiktok" not in build_publishers(build(Config, {"publish": {"tiktok": {"enabled": False}}}), SECRETS, None)


def test_update_github_secret_seals_the_value(server, http):
    from nacl import encoding, public

    private = public.PrivateKey.generate()
    server.route("GET", "/repos/me/repo/actions/secrets/public-key",
                 (200, {}, {"key_id": "k1", "key": private.public_key.encode(encoding.Base64Encoder()).decode()}))
    server.route("PUT", "/repos/me/repo/actions/secrets/TIKTOK_REFRESH_TOKEN", (204, {}, b""))
    update_secret(http, "ghp_x", "me/repo", "TIKTOK_REFRESH_TOKEN", "new-token", api_url=server.url)
    [put] = server.calls("PUT", "/repos/me/repo/actions/secrets/TIKTOK_REFRESH_TOKEN")
    payload = put.json()
    assert payload["key_id"] == "k1" and put.headers["authorization"] == "Bearer ghp_x"
    assert public.SealedBox(private).decrypt(base64.b64decode(payload["encrypted_value"])) == b"new-token"


def test_youtube_upload_completed_while_the_response_was_lost(server, http, video, monkeypatch):
    monkeypatch.setattr(youtube_module, "CHUNK", 2 * 1024 * 1024)  # whole file in one chunk
    server.route("POST", "/token", (200, {}, {"access_token": "ya29"}))
    server.route("POST", "/upload/youtube/v3/videos", (200, {"Location": f"{server.url}/session/3"}, {}))
    calls = {"n": 0}

    def session(request):
        calls["n"] += 1
        if request.headers["content-range"].startswith("bytes */"):
            return 200, {}, {"id": "done1", "status": {"privacyStatus": "public"}}
        return 502, {}, "bad gateway"  # the bytes arrived, the answer did not

    server.route("PUT", "/session/3", session)
    result = youtube(server, http).publish_short(video, META, make_candidate())
    assert result.id == "done1" and calls["n"] == 2
