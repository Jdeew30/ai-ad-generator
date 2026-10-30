from datetime import timedelta
from pathlib import Path

import pytest

from clipper.config import Config, Secrets, YouTubeSourceConfig, build
from clipper.sources import discover
from clipper.sources.manual import QueueError, load_queue, manual_candidate, parse_timestamp
from clipper.sources.twitch import TwitchApi, clip_slug, discover_twitch
from clipper.sources.youtube import candidates_from_info, channel_url, most_replayed
from clipper.ytdlp import DownloadError

from .conftest import NOW


def helix_clip(slug: str, views: int, *, offset=120, video="v1", login_id="1", name="Squeezie", game="g1"):
    return {
        "id": slug, "url": f"https://clips.twitch.tv/{slug}", "broadcaster_id": login_id, "broadcaster_name": name,
        "creator_name": "viewer42", "video_id": video, "game_id": game, "language": "fr", "title": f"clip {slug}",
        "view_count": views, "created_at": "2026-10-27T10:00:00Z", "thumbnail_url": "https://t/x.jpg",
        "duration": 29.9, "vod_offset": offset,
    }  # fmt: skip


class FakeYtDlp:
    def __init__(self, listings=None, infos=None):
        self.listings = listings or {}
        self.infos = infos or {}
        self.listed: list[str] = []

    def list_flat(self, url, limit):
        self.listed.append(url)
        for prefix, entries in self.listings.items():
            if url.startswith(prefix):
                if isinstance(entries, Exception):
                    raise entries
                return entries[:limit]
        raise DownloadError("not found", permanent=True)

    def info(self, url):
        return self.infos[url]


def test_helix_discovery(server, http):
    server.route("POST", "/oauth2/token", (200, {}, {"access_token": "tok", "expires_in": 5000}))
    server.route("GET", "/helix/streams", (200, {}, {"data": [{"user_login": "Gotaga"}, {"user_login": "squeezie"}, {"user_login": "blocked"}]}))
    server.route("GET", "/helix/users", (200, {}, {"data": [
        {"id": "1", "login": "squeezie", "display_name": "Squeezie"},
        {"id": "2", "login": "gotaga", "display_name": "Gotaga"},
    ]}))  # fmt: skip

    def clips(request):
        if request.query["broadcaster_id"] == ["1"]:
            return 200, {}, {"data": [helix_clip("SlugA", 5000), helix_clip("SlugB", 300, offset=None, video="")]}
        return 200, {}, {"data": [helix_clip("SlugC", 900, login_id="2", name="Gotaga", game="g2")]}

    server.route("GET", "/helix/clips", clips)
    server.route("GET", "/helix/games", (200, {}, {"data": [{"id": "g1", "name": "Fortnite"}, {"id": "g2", "name": "Valorant"}]}))
    config = build(Config, {"twitch": {"streamers": ["squeezie", "nobody"]}, "blocklist": ["blocked"]})
    api = TwitchApi("cid", "secret", http, base_url=f"{server.url}/helix", token_url=f"{server.url}/oauth2/token")
    found = discover_twitch(config, Secrets(), http, FakeYtDlp(), now=NOW, window=timedelta(hours=36), api=api)

    by_key = {c.key: c for c in found}
    assert set(by_key) == {"twitch:SlugA", "twitch:SlugB", "twitch:SlugC"}
    a = by_key["twitch:SlugA"]
    assert (a.streamer, a.streamer_login, a.game, a.views, a.vod_id, a.vod_offset, a.clipper) == (
        "Squeezie", "squeezie", "Fortnite", 5000, "v1", 120.0, "viewer42",
    )  # fmt: skip
    assert by_key["twitch:SlugB"].vod_id is None and by_key["twitch:SlugB"].vod_offset is None
    users = server.calls("GET", "/helix/users")[0]
    assert sorted(users.query["login"]) == ["gotaga", "nobody", "squeezie"]  # live channel added, blocked one not
    clip_call = server.calls("GET", "/helix/clips")[0]
    assert clip_call.headers["client-id"] == "cid" and clip_call.headers["authorization"] == "Bearer tok"
    assert clip_call.query["started_at"] == ["2026-10-26T00:00:00Z"] and clip_call.query["ended_at"] == ["2026-10-27T12:00:00Z"]


