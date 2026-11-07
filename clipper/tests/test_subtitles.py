import json

from clipper.sources.subtitles import parse_captions, timed_transcript


def test_youtube_json3_captions_become_timed_lines(tmp_path):
    path = tmp_path / "abc.fr.json3"
    path.write_text(json.dumps({"events": [
        {"tStartMs": 0, "dDurationMs": 4000},  # a window event, without words
        {"tStartMs": 1200, "segs": [{"utf8": "Salut"}, {"utf8": " tout"}, {"utf8": " le monde"}]},
        {"tStartMs": 5000, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 16500, "segs": [{"utf8": "aujourd'hui on teste"}, {"utf8": " un truc de fou"}]},
        {"tStartMs": 3_725_000, "segs": [{"utf8": "et ça explose"}]},
    ]}), encoding="utf-8")  # fmt: skip
    cues = parse_captions(path)
    assert cues == [(1.2, "Salut tout le monde"), (16.5, "aujourd'hui on teste un truc de fou"), (3725.0, "et ça explose")]
    assert timed_transcript(cues) == ("[0:01] Salut tout le monde\n[0:16] aujourd'hui on teste un truc de fou\n"
                                      "[1:02:05] et ça explose")  # fmt: skip


def test_automatic_vtt_captions_keep_each_line_once(tmp_path):
    path = tmp_path / "abc.fr.vtt"
    path.write_text(
        "WEBVTT\nKind: captions\nLanguage: fr\n\n"
        "00:00:01.000 --> 00:00:03.000 align:start position:0%\nbon<00:00:01.500><c> alors</c>\n\n"
        "00:00:03.000 --> 00:00:05.000\nbon alors\nil se passe quoi\n\n"
        "01:00:07.500 --> 01:00:09.000\nil se passe quoi\nla fin\n",
        encoding="utf-8",
    )
    assert parse_captions(path) == [(1.0, "bon alors"), (3.0, "il se passe quoi"), (3607.5, "la fin")]


def test_the_transcript_groups_lines_and_stops_at_its_size(tmp_path):
    cues = [(float(second), f"mot{second}") for second in range(0, 60, 5)]
    lines = timed_transcript(cues, every=15.0).split("\n")
    assert lines[0] == "[0:00] mot0 mot5 mot10" and lines[1] == "[0:15] mot15 mot20 mot25" and len(lines) == 4
    cut = timed_transcript(cues, every=15.0, max_chars=40)
    assert cut.endswith("[…] (suite coupée)") and cut.count("\n") == 1
    assert timed_transcript([]) == ""
    broken = tmp_path / "abc.fr.json3"
    broken.write_text("<html>Too Many Requests</html>", encoding="utf-8")
    assert parse_captions(broken) == []
