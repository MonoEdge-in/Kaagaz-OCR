"""Choose each OCR page's DPI from the size of its text (council 5 Oct 2026, PROVISIONAL).

Today every page sent to OCR is drawn at OCR_DPI (400 on the GPU server). The
council found most of those pixels thrown away: layout detection resizes the
page to its own input size, each large block is fitted to OCR_VL_MAX_PIXELS,
and RapidOCR fits the page to a 2000-pixel long side. Only small blocks (one
field, one number) use all 400 DPI. ``OCR_DPI_RULE`` says how a page's DPI is
chosen:

* ``fixed`` (the default): today's DPI, OCR_DPI, or lower when the page is too
  big for the pixel limits (``size_fit``).
* ``text_height``: from how tall the page's text is, measured on a small probe
  picture (``line_height_px``)::

      dpi = min(size_fit, max(72, min(text_dpi, scan_dpi)))
      text_dpi = 72 x TARGET_LINE_PX / (line height in points)

  so the DPI is never above OCR_DPI, never below 72, and the size limit still
  wins. ``scan_dpi`` (the scan cap) is the resolution of the page's largest
  image, only on a page R2 calls a scan (``text_quality.scan_like``: images
  over SCAN_IMAGE_COVERAGE, under 200 visible characters): above it there is
  no new detail, only more pixels. On any other page it is None, so a small
  logo or a background tint under vector text never decides.
* ``text_height_no_scan_cap``: the same without the scan cap, so the accuracy
  test can compare the two.

When the text cannot be measured (None), the page is drawn at ``size_fit``, as
today. A photo is shrunk by TARGET_LINE_PX over its line height, never
enlarged, and never below 72 DPI on A4 (``photo_scale``).

TARGET_LINE_PX, 48 (PROVISIONAL, to be set by a sweep on the T4): RapidOCR's
PP-OCRv5 recogniser resizes every line to 48 pixels high (rapidocr 3.9.2
config, ``rec_img_shape: [3, 48, 320]``), so a taller line is only shrunk
again. PaddleOCR-VL sees a picture in 28-pixel tokens (inferred: its pixel
bounds, 112,896 and 1,003,520, are 144 and 1,280 times 28 x 28), and 48 pixels
is about 1.7 tokens a line, room for the Devanagari headline and the marks
above and below it.

``line_height_px``, Pillow only and not tuned to any script:

1. Grey (so a grey and an RGB picture give the same answer), then Otsu's
   threshold from the histogram: a pixel at or under it is ink.
2. None when the picture is blank, or more than MAX_INK_SHARE of it is ink
   (noise, a photo, a dark page).
3. Ruled lines are taken out: a pixel column that is ink over a whole block
   of RULE_SPAN_PX rows (one BOX resize, in C) is a table's line or a picture,
   never text, which has gaps between its lines; with it go the blocks just
   above and below, where the line ends. Then a pixel row that is ink over a
   block of ROW_RULE_SPAN_PX columns (shorter, so a line drawn at a slight
   tilt still counts), without its neighbours, so a Devanagari headline keeps
   the letters hanging from it.
4. The picture is cut into STRIPS vertical strips, and one BOX resize to
   STRIPS x height (in C) gives each row's ink in each strip; a row with any
   ink is inked. A strip more than MAX_STRIP_INK inked (a picture, a border)
   is left out. In each other strip, a run of inked rows is one line, unless
   it is under MIN_RUN_POINTS (3.5 pt, from the picture's ``dpi``: 5 pixels
   on an A4 probe, 4 on A3's, 2 on A2's). A shorter run is a speck or a mark,
   or small print the probe cannot show: it is counted when it holds at least
   SHORT_RUN_INK of a row's ink, and with more than MAX_SHORT_SHARE of all
   the runs, None (the page stays at the ceiling). The council's minimum was
   2 pixels; on the six xlang scans (tilted, with noise) the runs of 2 to 4
   pixels were specks, the dots and marks of letters, and ruled lines drawn
   at a slight tilt, and they pulled the 20th percentile down until no page
   could be measured.
5. None when the page is turned a quarter (the projection-profile test): over
   the inked area, the ink per column varies more than the ink per row. Lines
   across the page make the rows go from ink to paper and back; the gaps
   between letters of different lines do not line up, so the columns stay
   even.
6. None with fewer than MIN_RUNS runs, or when the 80th percentile height is
   over MAX_SPREAD times the 20th (handwriting, or a mix too wide to trust).
   Otherwise the line height is the 20th percentile, so the small text
   decides.

Time: O(P) for the P pixels of the probe (grey, threshold, the resizes and the
crop, all in C), then O(rows x STRIPS + columns) Python steps and O(n log n)
for the n runs. No randomness, and whole-number cuts: the same picture always
gives the same answer.

ponytail: the probe is one more picture per page (``PROBE_LONG_SIDE``, about
1200 pixels on its long side), paid even when the rule then gives OCR_DPI: on
a laptop CPU, the eight xlang pages took 18 to 44 ms to draw and 8 to 16 ms to
measure (5 Oct 2026; not measured on the T4).
ponytail: a row with any ink is inked, so a speck between two lines joins
them in its strip; the 20th percentile still comes from the other strips.
ponytail: small print on fewer than a fifth of the runs (one footer line)
does not decide; the page is drawn for the rest of its text. A photo's
``dpi`` takes its short side as A4's.
"""