def test_helix_discovery_covers_french_twitch_beyond_the_list(server, http):
    server.route("POST", "/oauth2/token", (200, {}, {"access_token": "tok"}))
    server.route("GET", "/helix/streams", (200, {}, {"data": [{"user_login": "LiveFr"}]}))

    def users(request):
        if "id" in request.query:  # channels of the categories' top clips
            return 200, {}, {"data": [{"id": "9", "login": "PetitStreamer"}]}
        return 200, {}, {"data": [{"id": str(i), "login": login} for i, login in enumerate(request.query["login"], start=1)]}

    def clips(request):
        if "game_id" in request.query:
            english = {**helix_clip("Viral-EN", 90000, login_id="8"), "language": "en"}
            return 200, {}, {"data": [helix_clip("Viral-FR", 50000, login_id="9", name="PetitStreamer"), english, helix_clip("Clip1", 500)]}
        return 200, {}, {"data": [helix_clip(f"Clip{request.query['broadcaster_id'][0]}", 500)]}

    server.route("GET", "/helix/users", users)
    server.route("GET", "/helix/clips", clips)
    server.route("GET", "/helix/games/top", (200, {}, {"data": [{"id": "509658", "name": "Just Chatting"}]}))
    server.route("GET", "/helix/games", (200, {}, {"data": [{"id": "g1", "name": "Just Chatting"}]}))
    config = build(Config, {"twitch": {"streamers": ["squeezie"], "remember_live": 10, "discover_categories": 3}})
    memory = {"ancienfr": "2026-10-20T10:00:00+00:00"}  # seen live last week, offline now
    api = TwitchApi("cid", "secret", http, base_url=f"{server.url}/helix", token_url=f"{server.url}/oauth2/token")
    found = discover_twitch(config, Secrets(), http, FakeYtDlp(), now=NOW, window=timedelta(days=7), api=api, memory=memory)

    keys = [c.key for c in found]
    assert sorted(keys) == ["twitch:Clip1", "twitch:Clip2", "twitch:Clip3", "twitch:Viral-FR"]  # no English clip, no duplicate
    assert next(c for c in found if c.key == "twitch:Viral-FR").streamer_login == "petitstreamer"
    assert sorted(server.calls("GET", "/helix/users")[0].query["login"]) == ["ancienfr", "livefr", "squeezie"]
    assert server.calls("GET", "/helix/games/top")[0].query["first"] == ["3"]
    assert memory == {"ancienfr": "2026-10-20T10:00:00+00:00", "livefr": NOW.isoformat(), "petitstreamer": NOW.isoformat()}


def test_helix_skips_the_clips_of_co_streamed_lives(server, http):
    server.route("POST", "/oauth2/token", (200, {}, {"access_token": "tok"}))
    server.route("GET", "/helix/users", (200, {}, {"data": [{"id": "1", "login": "kameto", "display_name": "Kameto"}]}))
    server.route("GET", "/helix/clips", (200, {}, {"data": [
        helix_clip("CoStream", 9000, video="v1"), helix_clip("Fun", 800, video="v2"), helix_clip("Deleted", 700, video="v3"),
        helix_clip("NoVod", 600, video=""),
    ]}))  # fmt: skip
    server.route("GET", "/helix/videos", (200, {}, {"data": [
        {"id": "v1", "title": "WORLDS 2026 : KC vs AL | CO-STREAM !kc"}, {"id": "v2", "title": "On teste le nouveau jeu"},
    ]}))  # fmt: skip
    server.route("GET", "/helix/games", (200, {}, {"data": []}))
    config = build(Config, {"twitch": {"streamers": ["kameto"], "discover_live": False, "skip_live_words": ["worlds", "watch party"]}})
    api = TwitchApi("cid", "secret", http, base_url=f"{server.url}/helix", token_url=f"{server.url}/oauth2/token")
    found = discover_twitch(config, Secrets(), http, FakeYtDlp(), now=NOW, window=timedelta(days=7), api=api)
    assert sorted(c.key for c in found) == ["twitch:Deleted", "twitch:Fun", "twitch:NoVod"]  # an unknown live title keeps its clip
    assert server.calls("GET", "/helix/videos")[0].query["id"] == ["v1", "v2", "v3"]

    server.route("GET", "/helix/videos", (404, {}, {"error": "Not Found"}))  # none of the replays exists any more
    assert api.videos(["v8", "v9"]) == {}


