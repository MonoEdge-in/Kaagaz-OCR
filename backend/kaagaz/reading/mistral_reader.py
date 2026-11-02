"""The Mistral OCR page reader (#473, Tushar 2 Nov 2026): one call to Mistral's ``/v1/ocr`` a
page (``MistralClient``), and its reply as a ``PageRead`` (``page_read``). The model list and
names are in ``kaagaz.reading.mistral_ocr``, which the API imports; this module is for the
Gemini reader's engine only.

**The call.** ``MistralClient.read`` posts the page's PNG as a data URI with
``include_image_base64: false`` and word confidences, and never ``table_format``, so tables
stay inline as Markdown. It returns ``(page, pages_processed)`` on every HTTP 200, because
Mistral bills a 200: ``page`` is the reply's ``pages[0]`` (``{}`` when the body is not one,
which ``page_read`` then refuses), ``pages_processed`` its ``usage_info.pages_processed``
(1 when missing: one page was sent). It never retries: waiting is the retry. Each call has
MISTRAL_TIMEOUT_SECONDS, capped by ``seconds`` (the page's time left).

**When Mistral can't read** it raises ``MistralUnavailable`` with ``code``:

* ``mistral_key`` (401, 402, 403: the key is wrong or the credit is used up, which a person
  fixes): Mistral is switched off on this client for KEY_OFF_SECONDS.
* ``mistral`` (429, any other status, the network, a timeout): after OUTAGES_IN_A_ROW of
  these in a row it is switched off for OUTAGE_OFF_SECONDS. A 200 resets the count.

While switched off, ``read`` raises at once and sends nothing. Only the status and the error
reply's ``type`` and ``code`` (short codes, ``openrouter._code``) are logged; its message,
which can echo page text, is never read into a log, and the key is never in a log or an
exception (``raise ... from None``).

**The page** (``page_read``). Lines come from the reply's blocks in order, or from its
Markdown when it has none (Mistral OCR 3), split on newlines, with only Markdown's own marks
taken off: ``#`` headings, ``**``/``__`` emphasis, a ``-``/``*``/``+`` bullet followed by a
space, and backslash escapes ("1." and "-500" are kept). Image lines and ``[tbl-...]``
placeholders are dropped; a table row becomes one line, its cells joined by two spaces, and
its separator row is dropped. Every line is NFC, with Gemini's limits (controls dropped, a
lone surrogate, a line over MAX_BLOCK_CHARS or more than MAX_BLOCKS_PER_PAGE lines or blocks
is a ``BadReply``). A block's box is scaled by the reply's own ``dimensions``. Only a block
of one line is pinned to the found boxes (``gemini_ocr.pinned``); every other line keeps its
block's box (the whole page with no block box) as ``box_source`` "model", and the page gets
``unpinned``. A word under UNSURE_BELOW is unsure on the line its ``start_index`` falls in,
else on the first line holding it; one in no line flags the page UNSURE_UNPLACED. The page's
grade comes from ``average_page_confidence_score``. Any shape it can't read is ``BadReply``.

**The page cache** (#473 Task 4). ``trimmed(page)`` is the part of a page ``page_read`` reads
(the Markdown, dimensions, block types, texts and boxes, the average and the unsure words),
for the cache to keep; ``page_read`` on it gives the same ``PageRead``.

``gemini_ocr`` is imported inside the functions that need it, never at load: it imports this
module for the client, so a load-time import would be a cycle.

Cost: one HTTP call a page; O(markdown + blocks + words x log lines) to read the reply, plus
``pinned``'s O(lines x found) for the one-line blocks.
"""

import base64
import bisect
import json
import logging
import math
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, NoReturn

from kaagaz.ai.openrouter import _ERROR_BODY_BYTES, _LOOPBACK_HOSTS, _code, _read_until, build_opener
from kaagaz.ai.replies import MAX_RESPONSE_BYTES
from kaagaz.reading.mistral_ocr import (
    MISTRAL,
    MISTRAL_OCR_URL,
    MISTRAL_RESOLUTION,
    MISTRAL_THINKING,
    MISTRAL_TIMEOUT_SECONDS,
    MistralModel,
    read_by,
)
from kaagaz.reading.ocr import FIRST_PASS, MAX_BLOCK_CHARS, MAX_BLOCKS_PER_PAGE, OcrLine, PageRead
from kaagaz.reading.page_quality import NO_TEXT, UNPINNED
from kaagaz.reading.rapid_pool import PixelBox

