from datetime import timedelta

import pytest

from clipper.sources.watch import (
    MAX_ERRORS,
    Creators,
    channel_paces,
    due,
    failed,
    mentions,
    parse_feed,
    pre_score,
    prune,
    read_feed,
    remember,
    velocity,
)

from .conftest import NOW

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>Squeezie</title>
 <entry>
  <yt:videoId>abcdefghijk</yt:videoId><yt:channelId>UCsq</yt:channelId>
  <title>JE RÉAGIS à la pire embrouille !</title><published>{recent}</published>
  <media:group><media:community><media:starRating count="5400" average="5.00" min="1" max="5"/>
  <media:statistics views="120000"/></media:community></media:group>
 </entry>
 <entry>
  <yt:videoId>lmnopqrstuv</yt:videoId><yt:channelId>UCsq</yt:channelId>
  <title>Vlog tranquille</title><published>{old}</published>
  <media:group><media:community><media:statistics views="900"/></media:community></media:group>
 </entry>
</feed>
"""


def feed(recent=NOW - timedelta(hours=3), old=NOW - timedelta(days=30)):
    return FEED_XML.format(recent=recent.isoformat(), old=old.isoformat())


def test_an_rss_feed_gives_the_uploads_with_their_views_and_likes():
    first, second = parse_feed(feed())
    assert (first.id, first.channel_id, first.views, first.likes) == ("abcdefghijk", "UCsq", 120000, 5400)
    assert first.title == "JE RÉAGIS à la pire embrouille !" and first.published == NOW - timedelta(hours=3)
    assert first.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert second.likes is None and second.views == 900


def test_feeds_are_read_from_youtube_without_any_key(server, http):
    server.route("GET", "/feeds/videos.xml", (200, {"Content-Type": "application/atom+xml"}, feed()))
    from clipper.sources import watch

    watch_feed = watch.FEED
    try:
        watch.FEED = server.url + "/feeds/videos.xml?channel_id={}"
        videos = read_feed(http, "UCsq")
    finally:
        watch.FEED = watch_feed
    assert [v.id for v in videos] == ["abcdefghijk", "lmnopqrstuv"]
    assert server.calls("GET", "/feeds/videos.xml")[0].query == {"channel_id": ["UCsq"]}


def test_the_creators_database_follows_the_owners_list(tmp_path):
    db = Creators(tmp_path / "creators.json")
    db.sync(["@Squeezie", "@Amixem"], NOW)
    assert sorted(db.creators) == ["amixem", "squeezie"]
    main = db.creators["squeezie"]["channels"]["squeezie"]
    assert main["role"] == "main" and main["verified"] == "liste du propriétaire" and main["status"] == "active"
    assert [channel["ref"] for _, channel in db.watched()] == ["@Squeezie", "@Amixem"]
    # A secondary channel is only added once, whoever it is known under.
    assert db.add_secondary("squeezie", "@SqueezieGaming", verified="mentionne @Squeezie", now=NOW)
    assert not db.add_secondary("amixem", "@squeeziegaming", verified="x", now=NOW)
    db.sync(["@Squeezie"], NOW)  # taken off the list: no longer watched, kept in the base
    assert db.creators["amixem"]["status"] == "removed" and len(db.watched()) == 2
    db.save()
    again = Creators.load(tmp_path / "creators.json")
    assert again.creators == db.creators and again.summary() == {"creators": 1, "channels": 2, "with_id": 0,
                                                                 "secondary": 1, "set_aside": 0}  # fmt: skip


def test_a_channel_set_aside_gets_one_more_try_a_week(tmp_path):
    db = Creators(tmp_path / "creators.json")
    db.sync(["@old", "@recent", "@gone"], NOW)
    old, recent, gone = (db.creators[key]["channels"][key] for key in ("old", "recent", "gone"))
    for _ in range(MAX_ERRORS):
        failed(recent, "flux RSS : HTTP 404", NOW - timedelta(days=3))
    old["status"], old["errors"] = "introuvable", MAX_ERRORS  # set aside before the date was kept: tried at once
    gone["status"] = "introuvable"
    db.sync(["@old", "@recent"], NOW)  # taken off the owner's list: not tried again
    assert recent["status"] == "introuvable" and db.watched() == []
    assert db.revive(NOW) == ["@old"] and (old["status"], old["errors"]) == ("active", MAX_ERRORS - 1)
    failed(old, "toujours introuvable", NOW)  # a single failure sets it aside for another week
    assert old["status"] == "introuvable" and old["set_aside"] == NOW.isoformat()
    assert db.revive(NOW + timedelta(days=4)) == ["@recent"] and recent["status"] == "active"
    assert db.revive(NOW + timedelta(days=6)) == [] and db.revive(NOW + timedelta(days=7)) == ["@old"]


def test_a_secondary_channel_is_verified_by_its_description():
    main = {"ref": "@Squeezie", "id": "UCWeg2Pkate69NFdBeuRFTAw", "url": "https://www.youtube.com/@Squeezie"}
    assert mentions("Chaîne secondaire de @Squeezie ! Tous les lives en replay", main)
    assert mentions("Ma chaîne principale : youtube.com/@squeezie", main)
    assert not mentions("Fan de Squeezie depuis 2012", main)  # the name alone proves nothing
    assert not mentions("Abonnez-vous à @Squeezie2 et @SqueezieFan", main)  # another handle


def test_a_video_that_gathers_views_fast_for_its_channel_scores_higher():
    history: dict = {}
    for index, views in enumerate([30_000, 40_000, 25_000]):  # the channel's usual: about 1 000 views an hour
        entry = remember(history, _video(f"old{index}", views, NOW - timedelta(hours=30)), "squeezie", NOW)
    hit = remember(history, _video("hit", 90_000, NOW - timedelta(hours=3), title="Il CRAQUE en live ! (la pire réaction)",
                                   likes=7_000), "squeezie", NOW)  # fmt: skip
    flat = remember(history, _video("flat", 1_500, NOW - timedelta(hours=3), title="vlog du dimanche", likes=20), "squeezie", NOW)
    paces = channel_paces(history, NOW)
    assert paces == {"UCsq": pytest.approx(1000.0, rel=0.4)}
    assert 0 <= pre_score(flat, paces["UCsq"], NOW) < pre_score(hit, paces["UCsq"], NOW) <= 100
    assert velocity(hit, NOW) == pytest.approx(30_000)
    # Seen again 2 hours later with 20 000 views more: the pace is now measured between the two counts.
    remember(history, _video("hit", 110_000, NOW - timedelta(hours=3)), "squeezie", NOW + timedelta(hours=2))
    assert velocity(history["hit"], NOW + timedelta(hours=2)) == pytest.approx(10_000)
    assert entry["creator"] == "squeezie"


def test_a_video_is_looked_into_once_and_again_a_day_later_if_it_gave_nothing():
    window = timedelta(days=14)
    entry = {"published": (NOW - timedelta(days=2)).isoformat(), "examined": 0}
    assert due(entry, NOW, max_age=window)
    entry.update(examined=1, examined_at=NOW.isoformat(), outcome="moments")
    assert not due(entry, NOW + timedelta(days=1), max_age=window)
    entry.update(outcome="none")
    assert not due(entry, NOW + timedelta(hours=2), max_age=window) and due(entry, NOW + timedelta(hours=21), max_age=window)
    entry.update(examined=2)
    assert not due(entry, NOW + timedelta(days=3), max_age=window)
    assert not due({"published": (NOW - timedelta(days=20)).isoformat()}, NOW, max_age=window)  # past the catch-up


def test_the_history_forgets_videos_past_the_catch_up_window():
    history = {"new": {"published": (NOW - timedelta(days=3)).isoformat()},
               "old": {"published": (NOW - timedelta(days=30)).isoformat()}}  # fmt: skip
    assert list(prune(history, NOW, max_age=timedelta(days=14))) == ["new"]


def _video(video_id, views, published, *, title="titre", likes=None):
    from clipper.sources.watch import FeedVideo

    return FeedVideo(id=video_id, channel_id="UCsq", title=title, published=published, views=views, likes=likes)


def test_shorts_and_videos_with_too_few_views_are_not_looked_into():
    xml = """<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
     <entry><yt:videoId>shortvideo1</yt:videoId><yt:channelId>UCsq</yt:channelId><title>Le saut 😱</title>
      <link rel="alternate" href="https://www.youtube.com/shorts/shortvideo1"/><published>{at}</published></entry>
     <entry><yt:videoId>longvideo01</yt:videoId><yt:channelId>UCsq</yt:channelId><title>La vidéo</title>
      <link rel="alternate" href="https://www.youtube.com/watch?v=longvideo01"/><published>{at}</published>
      <media:group><media:community><media:statistics views="9000"/></media:community></media:group></entry>
    </feed>""".format(at=(NOW - timedelta(hours=2)).isoformat())
    short, long = parse_feed(xml)
    assert short.short and not long.short
    history: dict = {}
    window = timedelta(days=14)
    assert not due(remember(history, short, "sq", NOW), NOW, max_age=window)
    entry = remember(history, long, "sq", NOW)
    assert not due(entry, NOW, max_age=window, min_views=20_000)  # 9 000 views: not yet
    long.views = 25_000
    assert due(remember(history, long, "sq", NOW + timedelta(hours=3)), NOW, max_age=window, min_views=20_000)
    entry.update(examined=1, outcome="skip")  # too short, live, members only: never again
    assert not due(entry, NOW + timedelta(days=2), max_age=window, min_views=20_000)