def test_the_other_clips_of_a_moment_are_listed(server, http):
    from clipper.sources.twitch import SourceError, clips_around

    server.route("POST", "/oauth2/token", (200, {}, {"access_token": "tok"}))

    def clips(request):
        if "id" in request.query:
            if request.query["id"] == ["Gone"]:
                return 200, {}, {"data": []}
            return 200, {}, {"data": [{**helix_clip("Short30", 1334, video=""), "created_at": "2026-10-24T19:31:23Z", "duration": 30}]}
        return 200, {}, {"data": [
            {**helix_clip("Long60", 400, video=""), "created_at": "2026-10-24T19:31:50Z", "duration": 60},
            {**helix_clip("Before", 900, video=""), "created_at": "2026-10-24T19:29:02Z", "duration": 45},
        ]}  # fmt: skip

    server.route("GET", "/helix/clips", clips)
    api = TwitchApi("cid", "secret", http, base_url=f"{server.url}/helix", token_url=f"{server.url}/oauth2/token")
    clip, found = clips_around(api, "Short30", 10)
    assert clip["id"] == "Short30" and [c["id"] for c in found] == ["Before", "Short30", "Long60"]  # oldest first, itself included
    window = server.calls("GET", "/helix/clips")[1].query
    assert window["broadcaster_id"] == ["1"] and window["started_at"] == ["2026-10-24T19:21:23Z"] and window["ended_at"] == ["2026-10-24T19:41:23Z"]
    with pytest.raises(SourceError):
        clips_around(api, "Gone", 10)


def test_helix_refreshes_an_expired_app_token(server, http):
    tokens = iter(["old", "new"])
    server.route("POST", "/token", lambda _r: (200, {}, {"access_token": next(tokens)}))
    server.route("GET", "/helix/users", lambda r: (401, {}, {"message": "Invalid OAuth token"}) if r.headers["authorization"] == "Bearer old" else (200, {}, {"data": []}))
    api = TwitchApi("cid", "s", http, base_url=f"{server.url}/helix", token_url=f"{server.url}/token")
    assert api.users(["x"]) == {}
    assert len(server.calls("POST", "/token")) == 2


def test_listing_fallback_without_api_keys():
    entries = [
        {"url": "https://clips.twitch.tv/Fresh-Slug_1", "title": "frais", "view_count": 800, "duration": 20, "timestamp": (NOW - timedelta(hours=2)).timestamp()},
        {"url": "https://clips.twitch.tv/OldSlug", "title": "vieux", "view_count": 9000, "duration": 20, "timestamp": (NOW - timedelta(days=3)).timestamp()},
        {"url": "https://www.twitch.tv/videos/123", "title": "pas un clip"},
    ]
    ytdlp = FakeYtDlp({"https://www.twitch.tv/squeezie/clips": entries, "https://www.twitch.tv/broken": DownloadError("boom")})
    config = build(Config, {"twitch": {"streamers": ["squeezie", "broken"]}})
    found = discover_twitch(config, Secrets(), None, ytdlp, now=NOW, window=timedelta(hours=36))
    assert [c.key for c in found] == ["twitch:Fresh-Slug_1"]
    assert "range=7d" in ytdlp.listed[0]  # 36 h window needs the weekly list, then filters by date


def test_clip_slug_variants():
    assert clip_slug("https://clips.twitch.tv/FaintLightGullWholeWheat") == "FaintLightGullWholeWheat"
    assert clip_slug("https://www.twitch.tv/xqc/clip/Cultured-TiZ_-ixAGYR3y2Uy?filter=clips") == "Cultured-TiZ_-ixAGYR3y2Uy"
    assert clip_slug("https://clips.twitch.tv/embed?parent=a&clip=Inquisitive") == "Inquisitive"
    assert clip_slug("https://www.twitch.tv/videos/123") is None


