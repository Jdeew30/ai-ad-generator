import logging
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
    def __init__(self, listings=None, infos=None, comments=None):
        self.listings = listings or {}
        self.infos = infos or {}
        self.comments_by_url = comments or {}
        self.listed: list[str] = []
        self.commented: list[str] = []

    def list_flat(self, url, limit):
        self.listed.append(url)
        for prefix, entries in self.listings.items():
            if url.startswith(prefix):
                if isinstance(entries, Exception):
                    raise entries
                return entries[:limit]
        raise DownloadError("not found", permanent=True)

    def listing(self, url, limit):
        for prefix, entries in self.listings.items():
            if isinstance(entries, dict) and url.startswith(prefix):  # a channel's details and its entries
                self.listed.append(url)
                return {**entries, "entries": entries.get("entries", [])[:limit]}
        return {"entries": self.list_flat(url, limit)}

    def info(self, url):
        return self.infos[url]

    def comments(self, url, limit):
        self.commented.append(url)
        return self.comments_by_url.get(url, [])[:limit]


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

    # Another streamer's point of view of the same moment: their clips only.
    server.route("GET", "/helix/users", lambda r: (200, {}, {"data": [{"id": "42", "login": "hctuan"}] if r.query["login"] == ["hctuan"] else []}))
    clip, found = clips_around(api, "Short30", 10, channel="@HCTuan")
    assert [c["id"] for c in found] == ["Before", "Long60"] and server.calls("GET", "/helix/clips")[-1].query["broadcaster_id"] == ["42"]
    with pytest.raises(SourceError, match="introuvable"):
        clips_around(api, "Short30", 10, channel="personne")


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

    from clipper.sources.manual import joined_candidate, links

    assert links(" https://clips.twitch.tv/A,https://clips.twitch.tv/B\n https://clips.twitch.tv/C ") == [
        "https://clips.twitch.tv/A", "https://clips.twitch.tv/B", "https://clips.twitch.tv/C",
    ]  # fmt: skip
    joined = joined_candidate(["https://www.twitch.tv/inoxtag/clip/A", "https://www.twitch.tv/inoxtag/clip/B"])
    assert joined.key.startswith("manual:") and joined.parts[1].endswith("/B") and joined.streamer_login == "inoxtag"
    with pytest.raises(QueueError):
        joined_candidate(["https://clips.twitch.tv/A", "pas un lien"])


def test_youtube_channels_take_turns():
    from clipper.sources.youtube import discover_youtube

    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a", "@b", "@c"], "channels_per_run": 2, "tabs": ["videos"]}})
    ytdlp = FakeYtDlp({f"https://www.youtube.com/@{name}/videos": [] for name in "abc"})  # empty channels: only the URLs matter
    assert discover_youtube(config, ytdlp, now=NOW, offset=2) == []
    assert ytdlp.listed == ["https://www.youtube.com/@c/videos", "https://www.youtube.com/@a/videos"]


def test_the_log_tells_why_youtube_videos_gave_nothing(caplog):
    from clipper.sources.youtube import discover_youtube

    def video(video_id, days=1, heat=0.9, **values):
        heatmap = [{"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": heat if i == 50 else 0.1} for i in range(100)]
        return {"id": video_id, "uploader_id": "@a", "view_count": 90000, "duration": 1000,
                "timestamp": (NOW - timedelta(days=days)).timestamp(), "heatmap": heatmap, **values}  # fmt: skip

    videos = [video("old", days=30), video("nomap", heatmap=None), video("flat", heat=0.2), video("good"),
              video("talked", heatmap=None, title="Le pari")]  # fmt: skip
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a"], "tabs": ["videos"], "videos_per_channel": 5}})
    watch = "https://www.youtube.com/watch?v={}".format
    ytdlp = FakeYtDlp(listings={"https://www.youtube.com/@a/videos": [{"id": v["id"]} for v in videos]},
                      infos={watch(v["id"]): v for v in videos},
                      comments={watch("talked"): [{"text": "8:20 je suis mort 😂", "like_count": 40}]})  # fmt: skip
    with caplog.at_level("INFO", logger="clipper"):
        found = discover_youtube(config, ytdlp, now=NOW)
    assert [c.key.split("@")[0] for c in found] == ["youtube:good", "youtube:talked"]
    assert ytdlp.commented == [watch("nomap"), watch("talked")]  # only where YouTube gave no "most replayed" data
    assert found[1].start == 480.0 and found[1].end == 570.0  # its setup before the moment cited, its punchline after
    assert (found[1].cited, found[1].cited_at) == ("8:20 je suis mort 😂", 500.0)  # what viewers say of it, for the review
    assert found[0].cited is None  # a "most replayed" peak has no comment
    assert "Moments cités en commentaire sous « Le pari » : 8:00-9:30" in caplog.text
    assert ("YouTube : 2 passage(s) retenu(s) ; vidéos écartées : trop ancienne (1), "
            "sans « moments les plus revus » ni minutage en commentaire (1), aucun passage assez revu (1)") in caplog.text