import math
import statistics

from PIL import Image, ImageChops

from kaagaz.preparing.page_checks import A4_SHORT_SIDE_INCHES

FIXED = "fixed"
TEXT_HEIGHT = "text_height"
TEXT_HEIGHT_NO_SCAN_CAP = "text_height_no_scan_cap"
DPI_RULES = frozenset({FIXED, TEXT_HEIGHT, TEXT_HEIGHT_NO_SCAN_CAP})

# PROVISIONAL, council 5 Oct 2026 (the module notes); recorded in each run's config.
TARGET_LINE_PX = 48  # a line's height in the picture the engine is given
PROBE_LONG_SIDE = 1200  # the probe picture's long side, in pixels
MIN_DPI = 72  # never drawn lower (pdf_reader's lowest)
STRIPS = 12
RULE_SPAN_PX = 32  # rows of unbroken ink in one pixel column: a ruled column line
ROW_RULE_SPAN_PX = 16  # columns of unbroken ink in one pixel row: a ruled row line
RULE_FILL = 0.9  # of a block's pixels
MAX_STRIP_INK = 0.8  # of a strip's rows
MIN_RUN_POINTS = 3.5  # a shorter run is a speck, a mark or small print (step 4)
SHORT_RUN_INK = 0.5  # of a row: a short run with this much ink may be small print
MAX_SHORT_SHARE = 0.25  # of the runs: more such short runs, and small print may be missed
MIN_RUNS = 6
MAX_SPREAD = 3.0  # the 80th percentile height over the 20th
MAX_INK_SHARE = 0.4  # of the picture's pixels

_POINTS_PER_INCH = 72.0
_INK, _PAPER = 255, 0  # the threshold picture: ink is white, so getbbox finds it


def probe_dpi(size_pt: tuple[float, float], fit_dpi: int) -> int | None:
    """The DPI that draws the page about PROBE_LONG_SIDE pixels on its long side,
    at least 1 and never above ``fit_dpi``; None for a page with no usable size. O(1)."""
    long_side = max(size_pt)
    if not (math.isfinite(long_side) and long_side > 0):
        return None
    return max(1, min(fit_dpi, math.floor(PROBE_LONG_SIDE * _POINTS_PER_INCH / long_side)))


def chosen_dpi(fit_dpi: int, line_px: int | None, probe: int, scan_dpi: int | None) -> int:
    """The page's OCR DPI by the rule in the module notes, from the line height
    ``line_px`` measured on a picture drawn at ``probe`` DPI. ``fit_dpi`` (today's
    DPI) when the text was not measured; never above it, never below MIN_DPI unless
    it is. O(1)."""
    if line_px is None or line_px < 1 or probe < 1:
        return fit_dpi
    wanted = probe * TARGET_LINE_PX / line_px  # = 72 x TARGET_LINE_PX / line height in points
    if scan_dpi is not None:
        wanted = min(wanted, scan_dpi)
    return min(fit_dpi, max(MIN_DPI, math.ceil(wanted)))


def photo_scale(line_px: int | None, short_side_px: int) -> float:
    """How much a photo is shrunk for OCR: TARGET_LINE_PX over its line height, but
    never enlarged (at most 1) and never under 72 DPI on A4's short side; 1.0 when
    the text was not measured. O(1)."""
    if line_px is None or line_px < 1 or short_side_px < 1:
        return 1.0
    floor = MIN_DPI * A4_SHORT_SIDE_INCHES / short_side_px
    return min(1.0, max(TARGET_LINE_PX / line_px, floor))


