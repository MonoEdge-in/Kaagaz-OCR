"""Pictures for tests, drawn with plain rectangles and never with a font.

A page picture that is plain white (or one flat colour) is blank under PRD 2
R3, so a reader never sends it to OCR. Tests that need the OCR engine to be
called use ``text_like_picture``: dark bars laid out like lines of words on
white paper. It is drawn only with ``ImageDraw.rectangle``, so it is the same
on every machine (a font would depend on what is installed), and it is far
from blank at every size a test uses (``tests/test_picture_support.py``
guards that).

``picture_with_bars`` draws any bars, for tests that model a page with a
known amount of ink (one text line, a small footer). ``noise_picture`` has
no pattern at all, for a lossless round trip or a picture with no lines.
"""

from collections.abc import Iterable

from PIL import Image, ImageDraw

PAPER = (255, 255, 255)
INK = (24, 24, 24)  # a dark print colour, well below paper white minus 48

# One bar: (left, top, width, height) in pixels.
Bar = tuple[int, int, int, int]

# The relative widths of the words on each line, in turn: a fixed pattern, so
# every run draws the same picture.
_WORD_WIDTHS = (5, 3, 7, 2, 4, 6)
_SMALLEST_SIDE = 8


def picture_with_bars(
    width: int, height: int, bars: Iterable[Bar], *, ink: tuple[int, int, int] = INK, mode: str = "RGB"
) -> Image.Image:
    """A ``width`` x ``height`` picture of white paper with each bar filled in ``ink``."""
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    for left, top, bar_width, bar_height in bars:
        if bar_width > 0 and bar_height > 0:
            # rectangle() includes both corners, so the far edge is one pixel in.
            draw.rectangle((left, top, left + bar_width - 1, top + bar_height - 1), fill=ink)
    return image if mode == "RGB" else image.convert(mode)


def text_like_bars(width: int, height: int) -> list[Bar]:
    """Bars laid out like a page of text: margins, even lines, words with gaps.

    O(lines x words) = O(width x height) at worst; a few hundred bars for a page.
    """
    if width < _SMALLEST_SIDE or height < _SMALLEST_SIDE:
        raise ValueError(f"a text-like picture needs at least {_SMALLEST_SIDE} x {_SMALLEST_SIDE} pixels")
    margin_x, margin_y = max(1, width // 10), max(1, height // 10)
    pitch = max(3, height // 24)  # the distance from one line to the next
    bar_height = max(1, pitch // 3)
    gap = max(1, width // 50)
    text_width = width - 2 * margin_x
    bars: list[Bar] = []
    top = margin_y
    word = 0
    while top + bar_height <= height - margin_y:
        left = margin_x
        while True:
            word_width = max(1, text_width * _WORD_WIDTHS[word % len(_WORD_WIDTHS)] // 30)
            if left + word_width > width - margin_x:
                break
            bars.append((left, top, word_width, bar_height))
            left += word_width + gap
            word += 1
        top += pitch
    return bars


def noise_picture(width: int, height: int, *, mode: str = "RGB") -> Image.Image:
    """Grey values with no pattern, from Knuth's multiplicative hash of each byte's
    place: the same on every run, and no seed (nothing here is a secret)."""
    size = width * height * len(mode)
    return Image.frombytes(mode, (width, height), bytes((k * 2654435761 >> 16) & 255 for k in range(size)))


def text_like_picture(width: int, height: int, *, mode: str = "RGB") -> Image.Image:
    """A picture that looks like a page of text, in ``mode``: never blank (R3)."""
    return picture_with_bars(width, height, text_like_bars(width, height), mode=mode)