def test_moments_viewers_point_at_in_the_comments():
    from clipper.sources.youtube import moments_from_comments

    comments = [
        {"text": "3:45 mdr", "like_count": 120},
        {"text": "le 3:50 😂😂", "like_count": 10},
        {"text": "1:02:13 incroyable", "like_count": 5},
        {"text": "0:00 intro 1:00 a 2:00 b 3:00 c 4:00 d", "like_count": 50},  # a summary: each time counts for little
        {"text": "18:30 seul, sans like", "like_count": 0},
        {"text": "le match du 12/10, score 2:1, rdv à 20:30 ce soir"},  # no date or score read; 20:30 alone is too weak
        {"text": "première"},
    ]
    windows = moments_from_comments(comments, 4000.0, count=3)
    # 20 s before the time cited and 70 s after (until 70 s after the last one cited with it): setup and punchline.
    assert [(moment.start, moment.end) for moment in windows] == [(205.0, 310.0), (3713.0, 3803.0)]  # 3:45, 3:50 and 4:00
    assert windows[0].weight > windows[1].weight >= 2.0
    assert (windows[0].at, windows[0].comment) == (225.0, "3:45 mdr")  # the comment liked most tells what happens
    assert moments_from_comments(comments, 4000.0, count=1) == windows[:1]
    # A moment cited again 60 s later falls inside the first window: it is not cut a second time.
    again = moments_from_comments([{"text": "10:00 😂", "like_count": 50}, {"text": "11:00 la suite", "like_count": 9}],
                                  4000.0, count=3)  # fmt: skip
    assert [(moment.start, moment.end) for moment in again] == [(580.0, 670.0)]


def test_signed_in_yt_dlp_reads_channels_without_the_sign_in_check(tmp_path):
    from clipper.ytdlp import YtDlp

    assert YtDlp(cookies_file=tmp_path / "cookies.txt")._options()["extractor_args"] == {"youtubetab": {"skip": ["authcheck"]}}
    assert "extractor_args" not in YtDlp()._options()


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
    tabs = {f"https://www.youtube.com/{handle}/videos": [] for handle in ("@a", "@b", "@aGaming", "@bGaming")}
    ytdlp = FakeYtDlp({search_a: [{"channel": "a Gaming", "uploader_id": "@aGaming", "channel_follower_count": 50_000}], **tabs})
    assert discover_youtube(config, ytdlp, now=NOW, related=related) == []
    assert related["@a"] == {"at": NOW.isoformat(), "channels": ["@aGaming"]} and related["@b"]["channels"] == ["@bGaming"]
    assert [url.split("youtube.com/")[1] for url in ytdlp.listed] == [
        "results?search_query=a&sp=EgIQAg%253D%253D", "@a/videos", "@b/videos", "@aGaming/videos", "@bGaming/videos",
    ]  # fmt: skip


