import json
from datetime import timedelta
from pathlib import Path

import anthropic
import pytest

from clipper.ai import ClaudeCurator, Review, ReviewItem, clip_title, default_trim, hashtag, template_compilation_meta, template_review
from clipper.config import Config, build
from clipper.models import Transcript, Word

from .conftest import NOW, make_candidate


def message(payload: dict | None = None, *, stop_reason: str = "end_turn", text: str | None = None) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text if text is not None else json.dumps(payload)}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def curator(server, config: Config | None = None) -> ClaudeCurator:
    client = anthropic.Anthropic(api_key="test-key", base_url=server.url, max_retries=0)
    return ClaudeCurator("test-key", config or build(Config, {}), client=client)


def items(tmp_path: Path) -> list[ReviewItem]:
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    transcript = Transcript([Word(0.5, 1.0, "Non"), Word(1.0, 1.4, "mais"), Word(1.4, 2.0, "sérieux !")], "fr")
    return [
        ReviewItem(make_candidate("twitch:A", "squeezie", game="Fortnite"), 30.0, transcript, sheet),
        ReviewItem(make_candidate("twitch:B", "gotaga"), 25.0, None, None, clip_span=(5.0, 20.0)),
    ]


def test_review_sends_one_structured_request_and_validates_the_answer(server, tmp_path):
    answer = {
        "clips": [
            {
                "id": "c1", "verdict": "publish", "reason": "fou rire", "score": 140,
                "hook": "IL NE S'Y ATTENDAIT PAS 😂😂 ET C'EST TROP DRÔLE, VRAIMENT TROP TROP",
                "title": "Squeezie <perd> son calme", "description": "Il craque.",
                "hashtags": ["#Squeezie", "Fortnite", "fou rire", "squeezie"],
                "trim_start": 2.0, "trim_end": 999,
            },
            {
                "id": "c2", "verdict": "reject", "reason": "insulte un viewer", "score": 10, "hook": "x", "title": "y",
                "description": "z", "hashtags": [], "trim_start": 0, "trim_end": 25,
            },
        ]
    }  # fmt: skip
    server.route("POST", "/v1/messages", (200, {}, message(answer)))
    reviews = curator(server).review(items(tmp_path), now=NOW)

    a, b = reviews["twitch:A"], reviews["twitch:B"]
    assert a.verdict == "publish" and a.score == 100 and a.meta.ai
    assert "😂" not in a.meta.hook and len(a.meta.hook) <= 48
    assert a.meta.hashtags[:3] == ["squeezie", "fortnite", "fourire"]
    assert (a.meta.trim_start, a.meta.trim_end) == (2.0, 30.0)  # clamped to the extract
    assert b.verdict == "reject" and b.reason == "insulte un viewer"

    [request] = server.calls("POST", "/v1/messages")
    body = request.json()
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in body and "thinking" not in body
    content = body["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 1 and images[0]["source"]["media_type"] == "image/jpeg"  # B has no contact sheet
    texts = "\n".join(block["text"] for block in content if block["type"] == "text")
    assert 'id="c1"' in texts and "Non mais sérieux !" in texts and "Fortnite" in texts
    assert texts.count("Clip d'origine") == 1 and "de 5.0 s à 20.0 s de l'extrait" in texts  # only B comes with its replay


def test_claude_is_told_what_was_published_lately(server, tmp_path):
    server.route("POST", "/v1/messages", (200, {}, message({"clips": []})))
    recent = [(NOW - timedelta(hours=3), "Michou défend le skin d'Inoxtag"), (NOW - timedelta(days=2), "Mastu et sa mort la plus bête")]
    curator(server).review(items(tmp_path), now=NOW, recent=recent)
    [request] = server.calls("POST", "/v1/messages")
    body = request.json()
    assert body["messages"][0]["content"][0]["text"] == (
        "Shorts déjà publiés, du plus récent au plus ancien :\n"
        "- Michou défend le skin d'Inoxtag (publié il y a 3 h)\n- Mastu et sa mort la plus bête (publié il y a 2 j)"
    )
    system = json.dumps(body["system"], ensure_ascii=False)
    assert "moment déjà publié" in system and "(partie 2)" in system
    assert "co-streams de compétition" in system and "scènes de RP" in system  # the owner wants both
    assert "La rage et les gros mots font partie du show" in system  # only hate and harassment are refused


def test_refusal_and_errors_fall_back_to_the_template(server, tmp_path):
    server.route("POST", "/v1/messages", (200, {}, message({"clips": []}, stop_reason="refusal")))
    reviews = curator(server).review(items(tmp_path), now=NOW)
    assert all(not r.meta.ai and r.verdict == "publish" for r in reviews.values())

    server.route("POST", "/v1/messages", (200, {}, message(text="pas du json")))
    assert not curator(server).review(items(tmp_path), now=NOW)["twitch:A"].meta.ai

    server.route("POST", "/v1/messages", (401, {}, {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}))
    failing = curator(server)
    assert not failing.review(items(tmp_path), now=NOW)["twitch:A"].meta.ai
    assert failing.disabled_reason
    calls = len(server.calls("POST", "/v1/messages"))
    failing.review(items(tmp_path), now=NOW)  # disabled for the rest of the run: no new request
    assert len(server.calls("POST", "/v1/messages")) == calls


def test_a_section_picked_by_hand_is_kept_whole(server, tmp_path):
    answer = {"clips": [{"id": "c1", "verdict": "publish", "reason": "", "score": 70, "hook": "H", "title": "T",
                         "description": "D", "hashtags": [], "trim_start": 5, "trim_end": 20}]}  # fmt: skip
    server.route("POST", "/v1/messages", (200, {}, message(answer)))
    picked = make_candidate("manual:abc", "inoxtag", source="manual", manual=True, start=0.0, end=60.0)
    [review] = curator(server).review([ReviewItem(picked, 60.0)], now=NOW).values()
    assert review.meta.ai and (review.meta.trim_start, review.meta.trim_end) == (None, None)


def test_missing_ids_get_the_template(server, tmp_path):
    answer = {"clips": [{"id": "c2", "verdict": "publish", "reason": "", "score": 50, "hook": "H", "title": "T",
                         "description": "D", "hashtags": [], "trim_start": 0, "trim_end": 25}]}  # fmt: skip
    server.route("POST", "/v1/messages", (200, {}, message(answer)))
    reviews = curator(server).review(items(tmp_path), now=NOW)
    assert reviews["twitch:B"].meta.ai and not reviews["twitch:A"].meta.ai


def test_models_without_server_side_fallback_do_not_send_it(server, tmp_path):
    server.route("POST", "/v1/messages", (200, {}, message({"clips": []})))
    curator(server, build(Config, {"ai": {"model": "claude-haiku-5-5", "effort": "low"}})).review(items(tmp_path), now=NOW)
    [request] = server.calls("POST", "/v1/messages")
    assert "fallbacks" not in request.json() and "server-side-fallback" not in request.headers.get("anthropic-beta", "")


def test_compilation_meta(server):
    clips = [(make_candidate("twitch:a", "gotaga"), "clutch"), (make_candidate("twitch:b", "squeezie"), "fou rire")]
    server.route("POST", "/v1/messages", (200, {}, message({"title": "Les pépites de la semaine", "intro": "Une semaine folle.",
                                                            "hashtags": ["BestOf", "Twitch FR"], "thumbnail_text": "SEMAINE DE FOU"})))  # fmt: skip
    meta = curator(server).compilation_meta(clips, period="du 20/10 au 27/10/2026")
    assert meta.ai and meta.title == "Les pépites de la semaine" and meta.hashtags[:2] == ["bestof", "twitchfr"]
    fallback = template_compilation_meta(clips, build(Config, {}), period="p")
    assert fallback.title.startswith("TOP 2") and "Squeezie" in fallback.title.split("(")[1].split(",")[0]


def test_template_review_and_trim():
    item = ReviewItem(make_candidate("youtube:abc@0-100", "chan", source="youtube", title="Ma vidéo 😂"), 100.0)
    review = template_review(item, build(Config, {}))
    assert review.verdict == "publish" and not review.meta.ai
    assert review.meta.title == "Chan : Ma vidéo" and review.meta.hook == "Ma vidéo"
    assert default_trim(item.candidate, 100.0) == pytest.approx((18.45, 77.45))
    assert default_trim(item.candidate, 40.0) == (None, None)
    assert Review.from_dict(review.to_dict()).meta == review.meta
    replay = template_review(ReviewItem(make_candidate("twitch:C", "chan"), 53.0, clip_span=(20.0, 45.0)), build(Config, {}))
    assert (replay.meta.trim_start, replay.meta.trim_end) == (20.0, 45.0)  # without Claude: the clip as the viewers cut it
    assert hashtag("Joueur du Grenier") == "joueurdugrenier" and hashtag("Été #1") == "ete1"


def test_throwaway_clip_titles_are_replaced():
    for junk in ["f", "lol", "...", "😂😂😂", "W"]:
        item = ReviewItem(make_candidate("twitch:x", "chan", title=junk), 30.0)
        assert clip_title(item.candidate) == ""
        meta = template_review(item, build(Config, {})).meta
        assert meta.hook == meta.title == "Moment fort de Chan" and meta.description == "Un moment fort de Chan en live."
    assert clip_title(make_candidate("twitch:y", "chan", title="WTF ce clip")) == "WTF ce clip"


def test_a_rejected_fallback_beta_is_retried_without_it(server, tmp_path):
    def handler(request):
        if "fallbacks" in request.json():
            return 400, {}, {"type": "error", "error": {"type": "invalid_request_error", "message": "fallbacks: unknown beta"}}
        return 200, {}, message({"clips": [{"id": "c1", "verdict": "publish", "reason": "", "score": 70, "hook": "H",
                                            "title": "T", "description": "D", "hashtags": [], "trim_start": 0, "trim_end": 30}]})  # fmt: skip

    server.route("POST", "/v1/messages", handler)
    reviews = curator(server).review(items(tmp_path)[:1], now=NOW)
    assert reviews["twitch:A"].meta.ai and reviews["twitch:A"].score == 70
    assert len(server.calls("POST", "/v1/messages")) == 2
