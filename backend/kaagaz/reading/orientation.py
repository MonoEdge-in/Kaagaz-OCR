"""Which way up the writing on a page picture is (#709: the viewer's Straighten pages, and its Make
all pages wide or tall). A picture in, a turn out; nothing stored, nothing logged.

RapidOCR's text finder gives the page's lines (``RapidOcrPool.find_boxes``). A line is much longer
than it is thick, so most lines running down the page mean it lies sideways (a turn of 90 or 270)
and across it, upright or upside down (0 or 180). The longest lines are then read both of those
ways (``read_crop``), by the Latin recogniser first and, when that can't tell, the Devanagari one:
the right way up a line reads as many letters, the wrong way as few. ``read_crop`` gives the text
alone, no score, so the measure is the count of letters and digits read (``str.isalnum``). Each
script decides on its own: the Devanagari recogniser reads upside-down Latin as nearly as many
letters as upright, so the two are never added. Too few lines, or no clear winner: unsure (None).
"""

from PIL import Image

from kaagaz.reading.rapid_pool import PixelBox, RapidOcrPool

SIDE = 1280  # the picture is first shrunk to this longest side: lines stay readable, the finder quick
LINE_SHAPE = 2.0  # a line's long side is at least this many times its short side
MIN_LINES = 5  # fewer lines one way than this: unsure
LINES_READ = 3  # the longest lines are read
PIECE = 12  # of each, its middle at most this many thicknesses long (a long piece reads slowly)
MIN_LETTERS = 8  # the winning way reads at least this many letters and digits over those lines,
CLEAR_LEAD = 2.0  # and at least this many times the other way's


def upright_turn(pool: RapidOcrPool, picture: Image.Image) -> int | None:
    """The clockwise turn, 0, 90, 180 or 270, that puts the writing on ``picture`` upright; None
    when unsure. One find on the shrunk picture, then 2 x LINES_READ short reads per script."""
    small = picture.convert("RGB")  # a copy: shrunk in place below
    try:
        small.thumbnail((SIDE, SIDE))
        lines = [box for box in pool.find_boxes(small) if _long(box) >= LINE_SHAPE * _short(box)]
        down = [box for box in lines if box[3] - box[1] > box[2] - box[0]]
        across = [box for box in lines if box[3] - box[1] < box[2] - box[0]]
        kept = max(down, across, key=len)
        if len(kept) < MIN_LINES:
            return None
        ways = (90, 270) if kept is down else (0, 180)
        pieces = [_piece(small, box) for box in sorted(kept, key=_long, reverse=True)[:LINES_READ]]
        for devanagari in (False, True):
            letters = {
                way: sum(_letters(pool.read_crop(piece.rotate(-way, expand=True), devanagari=devanagari))
                         for piece in pieces)
                for way in ways
            }  # fmt: skip
            best, other = sorted(ways, key=letters.__getitem__, reverse=True)
            if letters[best] >= MIN_LETTERS and letters[best] >= CLEAR_LEAD * letters[other]:
                return best
        return None
    finally:
        small.close()


def _long(box: PixelBox) -> float:
    return max(box[2] - box[0], box[3] - box[1])


def _short(box: PixelBox) -> float:
    return min(box[2] - box[0], box[3] - box[1])


def _piece(picture: Image.Image, box: PixelBox) -> Image.Image:
    """The line's middle, at most PIECE of its thicknesses long (either way of reading it, the
    middle holds whole letters)."""
    x0, y0, x1, y1 = box
    half = PIECE * _short(box) / 2
    if x1 - x0 >= y1 - y0:
        middle = (x0 + x1) / 2
        x0, x1 = max(x0, middle - half), min(x1, middle + half)
    else:
        middle = (y0 + y1) / 2
        y0, y1 = max(y0, middle - half), min(y1, middle + half)
    return picture.crop((int(x0), int(y0), int(x1 + 0.5), int(y1 + 0.5)))


def _letters(read: str) -> int:
    return sum(character.isalnum() for character in read)