def test_a_youtube_handle_that_does_not_exist_is_looked_up_by_its_name(caplog):
    from clipper.sources.youtube import _channel_tab, discover_youtube, renamed_channel

    search = "https://www.youtube.com/results?search_query=Le%20Woop&sp="
    results = [
        {"channel": "Le Woop Gaming", "uploader_id": "@LeWoopGaming", "channel_follower_count": 900_000},  # another one
        {"channel": "Le Woop", "uploader_id": "@LeWoopFan", "channel_follower_count": 300},  # a fan's
        {"channel": "LE WOOP", "uploader_id": "@LeWoopOfficiel", "channel_follower_count": 4_000_000},
    ]
    heatmap = [{"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": 0.9 if i == 50 else 0.1} for i in range(100)]
    video = {"id": "w1", "uploader_id": "@LeWoopOfficiel", "view_count": 90000, "duration": 1000,
             "timestamp": (NOW - timedelta(days=10)).timestamp(), "heatmap": heatmap}  # fmt: skip
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@LeWoop", "@Nobody"], "tabs": ["videos"],
                                        "max_age_days": 14}})  # fmt: skip
    ytdlp = FakeYtDlp(listings={search: results, "https://www.youtube.com/@LeWoopOfficiel/videos": [{"id": "w1"}]},
                      infos={"https://www.youtube.com/watch?v=w1": video})  # fmt: skip
    with caplog.at_level("INFO", logger="clipper"):
        found = discover_youtube(config, ytdlp, now=NOW)
    assert [c.key.split("@")[0] for c in found] == ["youtube:w1"]
    assert "Chaîne YouTube @LeWoop introuvable : lue sous le nom @LeWoopOfficiel" in caplog.text
    assert "Chaîne YouTube introuvable ou indisponible (https://www.youtube.com/@Nobody/videos)" in caplog.text
    # A channel of lives only gives its lives.
    lives = FakeYtDlp(listings={"https://www.youtube.com/@Etoiles/videos": DownloadError("[youtube:tab] @Etoiles: This "
                                "channel does not have a videos tab", permanent=True),
                                "https://www.youtube.com/@Etoiles/streams": [{"id": "l1"}, {"id": "l2"}]})  # fmt: skip
    assert _channel_tab(lives, "@Etoiles", "videos", 1) == ("@Etoiles", [{"id": "l1"}])
    assert not any("results?" in url for url in lives.listed)  # its handle is right: no other channel of that name
    # The channel found under the very handle that failed is no other name for it.
    assert renamed_channel(FakeYtDlp({search: [{"channel": "Le Woop", "uploader_id": "@lewoop"}]}), "@LeWoop") is None
    wankil = [{"channel": "Wankil Studio - Laink et Terracid", "uploader_id": "@wankil", "channel_follower_count": 3_000_000}]
    daniel = [{"channel": "Antoine Daniel - Les VOD", "uploader_id": "@adlesvod", "channel_follower_count": 300_000},
              {"channel": "Antoine Daniel", "uploader_id": "@antoinedanielofficiel"}]  # no count shown: the title wins
    assert renamed_channel(FakeYtDlp({"https://www.youtube.com/results?search_query=Antoine%20Daniel&": daniel}),
                           "@AntoineDaniel") == "@antoinedanielofficiel"  # fmt: skip
    assert renamed_channel(FakeYtDlp({"https://www.youtube.com/results?search_query=Wankil%20Studio&": wankil}),
                           "@WankilStudio") == "@wankil"  # fmt: skip
    duo = [{"channel": "McFly & Carlito", "uploader_id": "@McFlyEtCarlitoOff", "channel_follower_count": 7_000_000}]
    assert renamed_channel(FakeYtDlp({"https://www.youtube.com/results?search_query=mcflyetcarlito&": duo}),
                           "@mcflyetcarlito") == "@McFlyEtCarlitoOff"  # fmt: skip


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


def test_a_yt_dlp_call_is_tried_again_after_a_network_hiccup_only():
    from clipper.ytdlp import YtDlp

    pauses: list[float] = []
    ytdlp = YtDlp(sleep=pauses.append)
    errors = [DownloadError("Unable to download webpage: HTTP Error 503: Service Unavailable"),
              DownloadError("Read timed out")]  # fmt: skip

    def once(url, options, download):
        if errors:
            raise errors.pop(0)
        return {"id": "ok"}

    ytdlp._once = once
    assert ytdlp.info("https://www.youtube.com/watch?v=abc") == {"id": "ok"} and pauses == [5.0, 20.0]

    def gone(url, options, download):
        raise DownloadError("Video unavailable. This video is private", permanent=True)

    ytdlp._once, pauses[:] = gone, []
    with pytest.raises(DownloadError, match="private"):
        ytdlp.info("https://www.youtube.com/watch?v=abc")
    assert pauses == []  # a private video stays private


def test_yt_dlp_retries_each_request_with_growing_pauses():
    from clipper.ytdlp import YtDlp, _backoff

    options = YtDlp()._options()
    assert options["retries"] == 5 and options["fragment_retries"] == 5 and options["skip_unavailable_fragments"]
    assert [_backoff(n=n) for n in range(7)] == [1.0, 2.0, 4.0, 8.0, 16.0, 20.0, 20.0]
    assert options["retry_sleep_functions"]["fragment"] is _backoff


