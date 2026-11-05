import json
from datetime import timedelta
from pathlib import Path

import pytest

from clipper.config import Config, build
from clipper.models import PublishResult, ShortMeta, utcnow
from clipper.selection import rank, spread
from clipper.state import MAX_DOWNLOAD_FAILURES, State, same_moment, spans_overlap

from .conftest import NOW, make_candidate


def meta() -> ShortMeta:
    return ShortMeta(hook="h", title="t", description="d", hashtags=["a"])


def config(**values) -> Config:
    return build(Config, values)


def test_state_roundtrip_is_atomic(tmp_path: Path):
    path = tmp_path / "state" / "state.json"
    state = State(path)
    candidate = make_candidate(vod_id="v1", vod_offset=100.0)
    state.record_post(candidate, meta(), [PublishResult("youtube", True, "abc", "https://y/abc")])
    state.save()
    assert not list(path.parent.glob(".state-*"))  # no temporary file left behind
    loaded = State.load(path)
    assert loaded.seen(candidate.key)
    assert loaded.posted[candidate.key]["results"]["youtube"]["id"] == "abc"
    assert loaded.posted[candidate.key]["span"] == ["tw:v1", 100.0, 130.0]


def test_unknown_state_version_is_refused(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(ValueError):
        State.load(path)


def test_download_failures_give_up_after_three(tmp_path: Path):
    state = State(tmp_path / "s.json")
    candidate = make_candidate()
    for _ in range(MAX_DOWNLOAD_FAILURES - 1):
        state.record_failure(candidate, "timeout")
    assert not state.seen(candidate.key)
    state.record_failure(candidate, "timeout")
    assert state.seen(candidate.key)


def test_same_moment_rules():
    assert spans_overlap(("tw:1", 100, 130), ("tw:1", 110, 140))
    assert not spans_overlap(("tw:1", 100, 130), ("tw:2", 100, 130))
    assert not spans_overlap(("tw:1", 100, 130), ("tw:1", 129, 160))  # 1 s of 30: different moments
    a = ("streamer", None, NOW)
    assert same_moment(a, ("STREAMER", None, NOW + timedelta(seconds=60)))
    assert not same_moment(a, ("streamer", None, NOW + timedelta(seconds=300)))
    assert not same_moment(a, ("other", None, NOW))
    # VOD positions win over creation times when both clips have one in the same VOD.
    assert not same_moment(("s", ("tw:1", 0, 30), NOW), ("s", ("tw:1", 500, 530), NOW))


def test_prune_drops_old_entries(tmp_path: Path):
    state = State(tmp_path / "s.json")
    state.rejected["old"] = {"at": (NOW - timedelta(days=40)).isoformat(), "reason": "x"}
    state.rejected["new"] = {"at": (NOW - timedelta(days=1)).isoformat(), "reason": "x"}
    state.prune(NOW)
    assert list(state.rejected) == ["new"]


def test_channels_seen_are_remembered_14_days(tmp_path: Path):
    state = State(tmp_path / "s.json")
    state.streamers_seen = {"recent": (NOW - timedelta(days=2)).isoformat(), "old": (NOW - timedelta(days=15)).isoformat()}
    state.prune(NOW)
    assert list(state.streamers_seen) == ["recent"]
    state.streamers_seen["recent"] = utcnow().isoformat()
    state.save()
    assert list(State.load(tmp_path / "s.json").streamers_seen) == ["recent"]
    state.youtube_related = {"@new": {"at": (NOW - timedelta(days=3)).isoformat()}, "@old": {"at": (NOW - timedelta(days=31)).isoformat()}}
    state.prune(NOW)
    assert list(state.youtube_related) == ["@new"]


def test_rank_filters_dedupes_and_orders(tmp_path: Path):
    state = State(tmp_path / "s.json")
    cfg = config()
    cands = [
        make_candidate("twitch:big", "a", views=5000, created_at=NOW - timedelta(hours=1)),
        make_candidate("twitch:same-moment", "a", views=4000, created_at=NOW - timedelta(hours=1, seconds=-40)),
        make_candidate("twitch:small", "b", views=100),
        make_candidate("twitch:short", "b", views=5000, duration=4),
        make_candidate("twitch:long", "b", views=5000, duration=200),
        make_candidate("twitch:ok", "b", views=900, created_at=NOW - timedelta(hours=3)),
        make_candidate("twitch:blocked", "c", views=99999),
    ]
    cfg.blocklist = ["c"]
    ranked = [c.key for c in rank(cands, state, cfg, now=NOW)]
    assert ranked == ["twitch:big", "twitch:ok"]


def test_watch_party_and_ad_titles_are_skipped(tmp_path: Path):
    cfg = config(twitch={"skip_title_words": ["worlds", "publicité"]})
    cands = [
        make_candidate("twitch:worlds", "a", views=9000, title="🔥WORLDS JOUR 3, MATCH CAPITAL🔥"),
        make_candidate("twitch:ad", "b", views=8000, title="Ouverture de packs - Publicite"),
        make_candidate("twitch:fun", "c", views=900, title="il rate tout"),
        make_candidate("manual:mine", "d", views=0, title="worlds", manual=True),
    ]
    ranked = [c.key for c in rank(cands, State(tmp_path / "s.json"), cfg, now=NOW)]
    assert ranked == ["manual:mine", "twitch:fun"]  # accents and case ignored; URLs added by hand always kept


def test_rank_relative_popularity_favours_a_standout_clip(tmp_path: Path):
    state = State(tmp_path / "s.json")
    # Streamer "big" always gets ~20k views; streamer "small" usually 300, but this clip got 3000.
    cands = [make_candidate(f"twitch:big{i}", "big", views=20000 + i, created_at=NOW - timedelta(hours=i + 1)) for i in range(5)]
    cands += [make_candidate(f"twitch:small{i}", "small", views=300, created_at=NOW - timedelta(hours=i + 3)) for i in range(4)]
    cands.append(make_candidate("twitch:standout", "small", views=3000, created_at=NOW - timedelta(hours=2)))
    ranked = [c.key for c in rank(cands, state, config(shorts={"max_per_streamer_per_day": 10}), now=NOW)]
    # The clip taking off right now goes first; the standout comes next, before the big streamer's older clips.
    assert ranked[:3] == ["twitch:big0", "twitch:standout", "twitch:big1"]
    assert ranked.index("twitch:standout") < ranked.index("twitch:small0")


def test_a_clip_taking_off_now_goes_before_an_older_one_with_more_views(tmp_path: Path):
    old = make_candidate("twitch:old", "a", views=9000, created_at=NOW - timedelta(hours=60))
    fresh = make_candidate("twitch:fresh", "b", views=1200, created_at=NOW - timedelta(hours=3))
    state = State(tmp_path / "s.json")
    ranked = rank([old, fresh], state, config(), now=NOW)
    assert [c.key for c in ranked] == ["twitch:fresh", "twitch:old"]  # 400 views an hour against 150
    assert round(fresh.pace) == 400  # shown on the Telegram card
    # A best-of looks back over days: there, the views piled up still count first.
    assert [c.key for c in rank([old, fresh], state, config(), now=NOW, compilation=True)] == ["twitch:old", "twitch:fresh"]


def test_rank_respects_history_and_daily_cap(tmp_path: Path):
    state = State(tmp_path / "s.json")
    posted = make_candidate("twitch:posted", "a", vod_id="v", vod_offset=1000.0)
    state.record_post(posted, meta(), [])
    cands = [
        make_candidate("twitch:posted", "a"),
        make_candidate("twitch:replay", "a", vod_id="v", vod_offset=1010.0, created_at=NOW - timedelta(hours=8)),
        make_candidate("twitch:a2", "a", views=3000, created_at=NOW - timedelta(hours=5)),
        make_candidate("twitch:a3", "a", views=2000, created_at=NOW - timedelta(hours=6)),
        make_candidate("twitch:b1", "b", views=800),
    ]
    ranked = [c.key for c in rank(cands, state, config(shorts={"max_per_streamer_per_day": 2}), now=NOW)]
    # "a" already posted once today: one more allowed, after "b" (room for other creators). The replay overlaps the
    # posted VOD span.
    assert ranked == ["twitch:b1", "twitch:a2"]


def test_rank_manual_items_skip_popularity_filters_and_come_first(tmp_path: Path):
    state = State(tmp_path / "s.json")
    manual = make_candidate("manual:x", "", views=0, duration=0, manual=True, source="manual")
    ranked = rank([make_candidate("twitch:hot", "a", views=90000), manual], state, config(), now=NOW)
    assert [c.key for c in ranked] == ["manual:x", "twitch:hot"]


def test_rank_for_compilation_reuses_posted_clips_but_not_compiled_or_rejected(tmp_path: Path):
    state = State(tmp_path / "s.json")
    state.record_post(make_candidate("twitch:posted", "a"), meta(), [])
    state.record_compilation(["twitch:compiled"], "t", [])
    state.record_rejection(make_candidate("twitch:rejected", "c"), "nope")
    cands = [
        make_candidate("twitch:posted", "a", views=5000),
        make_candidate("twitch:compiled", "b", views=5000),
        make_candidate("twitch:rejected", "c", views=5000),
    ]
    assert [c.key for c in rank(cands, state, config(), now=NOW, compilation=True)] == ["twitch:posted"]


def test_spread_alternates_creators():
    ranked = [make_candidate("twitch:a1", "alpha"), make_candidate("twitch:a2", "Alpha"), make_candidate("twitch:b1", "beta"),
              make_candidate("twitch:a3", "alpha"), make_candidate("manual:x", "", manual=True), make_candidate("twitch:c1", "gamma")]  # fmt: skip
    assert [c.key for c in spread(ranked)] == ["twitch:a1", "twitch:b1", "manual:x", "twitch:c1", "twitch:a2", "twitch:a3"]


def test_a_creator_posted_a_lot_this_week_leaves_room_for_others(tmp_path):
    from clipper.config import Config, build
    from clipper.models import ShortMeta
    from clipper.selection import rank
    from clipper.state import State

    from .conftest import NOW, make_candidate

    state = State(tmp_path / "state.json")
    meta = ShortMeta(hook="h", title="t", description="d", hashtags=[])
    for index in range(3):  # three shorts of « star » this week
        state.record_post(make_candidate(f"twitch:old{index}", "star", created_at=NOW - timedelta(days=index + 2)), meta, [])
        state.posted[f"twitch:old{index}"]["at"] = (NOW - timedelta(days=index + 1)).isoformat()
    config = build(Config, {"shorts": {"max_per_streamer_per_day": 10}})
    star = make_candidate("twitch:star", "star", views=1500, created_at=NOW - timedelta(hours=3))
    newcomer = make_candidate("twitch:new", "newcomer", views=1000, created_at=NOW - timedelta(hours=3))
    assert [c.key for c in rank([star, newcomer], state, config, now=NOW)] == ["twitch:new", "twitch:star"]
    assert [c.key for c in rank([star, newcomer], State(tmp_path / "empty.json"), config, now=NOW)] == ["twitch:star", "twitch:new"]


def test_the_state_counts_the_long_shorts_of_the_day(tmp_path: Path):
    state = State(tmp_path / "s.json")
    state.record_post(make_candidate("twitch:long"), meta(), [], length=65.4)
    state.record_post(make_candidate("twitch:short"), meta(), [], length=28.0)
    state.record_post(make_candidate("twitch:unknown"), meta(), [])
    assert state.posted["twitch:long"]["length"] == 65.4 and state.posted["twitch:unknown"]["length"] is None
    assert state.long_count(NOW - timedelta(days=1), 61) == 1
    state.posted["twitch:long"]["at"] = (NOW - timedelta(days=2)).isoformat()
    assert state.long_count(NOW - timedelta(days=1), 61) == 0  # sent another day
