from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .captions import clean, wrap
from .render import FONT_FILE

SIZE = (1280, 720)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_FILE), size)


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_lines: int, start: int, minimum: int) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Largest font size at which `text` fits in `max_lines` lines of `max_width` pixels."""
    size = start
    while True:
        font = _font(size)
        for width in range(30, 5, -1):
            lines = wrap(text, width)
            if len(lines) <= max_lines and all(draw.textlength(line, font=font) <= max_width for line in lines):
                return lines, font
        if size <= minimum:
            return wrap(text, 14)[:max_lines], font
        size -= 8


def make_thumbnail(frame: Path, out: Path, *, headline: str, subline: str | None = None, highlight: str = "#FFE600") -> Path:
    """1280x720 YouTube thumbnail: punchy frame, dark left gradient, huge outlined text."""
    with Image.open(frame) as source:
        image = ImageOps.fit(source.convert("RGB"), SIZE, method=Image.Resampling.LANCZOS)
    image = ImageEnhance.Contrast(image).enhance(1.15)
    image = ImageEnhance.Color(image).enhance(1.3)

    shade = Image.new("L", SIZE, 0)
    shade_draw = ImageDraw.Draw(shade)
    for x in range(SIZE[0]):
        shade_draw.line([(x, 0), (x, SIZE[1])], fill=int(200 * max(0.0, 1 - x / (SIZE[0] * 0.75))))
    image = Image.composite(Image.new("RGB", SIZE, "black"), image, shade)

    draw = ImageDraw.Draw(image)
    text = clean(headline).upper() or " "
    lines, font = _fit_lines(draw, text, max_width=760, max_lines=3, start=150, minimum=70)
    line_height = int(font.size * 1.08)
    y = (SIZE[1] - line_height * len(lines)) // 2 - (30 if subline else 0)
    for index, line in enumerate(lines):
        fill = highlight if index == 0 else "white"
        draw.text((56, y), line, font=font, fill=fill, stroke_width=max(6, font.size // 14), stroke_fill="black")
        y += line_height
    if subline and clean(subline):
        small = _font(48)
        line = clean(subline).upper()
        while draw.textlength(line, font=small) > SIZE[0] - 112 and len(line) > 4:
            line = line[:-2].rstrip() + "…"
        draw.text((58, SIZE[1] - 92), line, font=small, fill="white", stroke_width=4, stroke_fill="black")

    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "JPEG", quality=90, optimize=True)
    if out.stat().st_size > 2_000_000:  # YouTube refuses thumbnails over 2 MB
        image.save(out, "JPEG", quality=75, optimize=True)
    return out