def test_yt_dlp_itself_can_call_the_pause_between_retries(monkeypatch):
    # The retry_sleep_functions are called by yt-dlp's RetryManager, with a keyword argument: a wrong signature
    # crashed a whole run (« _backoff() got an unexpected keyword argument 'n' »).
    import yt_dlp.utils._utils as ytdlp_utils
    from yt_dlp.utils import RetryManager

    from clipper.ytdlp import YtDlp

    pauses: list[float] = []
    monkeypatch.setattr(ytdlp_utils.time, "sleep", pauses.append)
    for kind in ("http", "fragment", "extractor"):
        RetryManager.report_retry(Exception("HTTP Error 503"), 3, 5, sleep_func=YtDlp()._options()["retry_sleep_functions"][kind],
                                  info=lambda *_: None, warn=lambda *_: None)  # fmt: skip
    assert pauses == [4.0, 4.0, 4.0]


def _feed(*videos):
    entries = "".join(
        f"<entry><yt:videoId>{video_id}</yt:videoId><yt:channelId>{channel}</yt:channelId><title>{title}</title>"
        f"<published>{published.isoformat()}</published><media:group><media:community>"
        f"<media:starRating count=\"{likes}\"/><media:statistics views=\"{views}\"/></media:community></media:group></entry>"
        for video_id, channel, title, published, views, likes in videos
    )
    return ('<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
            'xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">' + entries + "</feed>")


def test_youtube_watch_reads_every_feed_and_looks_into_the_most_promising_videos(server, http, tmp_path, monkeypatch):
    from clipper.sources import watch as watch_module
    from clipper.sources.watch import Creators
    from clipper.sources.youtube import discover_youtube

    monkeypatch.setattr(watch_module, "FEED", server.url + "/feeds/videos.xml?channel_id={}")
    feeds = {
        "UCa": _feed(("hitvideo001", "UCa", "Il CRAQUE en live !", NOW - timedelta(hours=5), 400_000, 30_000),
                     ("flatvideo01", "UCa", "vlog du dimanche", NOW - timedelta(hours=6), 30_000, 300)),
        "UCb": _feed(("oldvideo001", "UCb", "souvenirs", NOW - timedelta(days=40), 900_000, 9_000)),
    }  # fmt: skip
    server.route("GET", "/feeds/videos.xml", lambda request: (200, {}, feeds[request.query["channel_id"][0]]))
    heatmap = [{"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": 0.9 if i == 50 else 0.1} for i in range(100)]
    watch_url = "https://www.youtube.com/watch?v={}".format
    infos = {watch_url(video_id): {"id": video_id, "uploader_id": "@a", "channel_id": "UCa", "channel": "Chaîne A",
                                   "view_count": 400_000, "like_count": 30_000, "duration": 1000, "categories": ["Gaming"],
                                   "timestamp": (NOW - timedelta(hours=5)).timestamp(), "heatmap": heatmap}
             for video_id in ("hitvideo001", "flatvideo01")}  # fmt: skip
    pages = {"https://www.youtube.com/@a/videos": {"channel_id": "UCa", "channel": "Chaîne A", "uploader_id": "@a",
                                                    "channel_follower_count": 2_000_000, "description": "", "entries": []},
             "https://www.youtube.com/@b/videos": {"channel_id": "UCb", "channel": "B", "uploader_id": "@b", "entries": []}}
    ytdlp = FakeYtDlp(listings=pages, infos=infos)
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a", "@b"], "watch": "rss", "videos_per_run": 1}})
    creators, history = Creators(tmp_path / "creators.json"), {}

    found = discover_youtube(config, ytdlp, now=NOW, http=http, creators=creators, history=history)
    # Only the most promising new video was looked into; the one past the catch-up window left the history.
    assert [c.key.split("@")[0] for c in found] == ["youtube:hitvideo001"] and set(history) == {"hitvideo001", "flatvideo01"}
    assert found[0].creator == "Chaîne A" and found[0].momentum > 0 and found[0].pace == pytest.approx(80_000)
    assert found[0].likes == 30_000 and history["hitvideo001"]["examined"] == 1 and not history["flatvideo01"]["examined"]
    channel = creators.creators["a"]["channels"]["a"]
    assert (channel["id"], channel["followers"], channel["categories"]) == ("UCa", 2_000_000, {"Gaming": 1})

    # Next run: the ids are known, only the feeds are read, and the other video gets its turn.
    ytdlp.listed.clear()
    later = discover_youtube(config, ytdlp, now=NOW + timedelta(hours=1), http=http, creators=creators, history=history)
    assert [c.key.split("@")[0] for c in later] == ["youtube:flatvideo01"] and ytdlp.listed == []
    assert len(server.calls("GET", "/feeds/videos.xml")) == 4