# MistralUnavailable's codes, and the backend names a document waits on (the run's
# <code>_unavailable): the key or the credit, which a person fixes; Mistral not answering.
KEY_PROBLEM = "mistral_key"
OUTAGE = MISTRAL
KEY_STATUSES = frozenset({401, 402, 403})
# Tushar's rule 2 (2 Nov 2026): Mistral's organisations wait, then retry.
KEY_OFF_SECONDS = 300.0
OUTAGE_OFF_SECONDS = 60.0
OUTAGES_IN_A_ROW = 3
# A word Mistral is less sure of than this is an unsure word (its values go to review).
UNSURE_BELOW = 0.80
# The page's grade from Mistral's average word confidence: good from 0.92, fair from 0.80.
GOOD_FROM, FAIR_FROM = 0.92, 0.80
# An unsure word found on no line. Not in page_quality's F1 table (#473 Task 3 report).
UNSURE_UNPLACED = "unsure_unplaced"
# Mistral's block types that are a line kind of their own (gemini_ocr.LINE_KINDS); every
# other type is a paragraph.
_KINDS = {
    "title": "title",
    "list": "list_item",
    "header": "header",
    "footer": "footer",
    "signature": "signature",
}
_PARAGRAPH = "paragraph"
_CORNERS = ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")

# Markdown's marks (page_read).
_PLACEHOLDER = re.compile(r"!\[[^\]]*\]\([^)]*\)|\[tbl-[^\]]*\]\([^)]*\)")
_HEADING = re.compile(r"^#{1,6}\s+")
_BULLET = re.compile(r"^[-*+]\s+")
_EMPHASIS = re.compile(r"\*\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*\*|__(?=[^\s_])([^_]+?)(?<=[^\s_])__")
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_CELL = re.compile(r"(?<!\\)\|")
_SEPARATOR = re.compile(r":?-+:?")

_log = logging.getLogger(__name__)


class MistralUnavailable(Exception):
    """Mistral could not read the page: ``code`` is KEY_PROBLEM or OUTAGE, short and safe to
    log. Never the key or the reply's message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BadReply(Exception):
    """A page ``page_read`` can't read: a missing field, a wrong type, a box outside the page,
    a line or a page over Gemini's limits. The message is "bad_reply" only, never page text."""