def line_height_px(image: Image.Image, *, dpi: float) -> int | None:
    """The height in pixels of the picture's smaller text lines, or None when it
    cannot be trusted, by the steps in the module notes; ``dpi`` is the picture's
    (for a photo, its short side over A4's), for the shortest line. O(P)."""
    if image.width < 1 or image.height < 1:
        return None
    grey = image.convert("L")
    histogram = grey.histogram()
    cut = otsu_threshold(histogram)
    if cut is None or sum(histogram[: cut + 1]) > MAX_INK_SHARE * grey.width * grey.height:
        return None
    ink = _without_lines(grey.point([_INK if value <= cut else _PAPER for value in range(256)]))
    shortest = max(2, round(MIN_RUN_POINTS * dpi / _POINTS_PER_INCH))
    heights, short = _run_heights(ink, shortest)
    heights.sort()
    if len(heights) < MIN_RUNS or short > MAX_SHORT_SHARE * (len(heights) + short) or _turned(ink):
        return None
    low = heights[(len(heights) - 1) * 20 // 100]
    high = heights[(len(heights) - 1) * 80 // 100]
    return None if high > MAX_SPREAD * low else low


def otsu_threshold(histogram: list[int]) -> int | None:
    """Otsu's threshold of a 256-bin grey histogram: the value t that best splits
    the pixels into "at or under t" and "over t" (the most variance between the two);
    the lowest such t, so ties are settled the same way every time. None when there
    is no split (every pixel the same grey). O(256)."""
    total = sum(histogram)
    weighted = sum(value * count for value, count in enumerate(histogram))
    below = below_weighted = 0
    best: tuple[float, int] | None = None
    for value, count in enumerate(histogram[:255]):
        below += count
        below_weighted += value * count
        above = total - below
        if below == 0 or above == 0:
            continue
        gap = below_weighted / below - (weighted - below_weighted) / above
        between = below * above * gap * gap
        if best is None or between > best[0]:
            best = (between, value)
    return None if best is None else best[1]


def _without_lines(ink: Image.Image) -> Image.Image:
    """The ink without its ruled column lines, then its ruled row lines (step 3)."""
    columns = _without_column_lines(ink, RULE_SPAN_PX, grow=True)
    rows = _without_column_lines(columns.transpose(Image.Transpose.ROTATE_90), ROW_RULE_SPAN_PX, grow=False)
    return rows.transpose(Image.Transpose.ROTATE_270)


def _without_column_lines(ink: Image.Image, span: int, *, grow: bool) -> Image.Image:
    """The ink without the pixel columns that are ink over a whole block of ``span``
    rows; with ``grow``, the blocks just above and below too, where the line ends part
    way. O(P), in C."""
    blocks = ink.resize((ink.width, max(1, ink.height // span)), Image.Resampling.BOX)
    lines = blocks.point([_INK if value >= RULE_FILL * _INK else _PAPER for value in range(256)])
    grown = lines
    for shift in (-1, 1) if grow else ():
        moved = Image.new("L", lines.size, _PAPER)
        moved.paste(lines, (0, shift))
        grown = ImageChops.lighter(grown, moved)
    return ImageChops.subtract(ink, grown.resize(ink.size, Image.Resampling.NEAREST))


def _turned(ink: Image.Image) -> bool:
    """Whether the ink's lines run up the page (step 5). O(P) in C, then O(rows + columns)."""
    inked = ink.crop(ink.getbbox())
    rows = inked.resize((1, inked.height), Image.Resampling.BOX).tobytes()
    columns = inked.resize((inked.width, 1), Image.Resampling.BOX).tobytes()
    return statistics.pvariance(columns) > statistics.pvariance(rows)


def _run_heights(ink: Image.Image, shortest: int) -> tuple[list[int], int]:
    """The heights of the runs of inked rows, at least ``shortest``, in every strip
    that is not mostly ink, and how many shorter runs held at least SHORT_RUN_INK
    of a row's ink (step 4). O(rows x STRIPS)."""
    profile = ink.resize((STRIPS, ink.height), Image.Resampling.BOX).tobytes()  # row by row
    heights: list[int] = []
    short = 0
    for strip in range(STRIPS):
        column = profile[strip::STRIPS]  # this strip's ink in each row, 0 to 255
        if len(column) - column.count(_PAPER) > MAX_STRIP_INK * len(column):
            continue
        start = -1
        for row, value in enumerate(column + bytes([_PAPER])):  # a paper row closes the last run
            if value != _PAPER:
                start = row if start < 0 else start
            elif start >= 0:
                if row - start >= shortest:
                    heights.append(row - start)
                elif sum(column[start:row]) >= SHORT_RUN_INK * _INK:
                    short += 1
                start = -1
    return heights, short