def test_a_youtube_feed_that_cannot_be_read_does_not_stop_the_others(server, http, tmp_path, monkeypatch):
    from clipper.sources import watch as watch_module
    from clipper.sources.watch import Creators
    from clipper.sources.youtube import discover_youtube

    monkeypatch.setattr(watch_module, "FEED", server.url + "/feeds/videos.xml?channel_id={}")
    server.route("GET", "/feeds/videos.xml", lambda request: (404, {}, "") if request.query["channel_id"][0] == "UCx"
                 else (200, {}, _feed(("goodvideo01", "UCb", "titre", NOW - timedelta(hours=2), 50_000, 100))))  # fmt: skip
    creators = Creators(tmp_path / "creators.json")
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@x", "@b"], "watch": "rss", "videos_per_run": 0}})
    creators.sync(config.youtube.channels, NOW)
    creators.creators["x"]["channels"]["x"]["id"], creators.creators["b"]["channels"]["b"]["id"] = "UCx", "UCb"
    history: dict = {}
    for hours in range(3):  # three runs in a row: the unreadable channel is set aside
        discover_youtube(config, FakeYtDlp(), now=NOW + timedelta(hours=hours), http=http, creators=creators, history=history)
    assert list(history) == ["goodvideo01"] and creators.creators["x"]["channels"]["x"]["status"] == "introuvable"
    assert [channel["ref"] for _, channel in creators.watched()] == ["@b"]


def test_a_youtube_feed_outage_sets_no_channel_aside(server, http, tmp_path, monkeypatch, caplog):
    from clipper.sources import watch as watch_module
    from clipper.sources.watch import Creators
    from clipper.sources.youtube import discover_youtube

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(watch_module, "FEED", server.url + "/feeds/videos.xml?channel_id={}")
    status = dict.fromkeys(("UCa", "UCb", "UCc", "UCd"), 500)

    def feed(request):
        channel_id = request.query["channel_id"][0]
        if status[channel_id] != 200:
            return status[channel_id], {}, ""
        return 200, {}, _feed(("goodvideo01", channel_id, "titre", NOW - timedelta(hours=2), 50_000, 100))

    server.route("GET", "/feeds/videos.xml", feed)
    creators = Creators(tmp_path / "creators.json")
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a", "@b", "@c", "@d"], "watch": "rss",
                                        "videos_per_run": 0}})  # fmt: skip
    creators.sync(config.youtube.channels, NOW)
    channels = {key: creators.creators[key]["channels"][key] for key in "abcd"}
    for key, channel in channels.items():
        channel["id"] = f"UC{key}"

    def runs(hours):
        for hour in hours:
            discover_youtube(config, FakeYtDlp(), now=NOW + timedelta(hours=hour), http=http, creators=creators, history={})

    runs(range(3))  # every feed down three runs in a row: YouTube's doing, no channel is set aside
    assert all(channel["status"] == "active" and channel["errors"] == 0 for channel in channels.values())
    assert channels["a"]["error"].startswith("flux RSS : GET")
    assert "flux RSS en panne (4 illisible(s), 0 lu(s) ; HTTP 500 × 4)" in caplog.text
    assert len(server.calls("GET", "/feeds/videos.xml")) == 4 * 2 * 3  # one retry per feed: an outage costs seconds
    # Back up, but two feeds fail alone: only the one that does not exist (HTTP 404) counts against its channel.
    status.update(UCa=200, UCb=500, UCc=404, UCd=200)
    runs(range(3, 6))
    assert (channels["a"]["errors"], channels["b"]["status"], channels["b"]["errors"]) == (0, "active", 0)
    assert channels["c"]["status"] == "introuvable" and channels["c"]["set_aside"] == (NOW + timedelta(hours=5)).isoformat()
    assert "2 illisible(s) : @b, @c ; HTTP 500 × 1, HTTP 404 × 1)" in caplog.text


