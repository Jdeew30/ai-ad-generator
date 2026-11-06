import re

from clipper.captions import (
    TextLayout,
    build_card_ass,
    build_lower_third_ass,
    build_short_ass,
    chunk_words,
    clean,
    style_color,
    tag_color,
    timestamp,
    wrap,
)
from clipper.models import Word
from clipper.transcribe import shift_words


def words(*items):
    return [Word(start, end, text) for start, end, text in items]


def dialogues(ass: str, style: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:") and f",{style}," in line]


def test_colors_and_timestamps():
    assert style_color("#FFE600") == "&H0000E6FF"
    assert tag_color("#FFE600") == "&H00E6FF&"
    assert timestamp(0) == "0:00:00.00"
    assert timestamp(83.456) == "0:01:23.46"
    assert timestamp(3723.4) == "1:02:03.40"


def test_clean_neutralises_override_syntax_and_emoji():
    assert clean("{\\b1}salut 😂 c'est fou\\N") == "(/b1)salut c'est fou/N"
    assert clean("  « Quoi ? »  ") == "« Quoi ? »"


def test_chunks_break_on_size_pauses_and_punctuation():
    chunks = chunk_words(words(
        (0.0, 0.2, "Oh"), (0.2, 0.4, "mais"), (0.4, 0.6, "attends,"), (0.7, 0.9, "regarde"),
        (2.0, 2.2, "ça"), (2.2, 2.5, "incroyablement"), (2.5, 2.7, "fort"),
    ))  # fmt: skip
    # comma closes a burst; a 1.1 s pause too; 16 characters at most per burst
    assert [[w.text for w in chunk] for chunk in chunks] == [["Oh", "mais", "attends,"], ["regarde"], ["ça"], ["incroyablement"], ["fort"]]
    many = chunk_words(words((0, 0.1, "a"), (0.1, 0.2, "b"), (0.2, 0.3, "c"), (0.3, 0.4, "d")))
    assert [[w.text for w in chunk] for chunk in many] == [["a", "b", "c"], ["d"]]


def test_short_ass_highlights_one_word_at_a_time():
    layout = TextLayout(caption_y=1400, hook_y=470, hook_until=3.0, credit_y=1300)
    ass = build_short_ass(
        words((0.5, 0.8, "Il"), (0.8, 1.2, "rage"), (1.2, 1.6, "quit")),
        layout,
        duration=5.0,
        hook="Il pète un câble en plein live 😂",
        credit="twitch.tv/squeezie",
        highlight="#FFE600",
    )
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    captions = dialogues(ass, "Caption")
    assert len(captions) == 3
    assert captions[0].startswith("Dialogue: 0,0:00:00.50,0:00:00.80,Caption")
    assert "{\\c&H00E6FF&}IL{\\c&HFFFFFF&} RAGE QUIT" in captions[0]
    assert "IL {\\c&H00E6FF&}RAGE{\\c&HFFFFFF&} QUIT" in captions[1]
    assert "\\pos(540,1400)" in captions[0] and "\\t(0,90" in captions[0]  # pop-in on each new burst
    assert captions[2].split(",")[2] == "0:00:01.95"  # last word lingers 0.35 s
    [hook] = dialogues(ass, "Hook")
    assert hook.split(",")[2] == "0:00:03.00" and "IL PÈTE UN CÂBLE EN\\NPLEIN LIVE" in hook and "😂" not in hook
    assert hook.split(",")[1] == "0:00:00.00" and "\\fad(0," in hook  # on the very first frame: the TikTok cover
    [credit] = dialogues(ass, "Credit")
    assert credit.endswith("twitch.tv/squeezie") and credit.split(",")[2] == "0:00:05.00"


def test_short_ass_never_overlaps_bursts_and_clips_to_duration():
    ass = build_short_ass(
        words((0.0, 0.5, "un"), (0.5, 1.0, "deux"), (1.0, 1.5, "trois"), (1.55, 2.0, "quatre"), (9.0, 12.0, "tard")),
        TextLayout(),
        duration=10.0,
    )
    spans = []
    for line in dialogues(ass, "Caption"):
        start, end = line.split(",")[1:3]
        spans.append((start, end))
    assert spans[2][1] <= spans[3][0]  # "trois" ends when "quatre" starts
    assert spans[-1][1] == "0:00:10.00"
    assert not dialogues(ass, "Hook") and not dialogues(ass, "Credit")


def test_shift_words_after_trimming():
    shifted = shift_words(words((1.0, 2.0, "avant"), (4.8, 5.4, "coupé"), (6.0, 6.5, "dedans"), (12.0, 13.0, "après")), 5.0, 11.0)
    assert [(w.start, w.end, w.text) for w in shifted] == [(0.0, 0.4, "coupé"), (1.0, 1.5, "dedans")]


def test_lower_third_and_card():
    ass = build_lower_third_ass(rank=7, name="Gotaga", label="Le clutch de l'année " * 5, credit="twitch.tv/gotaga", duration=20)
    assert "#7" in dialogues(ass, "Rank")[0] and "GOTAGA" in dialogues(ass, "Name")[0]
    assert len(re.sub(r"\{[^}]*\}", "", dialogues(ass, "Label")[0].split(",,", 1)[1].split(",", 4)[-1])) <= 60
    assert dialogues(ass, "Band") and dialogues(ass, "Credit")[0].split(",")[2] == "0:00:20.00"
    card = build_card_ass(title="Top 12 des clips de la semaine", subtitle="du 20/10 au 27/10/2026", duration=2.5)
    assert "TOP 12 DES CLIPS DE LA\\NSEMAINE" in card and "DU 20/10 AU 27/10/2026" in card


def test_wrap():
    assert wrap("un deux trois quatre", 9) == ["un deux", "trois", "quatre"]