def test_most_replayed_picks_peaks_away_from_intro():
    values = [1.0] + [0.2] * 40 + [0.5, 0.9, 0.95, 0.7] + [0.2] * 30 + [0.8] + [0.2] * 24
    heatmap = [{"start_time": i * 36.0, "end_time": (i + 1) * 36.0, "value": v} for i, v in enumerate(values)]
    windows = most_replayed(heatmap, 3600, count=2, min_peak=0.55, seg_min=20, seg_max=58)
    assert len(windows) == 2
    (s1, e1, h1), (s2, e2, h2) = windows
    assert h1 == 0.95 and 1476 <= s1 < 1548 and e1 - s1 == pytest.approx(72, abs=0.2)  # hot region kept whole, trimmed later
    assert h2 == 0.8 and e2 - s2 == pytest.approx(36, abs=0.2)
    assert most_replayed(heatmap, 3600, count=3, min_peak=0.99, seg_min=20, seg_max=58) == []


def test_youtube_candidates_from_info():
    heatmap = [{"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": 0.9 if i == 50 else 0.1} for i in range(100)]
    info = {
        "id": "abcdefghijk", "title": "Ma vidéo", "uploader_id": "@Squeezie", "channel": "Squeezie",
        "channel_url": "https://www.youtube.com/@Squeezie", "view_count": 50000, "duration": 1000,
        "timestamp": (NOW - timedelta(days=1)).timestamp(), "heatmap": heatmap,
    }  # fmt: skip
    config = build(Config, {})
    settings = YouTubeSourceConfig(enabled=True)
    [candidate] = candidates_from_info(info, settings, config, NOW)
    assert candidate.key.startswith("youtube:abcdefghijk@")
    assert candidate.streamer_login == "squeezie" and candidate.start < 505 < candidate.end
    assert candidate.vod_span()[0] == "yt:abcdefghijk"
    assert candidates_from_info({**info, "view_count": 10}, settings, config, NOW) == []
    assert candidates_from_info({**info, "timestamp": (NOW - timedelta(days=30)).timestamp()}, settings, config, NOW) == []
    assert channel_url("Squeezie") == "https://www.youtube.com/@Squeezie"
    assert channel_url("UC" + "x" * 22).endswith("/channel/UC" + "x" * 22)


def test_manual_queue(tmp_path: Path):
    assert parse_timestamp("1:23") == 83 and parse_timestamp("1h2m3s") == 3723 and parse_timestamp(7) == 7
    with pytest.raises(QueueError):
        parse_timestamp("demain")
    clip = manual_candidate("https://clips.twitch.tv/SomeSlug")
    assert clip.key == "twitch:SomeSlug" and clip.manual
    segment = manual_candidate("https://youtu.be/abcdefghijk", "1:00", "1:45")
    assert segment.key == "youtube:abcdefghijk@60-105" and segment.duration == 45
    vod = manual_candidate("https://www.twitch.tv/gotaga/v/123?t=1h", "10", "40")
    assert vod.key.startswith("manual:") and vod.streamer_login == "gotaga"
    with pytest.raises(QueueError):
        manual_candidate("https://youtu.be/abcdefghijk", "2:00", "1:00")

    queue = tmp_path / "queue.yaml"
    queue.write_text("items:\n  - https://clips.twitch.tv/A\n  - url: https://youtu.be/abcdefghijk\n    start: 10\n    end: 30\n", encoding="utf-8")
    assert [c.key for c in load_queue(queue)] == ["twitch:A", "youtube:abcdefghijk@10-30"]
    queue.write_text("items:\n  - url: https://clips.twitch.tv/A\n    debut: 3\n", encoding="utf-8")
    with pytest.raises(QueueError, match="debut"):
        load_queue(queue)
    assert load_queue(tmp_path / "missing.yaml") == []


def test_youtube_channels_take_turns():
    from clipper.sources.youtube import discover_youtube

    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a", "@b", "@c"], "channels_per_run": 2, "tabs": ["videos"]}})
    ytdlp = FakeYtDlp()  # every listing fails: only the URLs asked for matter here
    assert discover_youtube(config, ytdlp, now=NOW, offset=2) == []
    assert ytdlp.listed == ["https://www.youtube.com/@c/videos", "https://www.youtube.com/@a/videos"]