def test_new_creators_come_from_streamers_seen_live_once_their_channel_links_to_their_twitch(tmp_path):
    from clipper.sources.watch import Creators
    from clipper.sources.youtube import _discover_from_twitch

    search = "https://www.youtube.com/results?search_query={}&sp=".format
    ytdlp = FakeYtDlp(listings={
        search("locklear"): [{"channel": "Locklear", "uploader_id": "@Locklear", "channel_follower_count": 900_000}],
        "https://www.youtube.com/@Locklear/videos": {"channel_id": "UCll", "channel": "Locklear", "uploader_id": "@Locklear",
                                                      "description": "Lives tous les soirs sur https://www.twitch.tv/locklear"},
        search("moman"): [{"channel": "Moman", "uploader_id": "@MomanFan", "channel_follower_count": 50_000}],
        "https://www.youtube.com/@MomanFan/videos": {"channel_id": "UCmf", "channel": "Moman", "description": "Best of"},
    })  # fmt: skip
    seen = {"locklear": (NOW - timedelta(hours=3)).isoformat(), "moman": (NOW - timedelta(hours=5)).isoformat(),
            "oldtimer": (NOW - timedelta(days=9)).isoformat()}  # fmt: skip
    creators = Creators(tmp_path / "creators.json")
    _discover_from_twitch(creators, seen, ytdlp, NOW, budget=3)
    assert list(creators.creators) == ["locklear"]  # the namesake without a link to twitch.tv/moman stays out
    channel = creators.creators["locklear"]["channels"]["locklear"]
    assert (channel["id"], channel["verified"]) == ("UCll", "sa description renvoie vers twitch.tv/locklear")
    assert creators.creators["locklear"]["source"] == "twitch" and set(creators.lookups) == {"locklear", "moman"}
    ytdlp.listed.clear()
    _discover_from_twitch(creators, seen, ytdlp, NOW + timedelta(hours=2), budget=3)
    assert ytdlp.listed == []  # each streamer is looked up once a month at most
    # The owner's Twitch streamers are looked up first, then the latest seen.
    order: list[str] = []
    picky = FakeYtDlp()
    picky.list_flat = lambda url, limit: order.append(url.split("search_query=")[1].split("&")[0]) or []
    seen = {"zzz": (NOW - timedelta(hours=1)).isoformat(), "aaa": (NOW - timedelta(hours=5)).isoformat(),
            "star": (NOW - timedelta(hours=9)).isoformat()}  # fmt: skip
    _discover_from_twitch(Creators(tmp_path / "other.json"), seen, picky, NOW, budget=3, first=["star"])
    assert order == ["star", "zzz", "aaa"]


def test_the_watch_looks_past_the_passages_already_sent_or_refused(server, http, tmp_path, monkeypatch):
    from clipper.sources import watch as watch_module
    from clipper.sources.watch import Creators
    from clipper.sources.youtube import discover_youtube

    monkeypatch.setattr(watch_module, "FEED", server.url + "/feeds/videos.xml?channel_id={}")
    server.route("GET", "/feeds/videos.xml", (200, {}, _feed(("peaksvideo1", "UCa", "titre", NOW - timedelta(hours=5), 90_000, 900))))
    peaks = {20: 0.8, 50: 0.95, 80: 0.85}
    heatmap = [{"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": peaks.get(i, 0.1)} for i in range(100)]
    info = {"id": "peaksvideo1", "uploader_id": "@a", "channel_id": "UCa", "view_count": 90_000, "duration": 1000,
            "timestamp": (NOW - timedelta(hours=5)).timestamp(), "heatmap": heatmap}  # fmt: skip
    pages = {"https://www.youtube.com/@a/videos": {"channel_id": "UCa", "channel": "A", "uploader_id": "@a", "entries": []}}
    config = build(Config, {"youtube": {"enabled": True, "channels": ["@a"], "watch": "rss", "segments_per_video": 1}})

    def run(known):
        ytdlp = FakeYtDlp(listings=pages, infos={"https://www.youtube.com/watch?v=peaksvideo1": info})
        return discover_youtube(config, ytdlp, now=NOW, http=http, creators=Creators(tmp_path / "c.json"), history={},
                                known=known)  # fmt: skip

    [best] = run(set())
    [next_best] = run({best.key})  # the hottest passage was already sent: the next one comes instead
    assert best.start < 500 < best.end and next_best.start < 800 < next_best.end