class MistralClient:
    """One call to Mistral's ``/v1/ocr`` a page (see the module notes). ``url`` is
    MISTRAL_OCR_URL but in tests (https, or http on a loopback host); ``opener`` defaults to
    ``openrouter.build_opener()`` (no proxy, no redirects); ``now`` is the clock the switch-off
    is kept on. Thread-safe: the switch-off and the count are under a lock."""

    def __init__(
        self,
        key: str,
        *,
        url: str = MISTRAL_OCR_URL,
        opener: urllib.request.OpenerDirector | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS)
        if not (secure and parts.hostname) or parts.username or parts.password or parts.query:
            raise ValueError("the Mistral OCR url must be https (http only on a loopback host)")
        self._key = key
        self._https = parts.scheme == "https"
        self._host_path = url.split("://", 1)[1]
        self._opener = opener or build_opener()
        self.now = now
        self._lock = threading.Lock()
        self._off: tuple[float, str] | None = None  # (until when on ``now``, the code)
        self._outages = 0

    def read(self, png: bytes, *, model: str, seconds: float) -> tuple[dict[str, Any], int]:
        """``(page, pages_processed)`` for one page's PNG read by ``model``, or
        ``MistralUnavailable``. ``seconds``: the page's time left; nothing is sent without any."""
        with self._lock:
            if self._off is not None and self.now() < self._off[0]:
                raise MistralUnavailable(self._off[1])
        timeout = min(MISTRAL_TIMEOUT_SECONDS, seconds)
        if timeout <= 0:
            raise MistralUnavailable(OUTAGE)
        body = {
            "model": model,
            "document": {
                "type": "image_url",
                "image_url": "data:image/png;base64," + base64.b64encode(png).decode(),
            },
            "include_image_base64": False,
            "confidence_scores_granularity": "word",
        }
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
        # The scheme is spelled out, so nothing but https (or http on a loopback host,
        # checked in __init__) can be opened.
        if self._https:
            request = urllib.request.Request(f"https://{self._host_path}", data, headers, method="POST")
        else:
            request = urllib.request.Request(f"http://{self._host_path}", data, headers, method="POST")
        request.add_unredirected_header("Authorization", "Bearer " + self._key)
        ends = time.monotonic() + timeout
        raw: bytes | None = None
        status = 0
        codes: tuple[str | None, str | None] = (None, None)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = _read_until(response, ends, MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status, codes = error.code, _error_codes(error)
        except (OSError, HTTPException):  # the network, a timeout (URLError is an OSError)
            status = 0
        if raw is None:
            self._failed(status, *codes)
        with self._lock:
            self._outages = 0
        return _reply_page(raw)

    def _failed(self, status: int, error_type: str | None, code: str | None) -> NoReturn:
        """Logs the status and the two codes, switches Mistral off when it is time to, and
        raises ``MistralUnavailable``."""
        problem = KEY_PROBLEM if status in KEY_STATUSES else OUTAGE
        level = logging.ERROR if problem == KEY_PROBLEM else logging.WARNING
        _log.log(
            level,
            "mistral ocr failed: status %s, type %s, code %s",
            status,
            error_type,
            code,
            extra={"code": problem},
        )
        with self._lock:
            if problem == KEY_PROBLEM:
                self._off = (self.now() + KEY_OFF_SECONDS, KEY_PROBLEM)
            else:
                self._outages += 1
                if self._outages >= OUTAGES_IN_A_ROW:
                    self._off, self._outages = (self.now() + OUTAGE_OFF_SECONDS, OUTAGE), 0
        raise MistralUnavailable(problem) from None


def _error_codes(error: urllib.error.HTTPError) -> tuple[str | None, str | None]:
    """An error reply's ``type`` and ``code`` (short codes only). The body is read here and
    dropped: its message is never looked at, kept or logged."""
    try:
        raw = error.read(_ERROR_BODY_BYTES)
    except (OSError, HTTPException):
        return None, None
    finally:
        error.close()
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return None, None
    if not isinstance(body, dict):
        return None, None
    return _code(body.get("type")), _code(body.get("code"))


def _reply_page(raw: bytes) -> tuple[dict[str, Any], int]:
    """A 200's ``pages[0]`` (``{}`` when there is none) and ``usage_info.pages_processed``
    (1 when it is not a whole number of 0 or more)."""
    try:
        reply = json.loads(raw) if len(raw) <= MAX_RESPONSE_BYTES else None
    except (ValueError, RecursionError):
        reply = None
    if not isinstance(reply, dict):
        return {}, 1
    pages, usage = reply.get("pages"), reply.get("usage_info")
    page = pages[0] if isinstance(pages, list) and pages and isinstance(pages[0], dict) else {}
    processed = usage.get("pages_processed") if isinstance(usage, dict) else None
    if isinstance(processed, bool) or not isinstance(processed, int) or processed < 0:
        processed = 1
    return page, processed


# The page.


@dataclass(frozen=True, slots=True)
class _Text:
    """One line of the reply before it is pinned: its kind, its block (None with no blocks),
    its block's box in our pixels (None when it has none), whether it is its block's only
    line, and where its raw line lies in the page's Markdown (None when not found)."""

    text: str
    kind: str
    block: int | None
    box: PixelBox | None
    single: bool
    start: int | None
    end: int


def page_read(
    page: dict[str, Any], *, page_no: int, model: MistralModel, found: list[PixelBox], width: int, height: int
) -> PageRead:
    """Mistral's reply page as a ``PageRead`` on an image ``width`` x ``height``, its one-line
    blocks pinned to ``found`` (see the module notes); ``BadReply`` when it can't be read."""
    from kaagaz.reading.gemini_ocr import BOX_SCALE, _Line, pinned  # not at load: a cycle

    try:
        if not isinstance(page, dict):
            raise TypeError
        markdown = page["markdown"]
        if not isinstance(markdown, str):
            raise TypeError
        texts = _texts(page, markdown, width, height)
        unsure, unplaced = _unsure(page, markdown, texts)
        quality = _quality(page)
    except (KeyError, TypeError, ValueError):
        raise BadReply("bad_reply") from None
    pin = {}
    for index, text in enumerate(texts):
        if text.single and text.box is not None:  # a one-line block's line (block is set)
            box = _scaled(text.box, width, height, BOX_SCALE)
            pin[index] = _Line(index, text.kind, text.text, box, unsure[index], False, None, text.block or 0)
    pinned_lines = dict(zip(pin, pinned(pin.values(), found, width, height), strict=True))
    whole = (0.0, 0.0, float(width), float(height))
    result = tuple(
        pinned_lines.get(index)
        or OcrLine(
            text.text,
            *(text.box or whole),
            confidence=None,
            kind=text.kind,
            unsure=unsure[index],
            box_source="model",
            block=text.block,
        )
        for index, text in enumerate(texts)
    )
    issues = []
    if unplaced:
        _log.warning("mistral unsure word on no line", extra={"code": UNSURE_UNPLACED})
        issues.append(UNSURE_UNPLACED)
    if any(line.box_source == "model" for line in result):
        issues.append(UNPINNED)
    if not result:
        issues.append(NO_TEXT)
    return PageRead(
        page_no=page_no,
        lines=result,
        quality=quality,
        handwritten=False,
        scripts=(),
        issues=tuple(issues),
        read_by=read_by(model.id),
        read_thinking=MISTRAL_THINKING,
        read_resolution=MISTRAL_RESOLUTION,
        read_pass=FIRST_PASS,
    )


def trimmed(page: dict[str, Any]) -> dict[str, Any]:
    """The part of a page ``page_read`` accepted that it reads, for the page cache: the
    Markdown, the dimensions, each block's type, content and box, the average and the unsure
    words. ``page_read`` on it gives the same ``PageRead``."""
    kept: dict[str, Any] = {
        "markdown": page["markdown"],
        "dimensions": page.get("dimensions"),
        "blocks": [
            {name: block[name] for name in ("type", "content", *_CORNERS) if name in block}
            for block in page.get("blocks") or []
        ],
    }
    scores = page.get("confidence_scores")
    if scores is not None:
        kept["confidence_scores"] = {
            "average_page_confidence_score": scores.get("average_page_confidence_score"),
            "word_confidence_scores": [
                {name: word.get(name) for name in ("text", "confidence", "start_index")}
                for word in scores.get("word_confidence_scores") or []
                if word["confidence"] < UNSURE_BELOW
            ],
        }
    return kept


def _texts(page: dict[str, Any], markdown: str, width: int, height: int) -> list[_Text]:
    """The page's lines from its blocks in order, or from its Markdown with no blocks."""
    blocks = page.get("blocks") or []
    if not isinstance(blocks, list):
        raise TypeError
    if len(blocks) > MAX_BLOCKS_PER_PAGE:
        raise ValueError
    if not blocks:
        texts = [
            _Text(text, _PARAGRAPH, None, None, False, start, end) for text, start, end in _lines(markdown, 0)
        ]
    else:
        texts, cursor = [], 0
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise TypeError
            content, kind = block["content"], block["type"]
            if not isinstance(content, str) or not isinstance(kind, str):
                raise TypeError
            box = _block_box(block, page, width, height)
            base = markdown.find(content, cursor) if content else -1
            if base >= 0:
                cursor = base + len(content)
            lines = _lines(content, None if base < 0 else base)
            texts.extend(
                _Text(text, _KINDS.get(kind, _PARAGRAPH), index, box, len(lines) == 1, start, end)
                for text, start, end in lines
            )
    if len(texts) > MAX_BLOCKS_PER_PAGE:
        raise ValueError
    return texts


def _lines(content: str, base: int | None) -> list[tuple[str, int | None, int]]:
    """``content``'s lines as (clean text, where its raw line starts in the page's Markdown,
    where it ends); a line left with no text is dropped. ``base``: where ``content`` starts
    there (None: not found)."""
    lines = []
    offset = 0
    for raw in content.split("\n"):
        start = None if base is None else base + offset
        offset += len(raw) + 1
        text = _clean(raw)
        if text:
            lines.append((text, start, (start or 0) + len(raw)))
    return lines


def _clean(raw: str) -> str:
    """One Markdown line as plain text (see the module notes); "" when nothing is left."""
    from kaagaz.reading.gemini_ocr import _CONTROL, _SURROGATE  # not at load: a cycle

    if len(raw) > MAX_BLOCK_CHARS or _SURROGATE.search(raw):
        raise ValueError
    line = _PLACEHOLDER.sub("", raw).strip()
    if line.startswith("|"):
        # shortcut: rows as paragraphs, not OcrTable cells; upgrade if Ask needs cells
        row = line[1:-1] if line.endswith("|") and not line.endswith("\\|") else line[1:]
        cells = [cell.strip() for cell in _CELL.split(row)]
        if all(_SEPARATOR.fullmatch(cell) for cell in cells if cell):
            return ""
        line = "  ".join(cell for cell in cells if cell)
    else:
        line = _BULLET.sub("", _HEADING.sub("", line, count=1), count=1)
    line = _ESCAPE.sub(r"\1", _EMPHASIS.sub(lambda match: match[1] or match[2], line))
    return _CONTROL.sub("", unicodedata.normalize("NFC", line)).strip()


def _block_box(block: dict[str, Any], page: dict[str, Any], width: int, height: int) -> PixelBox | None:
    """A block's box in our pixels, scaled by the reply's own dimensions; None when it has
    none. A box outside the page, or with no area, is a ValueError."""
    corners = [block.get(name) for name in _CORNERS]
    if all(corner is None for corner in corners):
        return None
    dimensions = page["dimensions"]
    if not isinstance(dimensions, dict):
        raise TypeError
    their_width, their_height = dimensions["width"], dimensions["height"]
    if (
        not all(map(_is_finite, [*corners, their_width, their_height]))
        or their_width <= 0
        or their_height <= 0
    ):
        raise ValueError
    x0, y0, x1, y1 = corners
    box = (
        x0 / their_width * width,
        y0 / their_height * height,
        x1 / their_width * width,
        y1 / their_height * height,
    )
    if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
        raise ValueError
    return box


def _scaled(box: PixelBox, width: int, height: int, scale: float) -> tuple[float, float, float, float]:
    """A box in our pixels (x0, y0, x1, y1) as the model boxes ``pinned`` takes: (y0, x0, y1,
    x1) in 0..``scale`` of the page."""
    x0, y0, x1, y1 = box
    return y0 / height * scale, x0 / width * scale, y1 / height * scale, x1 / width * scale


def _unsure(page: dict[str, Any], markdown: str, texts: list[_Text]) -> tuple[list[tuple[str, ...]], bool]:
    """Each line's unsure words, and whether one was found on no line (see the module notes).
    O(words x log lines + words x lines for those whose start doesn't place them)."""
    unsure: list[list[str]] = [[] for _ in texts]
    words = _scores(page).get("word_confidence_scores") or []
    if not isinstance(words, list):
        raise TypeError
    ranged = [(text.start, text.end, index) for index, text in enumerate(texts) if text.start is not None]
    starts = [start for start, _end, _index in ranged]
    unplaced = False
    for word in words:
        if not isinstance(word, dict):
            raise TypeError
        raw, confidence, start = word["text"], word["confidence"], word.get("start_index")
        if not isinstance(raw, str) or not _is_finite(confidence):
            raise TypeError
        if start is not None and (isinstance(start, bool) or not isinstance(start, int) or start < 0):
            raise ValueError
        if confidence >= UNSURE_BELOW:
            continue
        wanted = _clean(raw)
        if not wanted:
            continue
        index = None
        if start is not None and _starts_with(markdown, start, raw):
            at = bisect.bisect_right(starts, start) - 1
            if at >= 0 and start < ranged[at][1] and wanted in texts[ranged[at][2]].text:
                index = ranged[at][2]
        if index is None:
            index = next((i for i, text in enumerate(texts) if wanted in text.text), None)
        if index is None:
            unplaced = True
        elif wanted not in unsure[index]:
            unsure[index].append(wanted)
    return [tuple(line) for line in unsure], unplaced


def _starts_with(markdown: str, start: int, word: str) -> bool:
    """``markdown[start:].startswith(word)``, NFC on both sides (a window long enough for a
    decomposed form of the word)."""
    window = markdown[start : start + 2 * len(word) + 8]
    return unicodedata.normalize("NFC", window).startswith(unicodedata.normalize("NFC", word))


def _quality(page: dict[str, Any]) -> str | None:
    """good, fair or poor from Mistral's average word confidence; None when it gives none."""
    average = _scores(page).get("average_page_confidence_score")
    if average is None:
        return None
    if not _is_finite(average):
        raise ValueError
    return "good" if average >= GOOD_FROM else "fair" if average >= FAIR_FROM else "poor"


def _scores(page: dict[str, Any]) -> dict[str, Any]:
    scores = page.get("confidence_scores")
    if scores is None:
        return {}
    if not isinstance(scores, dict):
        raise TypeError
    return scores


def _is_finite(number: object) -> bool:
    """A whole number or a finite float; a boolean is neither."""
    if isinstance(number, bool):
        return False
    return isinstance(number, int) or (isinstance(number, float) and math.isfinite(number))