def test_youtube_finds_a_youtubers_other_channels():
    from clipper.sources.youtube import related_channels

    found = [
        {"channel": "Squeezie", "uploader_id": "@Squeezie", "channel_follower_count": 19_000_000},
        {"channel": "Squeezie Gaming", "uploader_id": "@SqueezieGaming", "channel_follower_count": 3_000_000},
        {"channel": "Squeezie - Topic", "url": "https://www.youtube.com/channel/UC3"},
        {"channel": "Squeezie Clips", "uploader_id": "@SqueezieClips", "channel_follower_count": 50_000},  # a fan's
        {"channel": "Squeezie Vlog", "uploader_id": "@SqueezieVlogFan", "channel_follower_count": 800},  # too small
        {"channel": "Squeezie 2", "url": "https://www.youtube.com/channel/UC6", "channel_follower_count": 120_000},
    ]
    ytdlp = FakeYtDlp({"https://www.youtube.com/results?search_query=Squeezie&sp=": found})
    assert related_channels(ytdlp, "@Squeezie") == ["@SqueezieGaming", "https://www.youtube.com/channel/UC6"]


def test_other_channels_join_the_turns():
    from clipper.sources.youtube import discover_youtube

    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a", "@b"], "tabs": ["videos"], "related_channels": True}})
    related = {"@b": {"at": NOW.isoformat(), "channels": ["@bGaming"]}}  # looked up recently: kept as is
    search_a = "https://www.youtube.com/results?search_query=a&sp="
    ytdlp = FakeYtDlp({search_a: [{"channel": "a Gaming", "uploader_id": "@aGaming", "channel_follower_count": 50_000}]})
    assert discover_youtube(config, ytdlp, now=NOW, related=related) == []
    assert related["@a"] == {"at": NOW.isoformat(), "channels": ["@aGaming"]} and related["@b"]["channels"] == ["@bGaming"]
    assert [url.split("youtube.com/")[1] for url in ytdlp.listed] == [
        "results?search_query=a&sp=EgIQAg%253D%253D", "@a/videos", "@b/videos", "@aGaming/videos", "@bGaming/videos",
    ]  # fmt: skip


def test_a_focus_run_only_looks_at_the_sources_asked_for():
    from clipper.config import ConfigError
    from clipper.sources import focus_config
    from clipper.sources.youtube import discover_youtube

    config = build(Config, {
        "twitch": {"streamers": ["squeezie"], "remember_live": 150, "discover_categories": 20},
        "youtube": {"enabled": True, "channels": ["@Squeezie"], "channels_per_run": 8, "related_channels": True, "min_views": 50000},
    })  # fmt: skip
    video = "https://www.youtube.com/watch?v=L38q9KHfQgY"
    focused = focus_config(config, ["Gotaga", "https://www.twitch.tv/aminematue/", "@Gotaga", video])
    assert focused.twitch.streamers == ["gotaga", "aminematue"] and focused.twitch.clips_per_streamer == 50
    assert not focused.twitch.discover_live and focused.twitch.remember_live == focused.twitch.discover_categories == 0
    assert focused.youtube.channels == ["@Gotaga"] and focused.youtube.videos == [video]
    assert focused.youtube.channels_per_run == 0 and not focused.youtube.related_channels
    assert focused.shorts.max_per_streamer_per_day > 1000 and config.twitch.streamers == ["squeezie"]  # the config is untouched
    assert not focus_config(config, ["gotaga"]).youtube.enabled and not focus_config(config, [video]).twitch.enabled
    with pytest.raises(ConfigError, match="clips.twitch.tv"):
        focus_config(config, ["https://clips.twitch.tv/Foo"])

    # A video asked for by name gives its most replayed moments, whatever its age or views.
    heatmap = [{"start_time": i * 60.0, "end_time": (i + 1) * 60.0, "value": 0.9 if i in (20, 90, 150) else 0.1} for i in range(200)]
    info = {"id": "L38q9KHfQgY", "title": "UN MASTERKILL COMPLÈTEMENT FOU !", "uploader_id": "@Gotaga", "view_count": 900,
            "duration": 12000, "timestamp": (NOW - timedelta(days=40)).timestamp(), "heatmap": heatmap}  # fmt: skip
    ytdlp = FakeYtDlp(infos={video: info})
    found = discover_youtube(focus_config(config, [video]), ytdlp, now=NOW)
    assert [c.start for c in found] == [1197.0, 5397.0, 8997.0] and ytdlp.listed == []  # peaks of a 3 h 20 live, 40 days old


