from __future__ import annotations

from dataclasses import dataclass

from .models import Word

FONT = "Anton"
_SENTENCE_END = (".", "!", "?", "…", ",")


@dataclass(frozen=True)
class TextLayout:
    """Where text goes on a frame (pixels, ASS PlayRes = frame size)."""

    width: int = 1080
    height: int = 1920
    caption_y: int = 1420
    hook_y: int = 470
    hook_until: float | None = None  # None: the hook stays for the whole video
    credit_y: int | None = 1300
    caption_size: int = 96
    hook_size: int = 66
    credit_size: int = 38


def style_color(hex_color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS style colour '&HAABBGGRR'."""
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"couleur invalide : {hex_color!r} (format #RRGGBB attendu)")
    int(value, 16)
    return f"&H{alpha:02X}{value[4:6]}{value[2:4]}{value[0:2]}".upper()


def tag_color(hex_color: str) -> str:
    """'#RRGGBB' -> ASS override colour '&HBBGGRR&'."""
    return style_color(hex_color)[:2] + style_color(hex_color)[4:] + "&"


def timestamp(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, rest = divmod(centis, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def clean(text: str) -> str:
    """Neutralises ASS override syntax and drops glyphs a Latin display font cannot draw (emoji)."""
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    kept = "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "’‘“”…–—€")
    return " ".join(kept.split())


def wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def chunk_words(words: list[Word], *, max_words: int = 3, max_chars: int = 16, max_gap: float = 0.6) -> list[list[Word]]:
    """Groups words into the 1-3 word bursts shown one after another."""
    chunks: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        text = clean(word.text)
        if not text:
            continue
        word = Word(start=word.start, end=max(word.end, word.start + 0.05), text=text)
        if current:
            length = len(" ".join(w.text for w in current)) + 1 + len(text)
            gap = word.start - current[-1].end
            if len(current) >= max_words or length > max_chars or gap > max_gap or current[-1].text.endswith(_SENTENCE_END):
                chunks.append(current)
                current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def _header(width: int, height: int, styles: list[str]) -> list[str]:
    return [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]


def _event(layer: int, start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: {layer},{timestamp(start)},{timestamp(end)},{style},,0,0,0,,{text}"


def build_short_ass(
    words: list[Word],
    layout: TextLayout,
    *,
    duration: float,
    hook: str | None = None,
    credit: str | None = None,
    highlight: str = "#FFE600",
    uppercase: bool = True,
) -> str:
    """Word-by-word captions (current word highlighted), a hook title box and a credit line."""
    styles = [
        f"Style: Caption,{FONT},{layout.caption_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,"
        "0,0,0,0,100,100,1,0,1,7,3,5,40,40,0,1",
        f"Style: Hook,{FONT},{layout.hook_size},&H00141414,&H00141414,&H00FFFFFF,&H00000000,"
        "0,0,0,0,100,100,0,0,3,16,0,5,60,60,0,1",
        f"Style: Credit,{FONT},{layout.credit_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,1,0,1,3,1,5,40,40,0,1",
    ]
    center = layout.width // 2
    events: list[str] = []
    if hook and clean(hook):
        lines = wrap(clean(hook).upper(), 20)[:3]
        until = min(duration, layout.hook_until) if layout.hook_until else duration
        events.append(_event(2, 0.0, until, "Hook", f"{{\\an5\\pos({center},{layout.hook_y})\\fad(150,150)}}" + "\\N".join(lines)))
    if credit and layout.credit_y is not None and clean(credit):
        events.append(_event(1, 0.0, duration, "Credit", f"{{\\an5\\pos({center},{layout.credit_y})}}{clean(credit)}"))

    active = tag_color(highlight)
    chunks = chunk_words(words)
    for index, chunk in enumerate(chunks):
        chunk_end = chunk[-1].end + 0.35
        if index + 1 < len(chunks):
            chunk_end = min(chunk_end, chunks[index + 1][0].start)
        chunk_end = min(chunk_end, duration)
        texts = [w.text.upper() if uppercase else w.text for w in chunk]
        for position, word in enumerate(chunk):
            start = chunk[0].start if position == 0 else word.start
            end = chunk[position + 1].start if position + 1 < len(chunk) else chunk_end
            start, end = max(0.0, start), min(end, duration)
            if end - start < 0.04:
                continue
            body = " ".join(
                f"{{\\c{active}}}{text}{{\\c&HFFFFFF&}}" if k == position else text for k, text in enumerate(texts)
            )
            pop = "\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)" if position == 0 else ""
            events.append(_event(0, start, end, "Caption", f"{{\\an5\\pos({center},{layout.caption_y}){pop}}}{body}"))
    return "\n".join(_header(layout.width, layout.height, styles) + events) + "\n"


def build_lower_third_ass(
    *,
    rank: int | None,
    name: str,
    label: str,
    credit: str | None,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    highlight: str = "#FFE600",
) -> str:
    """Compilation overlay: '#7 Streamer / clip title' sliding in for a few seconds + a credit."""
    styles = [
        f"Style: Band,{FONT},20,&H70000000,&H70000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Rank,{FONT},120,{style_color(highlight)},&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,6,3,4,0,0,0,1",
        f"Style: Name,{FONT},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,5,2,4,0,0,0,1",
        f"Style: Label,{FONT},52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,4,2,4,0,0,0,1",
        f"Style: Credit,{FONT},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,3,1,9,0,0,0,1",
    ]
    shown = min(duration, 4.5)
    left = 70
    text_x = left + (190 if rank is not None else 0)
    name_text = clean(name).upper()
    line = clean(label)
    line = line if len(line) <= 60 else line[:59].rstrip() + "…"
    # Anton is narrow: ~0.45 em per character is a safe estimate for the band width.
    band_w = min(width - 60, int(text_x + max(len(name_text) * 72, len(line) * 52) * 0.47 + 70))
    band_top = height - 262
    events: list[str] = [
        _event(1, 0.0, shown, "Band", f"{{\\move({-band_w},{band_top},0,{band_top},0,250)\\fad(0,250)\\p1}}m 0 0 l {band_w} 0 {band_w} 222 0 222{{\\p0}}")
    ]
    if rank is not None:
        events.append(_event(2, 0.0, shown, "Rank", f"{{\\move({left - 400},{height - 150},{left},{height - 150},0,250)\\fad(0,250)}}#{rank}"))
    events.append(_event(2, 0.0, shown, "Name", f"{{\\move({text_x - 400},{height - 190},{text_x},{height - 190},60,310)\\fad(0,250)}}{name_text}"))
    if line:
        events.append(_event(2, 0.0, shown, "Label", f"{{\\move({text_x - 400},{height - 112},{text_x},{height - 112},120,370)\\fad(0,250)}}{line}"))
    if credit and clean(credit):
        events.append(_event(1, 0.0, duration, "Credit", f"{{\\an9\\pos({width - 40},34)}}{clean(credit)}"))
    return "\n".join(_header(width, height, styles) + events) + "\n"


def build_card_ass(*, title: str, subtitle: str | None, duration: float, width: int = 1920, height: int = 1080, highlight: str = "#FFE600") -> str:
    """Intro card of a compilation."""
    styles = [
        f"Style: Title,{FONT},128,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,6,4,5,80,80,0,1",
        f"Style: Sub,{FONT},64,{style_color(highlight)},&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,4,2,5,80,80,0,1",
    ]
    lines = wrap(clean(title).upper(), 24)[:3]
    events = [_event(1, 0.0, duration, "Title", f"{{\\an5\\pos({width // 2},{height // 2 - 40})\\fad(200,250)}}" + "\\N".join(lines))]
    if subtitle and clean(subtitle):
        events.append(_event(1, 0.25, duration, "Sub", f"{{\\an5\\pos({width // 2},{height // 2 + 140})\\fad(200,250)}}{clean(subtitle).upper()}"))
    return "\n".join(_header(width, height, styles) + events) + "\n"