def test_youtube_anti_bot_check_stops_the_youtube_source(monkeypatch):
    import yt_dlp

    from clipper.sources.youtube import discover_youtube
    from clipper.ytdlp import YtDlp

    class Refusing:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            raise yt_dlp.utils.DownloadError("ERROR: [youtube] abcdefghijk: Sign in to confirm you’re not a bot. This helps protect our community.")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Refusing)
    real = YtDlp()
    with pytest.raises(DownloadError) as caught:
        real.info("https://www.youtube.com/watch?v=abcdefghijk")
    assert caught.value.blocked and not caught.value.permanent and real.blocked

    class Blocked(FakeYtDlp):
        def info(self, url):
            self.asked.append(url)
            raise DownloadError("Sign in to confirm you're not a bot", blocked=True)

    entries = [{"id": "vid00000001"}, {"id": "vid00000002"}]
    ytdlp = Blocked({"https://www.youtube.com/@a/videos": entries, "https://www.youtube.com/@b/videos": entries})
    ytdlp.asked = []
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a", "@b"], "tabs": ["videos"]}})
    assert discover_youtube(config, ytdlp, now=NOW) == []
    assert len(ytdlp.asked) == 1 and ytdlp.listed == ["https://www.youtube.com/@a/videos"]  # nothing more asked of YouTube


def test_discover_puts_the_queue_first(tmp_path: Path):
    queue = tmp_path / "queue.yaml"
    queue.write_text("items: [https://clips.twitch.tv/Manual]\n", encoding="utf-8")
    entries = [{"url": "https://clips.twitch.tv/Auto", "view_count": 500, "duration": 20, "timestamp": NOW.timestamp()}]
    config = build(Config, {"twitch": {"streamers": ["s"]}})
    found = discover(config, Secrets(), None, FakeYtDlp({"https://www.twitch.tv/s/clips": entries}), now=NOW,
                     window=timedelta(hours=24), queue=queue)
    assert [c.key for c in found] == ["twitch:Manual", "twitch:Auto"]


def test_an_unreachable_twitch_is_an_error_not_an_empty_day():
    from clipper.sources.twitch import SourceError

    ytdlp = FakeYtDlp({"https://www.twitch.tv/": DownloadError("Tunnel connection failed: 403")})
    config = build(Config, {"twitch": {"streamers": ["a", "b"]}})
    with pytest.raises(SourceError, match="toutes les chaînes"):
        discover_twitch(config, Secrets(), None, ytdlp, now=NOW, window=timedelta(hours=24))


def test_sections_are_copied_unless_exact_cuts_are_asked(tmp_path: Path):
    from clipper.ytdlp import YtDlp

    class Recording(YtDlp):
        def _run(self, url, options, download):
            self.options = options
            (tmp_path / "replay.mp4").write_bytes(b"")
            return {"requested_downloads": [{"filepath": str(tmp_path / "replay.mp4")}]}

    ytdlp = Recording()
    ytdlp.download("https://www.twitch.tv/videos/1", tmp_path, "replay", start=80.0, end=120.0, exact=False)
    assert ytdlp.options["force_keyframes_at_cuts"] is False  # stream copy: seconds instead of a 1080p60 re-encode
    ytdlp.download("https://www.twitch.tv/videos/1", tmp_path, "replay", start=80.0, end=120.0)
    assert ytdlp.options["force_keyframes_at_cuts"] is True
    ytdlp.download("https://clips.twitch.tv/x", tmp_path, "replay")
    assert "download_ranges" not in ytdlp.options and "force_keyframes_at_cuts" not in ytdlp.options
