"""The Mistral OCR page reader (#473, Tushar 2 Nov 2026): ``MistralClient`` posts one page to
Mistral's ``/v1/ocr`` (``read``) and gives the Gemini reader's engine the page as a ``PageRead``
(``read_page``, with the page cache and the ledger); ``page_read`` reads a reply page. The model
list and names are in ``kaagaz.reading.mistral_ocr``, which the API imports.

**When Mistral can't read,** ``read`` raises ``MistralUnavailable(code)`` and ``read_page``
``BackendUnavailable(code)``, so the document waits:

* ``mistral_key`` (401, 402, 403, 404: the key, the credit or a withdrawn model, each a person's
  fix): Mistral is switched off on this client for KEY_OFF_SECONDS.
* ``mistral`` (any other status, the network, a timeout, or a 200 whose body was lost, which is
  billed all the same): after OUTAGES_IN_A_ROW in a row it is switched off for
  OUTAGE_OFF_SECONDS. A 200 resets the count.

While it is switched off nothing is sent: ``read`` and ``ready`` (asked before a document's
pages are drawn) raise at once, and ``off_seconds`` says how long is left, which the document
waits (review, 2 Nov 2026).

**A page refused,** not Mistral: no lines, the issue ``reader_refused``, never cached, no
outage counted, logged with its ids and the code ``refused_request`` (400, 413, 422; not
billed), ``bad_reply`` (a 200 ``page_read`` can't read; billed) or ``mistral_no_time`` (the
page's time was spent; nothing sent). A cached page this code no longer reads is logged
``cache_stale`` and read again.

**Never logged,** nor in an exception (``raise ... from None``): the key, the page's text, and
the error reply's message, which can echo it. Only the status and the reply's ``type`` and
``code`` (``openrouter._code``).

**Imports.** ``gemini_ocr`` is imported inside the functions that need it, never at load, and
never imports this module (it takes the client as its ``MistralReader``, built in ``wiring``),
so the two never import each other (tests/test_import_graph.py counts the imports inside
functions too).
"""

import base64
import bisect
import dataclasses
import itertools
import json
import logging
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

from PIL import Image

from kaagaz.ai.openrouter import _ERROR_BODY_BYTES, _LOOPBACK_HOSTS, _code, _read_until, build_opener
from kaagaz.ai.replies import MAX_RESPONSE_BYTES
from kaagaz.ai.usage import record_call, record_page
from kaagaz.db.page_reads import PageKey
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.reading.mistral_ocr import (
    MISTRAL,
    MISTRAL_ENDPOINT,
    MISTRAL_KEY,
    MISTRAL_OCR_MODELS,
    MISTRAL_RESOLUTION,
    MISTRAL_THINKING,
    MISTRAL_TIMEOUT_SECONDS,
    MistralModel,
    read_by,
)
from kaagaz.reading.ocr import (
    AGAIN_PASS,
    FIRST_PASS,
    MAX_BLOCK_CHARS,
    MAX_BLOCKS_PER_PAGE,
    DocumentPages,
    OcrLine,
    PageRead,
)
from kaagaz.reading.page_quality import NO_TEXT, READER_REFUSED, UNPINNED
from kaagaz.reading.rapid_pool import PixelBox

KEY_STATUSES = frozenset({401, 402, 403, 404})
# This page or request refused (lead, 2 Nov 2026): a BadReply, not an outage.
REFUSED_STATUSES = frozenset({400, 413, 422})
# A refused page's log codes (the module notes; review, 2 Nov 2026), and a stale cached page's.
REFUSED_REQUEST = "refused_request"
BAD_REPLY = "bad_reply"
NO_TIME = "mistral_no_time"
CACHE_STALE = "cache_stale"
# Tushar's rule 2 (2 Nov 2026): Mistral's organisations wait, then retry.
KEY_OFF_SECONDS = 300.0
OUTAGE_OFF_SECONDS = 60.0
OUTAGES_IN_A_ROW = 3
# The page cache's prompt_version for a Mistral read (#473 Task 4): change it with any change to
# what ``trimmed`` keeps or how ``page_read`` reads it, so an older cached page is read again.
CACHE_VERSION = "mistral-1"
# A word Mistral is less sure of than this is an unsure word (its values go to review).
UNSURE_BELOW = 0.80
# The page's grade from Mistral's average word confidence: good from 0.92, fair from 0.80.
GOOD_FROM, FAIR_FROM = 0.92, 0.80
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
    """Mistral could not read the page: ``code`` is MISTRAL_KEY or MISTRAL, short and safe to
    log, never the key or the reply's message. ``billed``: Mistral had answered 200 first, so
    the page is billed though nothing was read (review, 2 Nov 2026)."""

    def __init__(self, code: str, *, billed: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.billed = billed


class BadReply(Exception):
    """A page refused (the module notes): its message is the log code only, never page text."""


class MistralClient:
    """One call to Mistral's ``/v1/ocr`` a page (see the module notes). ``url`` is
    MISTRAL_ENDPOINT but in tests (https, or http on a loopback host); ``opener`` defaults to
    ``openrouter.build_opener()`` (no proxy, no redirects); ``now`` is the clock the switch-off
    is kept on. Thread-safe: the switch-off and the count are under a lock."""

    def __init__(
        self,
        key: str,
        *,
        url: str = MISTRAL_ENDPOINT,
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

    def off_seconds(self) -> float:
        """How long Mistral stays switched off on this client; 0.0 while it is on. O(1)."""
        off = self._switched_off()
        return 0.0 if off is None else off[1]

    def ready(self) -> None:
        """``BackendUnavailable`` at once while Mistral is switched off here, its ``seconds``
        the time left (review, 2 Nov 2026: a document waits before its pages are drawn, not
        once a minute after drawing them all). Nothing is sent. O(1)."""
        off = self._switched_off()
        if off is not None:
            raise _waits(*off)

    def read(self, png: bytes, *, model: str, seconds: float) -> tuple[dict[str, Any], int]:
        """``(page, pages_processed)`` for one page's PNG read by ``model`` on every HTTP 200,
        because Mistral bills a 200: ``page`` its ``pages[0]`` (``{}`` when the body is not one,
        which ``page_read`` then refuses), ``pages_processed`` its ``usage_info`` (1 when
        missing). ``MistralUnavailable`` when Mistral can't read; ``BadReply`` for a refused
        request, or with no time left (``seconds``, the page's: then nothing is sent). Never
        tables as placeholders (no ``table_format``); never a retry: waiting is the retry.
        At most MISTRAL_TIMEOUT_SECONDS."""
        off = self._switched_off()
        if off is not None:
            raise MistralUnavailable(off[0])
        timeout = min(MISTRAL_TIMEOUT_SECONDS, seconds)
        if timeout <= 0:  # review, 2 Nov 2026: the page's time is spent, Mistral is not down
            raise BadReply(NO_TIME)
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
        answered = False  # a 200 came: billed, whatever happens to its body (review, 2 Nov 2026)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                answered = True
                raw = _read_until(response, ends, MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status, codes = error.code, _error_codes(error)
        except (OSError, HTTPException):  # the network, a timeout (URLError is an OSError)
            status = 0
        if raw is None:
            self._failed(status, *codes, billed=answered)
        with self._lock:
            self._outages = 0
        return _reply_page(raw)

    def read_page(
        self,
        image: Image.Image,
        *,
        page_no: int,
        pages: DocumentPages,
        sha: str,
        found: Callable[[], list[PixelBox]],
        seconds: float,
    ) -> PageRead:
        """Page ``page_no`` (its pixels' hash ``sha``) of ``pages``'s document read by its Mistral
        model: ``found()`` the boxes to pin to, ``seconds`` the page's time left;
        ``BackendUnavailable`` when Mistral can't read (the module notes). The page cache under
        ``mistral/<id>`` and CACHE_VERSION unless it is to be read again (a hit is billed as a
        cached call at 0, with no page). Each 200 is billed at once, ``record_call`` at the
        model's price a page processed and ``record_page``, at MISTRAL_THINKING and
        MISTRAL_RESOLUTION; a page with text is cached. One call at most; O(the reply) besides."""
        from kaagaz.reading.gemini_ocr import png_of  # not at load (module notes)

        model = MISTRAL_OCR_MODELS[pages.mistral_model]
        name = read_by(model.id)
        ids = {"customer_id": pages.customer_id, "document_id": pages.document_id, "page_no": page_no}
        key = PageKey(pages.customer_id, pages.document_id, page_no, sha, name, CACHE_VERSION)
        again = page_no in pages.read_again
        hit = None if again else pages.cache.get(key)
        sent = {"thinking": MISTRAL_THINKING, "resolution": MISTRAL_RESOLUTION}

        def as_read(page: dict[str, Any]) -> PageRead:
            return page_read(
                page, page_no=page_no, model=model, found=found(), width=image.width, height=image.height
            )

        def billed(processed: int) -> None:
            record_call(name, MISTRAL, 0, 0, 0, processed * model.usd_per_page, cached=False, **sent)
            record_page(name, **sent)  # a 200 is billed, whatever it holds

        if hit is not None:
            try:
                cached = as_read(hit[0])
            except BadReply:  # one this code no longer reads is asked again
                _log.warning("cached page unreadable, read again", extra={**ids, "code": CACHE_STALE})
            else:
                record_call(name, MISTRAL, 0, 0, 0, 0.0, cached=True, **sent)  # SP1: costs 0
                return dataclasses.replace(cached, cached=True)
        if seconds <= 0:  # the page's time is spent: refused, never an outage (module notes)
            return _refused(page_no, name, ids, NO_TIME)
        try:
            reply, processed = self.read(png_of(image), model=model.id, seconds=seconds)
        except MistralUnavailable as error:
            if error.billed:
                billed(1)
            raise _waits(error.code, self.off_seconds()) from None
        except BadReply as error:  # refused before a 200: not billed
            return _refused(page_no, name, ids, str(error))
        billed(processed)
        try:
            result = as_read(reply)
        except BadReply as error:
            return _refused(page_no, name, ids, str(error))
        if result.lines:  # a page with no text is not cached
            pages.cache.put(key, trimmed(reply), 0, 0)
        return dataclasses.replace(result, read_pass=AGAIN_PASS) if again else result

    def _switched_off(self) -> tuple[str, float] | None:
        """(the code, the seconds left) while Mistral is switched off here, else None. O(1)."""
        with self._lock:
            if self._off is None:
                return None
            left = self._off[0] - self.now()
            return (self._off[1], left) if left > 0 else None

    def _failed(self, status: int, error_type: str | None, code: str | None, *, billed: bool) -> NoReturn:
        """Logs the status and the two codes, then raises ``BadReply`` for a refused request,
        else switches Mistral off when it is time to and raises ``MistralUnavailable``."""
        refused = status in REFUSED_STATUSES
        problem = MISTRAL_KEY if status in KEY_STATUSES else MISTRAL
        level = logging.ERROR if problem == MISTRAL_KEY else logging.WARNING
        _log.log(
            level,
            "mistral ocr failed: status %s, type %s, code %s",
            status,
            error_type,
            code,
            extra={"code": REFUSED_REQUEST if refused else problem},
        )
        if refused:
            raise BadReply(REFUSED_REQUEST) from None
        with self._lock:
            if problem == MISTRAL_KEY:
                self._off = (self.now() + KEY_OFF_SECONDS, MISTRAL_KEY)
            else:
                self._outages += 1
                if self._outages >= OUTAGES_IN_A_ROW:
                    self._off, self._outages = (self.now() + OUTAGE_OFF_SECONDS, MISTRAL), 0
        raise MistralUnavailable(problem, billed=billed) from None


def _waits(code: str, seconds: float) -> BackendUnavailable:
    """``BackendUnavailable(code)`` whose ``seconds`` is how long Mistral stays switched off
    here (0.0: it is not), for the worker to wait at least that long."""
    error = BackendUnavailable(code)
    error.seconds = seconds
    return error


def _refused(page_no: int, name: str, ids: dict[str, Any], code: str) -> PageRead:
    """#473 Task 4: a page Mistral (``name``) would not or could not read: no lines,
    ``reader_refused`` (its values can't be shown as read), never cached. Logs its ids and
    ``code`` only."""
    _log.warning("page refused by the reader", extra={**ids, "code": code})
    return PageRead(
        page_no=page_no,
        lines=(),
        quality=None,
        handwritten=False,
        scripts=(),
        issues=(READER_REFUSED,),
        read_by=name,
        read_thinking=MISTRAL_THINKING,
        read_resolution=MISTRAL_RESOLUTION,
        read_pass=FIRST_PASS,
    )


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
    """Mistral's reply page as a ``PageRead`` on an image ``width`` x ``height``; ``BadReply``
    when its shape is not the one read here (``_valid``) or a line or the page is over
    Gemini's limits (a lone surrogate, a line over MAX_BLOCK_CHARS, more than
    MAX_BLOCKS_PER_PAGE lines or blocks). Any other error is ours and goes up.

    Lines come from the reply's blocks in order, or from its Markdown when it has none
    (Mistral OCR 3), cleaned (``_clean``). A block's box is scaled by the reply's own
    ``dimensions`` and clamped to the page (``_block_box``). A block of one line is pinned to
    ``found`` (``gemini_ocr.pinned``); a block of more takes the found rows inside it, one a
    line in order, when there are as many rows as lines, evenly spaced (``_rows``, ``_even``,
    PRD 9 F-11). Every other line keeps its block's box (the whole page with none) as
    ``box_source`` "model", and the page gets ``unpinned``. The unsure words are ``_unsure``'s;
    the grade comes from ``average_page_confidence_score``. O(markdown + blocks x found log found
    + words x log lines), plus ``pinned``'s O(lines x found)."""
    from kaagaz.reading.gemini_ocr import BOX_SCALE, _Line, pinned  # not at load (module notes)

    if not _valid(page):
        raise BadReply(BAD_REPLY) from None
    markdown = page["markdown"]
    texts = _texts(page, markdown, width, height)
    unsure = _unsure(page, markdown, texts)
    quality = _quality(page)
    pin = {}
    for index, text in enumerate(texts):
        if text.single and text.box is not None:  # a one-line block's line (block is set)
            box = _scaled(text.box, width, height, BOX_SCALE)
            pin[index] = _Line(index, text.kind, text.text, box, unsure[index], False, None, text.block or 0)
    pinned_lines = dict(zip(pin, pinned(pin.values(), found, width, height), strict=True))
    blocks: dict[int | None, tuple[PixelBox, list[int]]] = {}
    for index, text in enumerate(texts):
        if not text.single and text.box is not None:  # a line of a block of more (block is set)
            blocks.setdefault(text.block, (text.box, []))[1].append(index)
    rows: dict[int, PixelBox] = {}
    for block_box, indexes in blocks.values():
        found_rows = _rows(block_box, found)
        if len(found_rows) == len(indexes) and _even(block_box, found_rows):  # PRD 9 F-11: never a guess
            rows.update(zip(indexes, found_rows, strict=True))
    whole = (0.0, 0.0, float(width), float(height))
    result = tuple(
        pinned_lines.get(index)
        or OcrLine(
            text.text,
            *rows.get(index, text.box or whole),
            confidence=None,
            kind=text.kind,
            unsure=unsure[index],
            box_source="found" if index in rows else "model",
            block=text.block,
        )
        for index, text in enumerate(texts)
    )
    issues = []
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


def _valid(page: object) -> bool:
    """Review (2 Nov 2026): whether a reply page has the shape ``page_read`` reads, checked
    before any of it is read: ``markdown`` text; ``blocks`` none or a list of objects with a
    text ``type`` and ``content`` and the four corners all missing or all numbers, and then
    ``dimensions`` with a ``width`` and ``height`` above 0; ``confidence_scores`` none or an
    object whose average is none or a number and whose ``word_confidence_scores`` are none or
    a list of objects with a text ``text``, a number ``confidence`` and a ``start_index`` none
    or a whole number of 0 or more. O(blocks + words)."""
    from kaagaz.reading.gemini_ocr import _is_finite  # not at load (module notes)

    if not isinstance(page, dict) or not isinstance(page.get("markdown"), str):
        return False
    blocks = page.get("blocks")
    if blocks is not None and not isinstance(blocks, list):
        return False
    boxed = False
    for block in blocks or []:
        if not (isinstance(block, dict) and isinstance(block.get("type"), str)):
            return False
        if not isinstance(block.get("content"), str):
            return False
        corners = [block.get(name) for name in _CORNERS]
        if any(corner is not None for corner in corners):
            if not all(map(_is_finite, corners)):
                return False
            boxed = True
    if boxed:
        dimensions = page.get("dimensions")
        if not isinstance(dimensions, dict):
            return False
        sides = (dimensions.get("width", 0), dimensions.get("height", 0))
        if not all(_is_finite(side) and side > 0 for side in sides):
            return False
    scores = page.get("confidence_scores")
    if scores is None:
        return True
    if not isinstance(scores, dict):
        return False
    average, words = scores.get("average_page_confidence_score"), scores.get("word_confidence_scores")
    if (average is not None and not _is_finite(average)) or (
        words is not None and not isinstance(words, list)
    ):
        return False
    for word in words or []:
        if not (
            isinstance(word, dict)
            and isinstance(word.get("text"), str)
            and _is_finite(word.get("confidence"))
        ):
            return False
        start = word.get("start_index")
        if start is not None and (isinstance(start, bool) or not isinstance(start, int) or start < 0):
            return False
    return True


def _texts(page: dict[str, Any], markdown: str, width: int, height: int) -> list[_Text]:
    """The page's lines from its blocks in order, or from its Markdown with no blocks."""
    blocks = page.get("blocks") or []
    if len(blocks) > MAX_BLOCKS_PER_PAGE:
        raise BadReply(BAD_REPLY) from None
    if not blocks:
        texts = [
            _Text(text, _PARAGRAPH, None, None, False, start, end) for text, start, end in _lines(markdown, 0)
        ]
    else:
        texts, cursor = [], 0
        for index, block in enumerate(blocks):
            content, kind = block["content"], block["type"]
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
        raise BadReply(BAD_REPLY) from None
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
    """One Markdown line as plain text, "" when nothing is left: only Markdown's own marks
    taken off (``#`` headings, ``**``/``__`` emphasis, a ``-``/``*``/``+`` bullet followed by a
    space, backslash escapes; "1." and "-500" are kept); image lines and ``[tbl-...]``
    placeholders dropped; a table row as one line, its cells joined by two spaces (its
    separator row ""); NFC, with Gemini's controls dropped. ``BadReply`` for a lone surrogate
    or a line over MAX_BLOCK_CHARS."""
    from kaagaz.reading.gemini_ocr import _CONTROL, _SURROGATE  # not at load (module notes)

    if len(raw) > MAX_BLOCK_CHARS or _SURROGATE.search(raw):
        raise BadReply(BAD_REPLY) from None
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
    """A block's box in our pixels, scaled by the reply's own dimensions and clamped to the
    page; None when it has none, or no area is left on the page (review, 2 Nov 2026: such a
    line is unpinned, never a refused page)."""
    corners = [block.get(name) for name in _CORNERS]
    if all(corner is None for corner in corners):
        return None
    their_width, their_height = page["dimensions"]["width"], page["dimensions"]["height"]
    x0, y0, x1, y1 = corners
    left, top = max(0.0, x0 / their_width * width), max(0.0, y0 / their_height * height)
    right = min(float(width), x1 / their_width * width)
    bottom = min(float(height), y1 / their_height * height)
    return (left, top, right, bottom) if left < right and top < bottom else None


def _rows(block: PixelBox, found: list[PixelBox]) -> list[PixelBox]:
    """PRD 9 F-11: the found boxes whose centre lies in ``block``, as rows from the top: a box
    whose middle height is inside the row above joins it (a table row's cells). Each row is
    the union of its boxes, clipped to the block. O(found log found)."""
    x0, y0, x1, y1 = block
    inside = sorted(
        (box for box in found if x0 <= (box[0] + box[2]) / 2 <= x1 and y0 <= (box[1] + box[3]) / 2 <= y1),
        key=lambda box: (box[1] + box[3]) / 2,
    )
    rows: list[PixelBox] = []
    for box in inside:
        row = rows[-1] if rows else None
        if row is not None and row[1] <= (box[1] + box[3]) / 2 <= row[3]:
            rows[-1] = (min(row[0], box[0]), min(row[1], box[1]), max(row[2], box[2]), max(row[3], box[3]))
        else:
            rows.append(box)
    return [(max(r[0], x0), max(r[1], y0), min(r[2], x1), min(r[3], y1)) for r in rows]


def _even(block: PixelBox, rows: list[PixelBox]) -> bool:
    """PRD 9 F-11, CLAUDE.md rule 6: whether ``rows`` (two or more, ``_rows``) lie evenly over
    ``block``, so that one row a line is no guess: each row's top within half to one and a half
    of the middle step from the row above's (a wrapped cell or a missed row breaks it), no row
    over one and a half times the middle height (a stamp, two rows as one), and neither edge of
    the block further from its row than three quarters of a step (a row missed there). O(rows log rows)."""

    def middle(values: list[float]) -> float:
        return sorted(values)[(len(values) - 1) // 2]

    steps = [below[1] - above[1] for above, below in itertools.pairwise(rows)]
    step, height = middle(steps), middle([row[3] - row[1] for row in rows])
    return (
        step > 0
        and all(step / 2 <= each <= step * 1.5 for each in steps)
        and all(row[3] - row[1] <= height * 1.5 for row in rows)
        and rows[0][1] - block[1] <= step * 0.75
        and block[3] - rows[-1][3] <= step * 0.75
    )


def _scaled(box: PixelBox, width: int, height: int, scale: float) -> tuple[float, float, float, float]:
    """A box in our pixels (x0, y0, x1, y1) as the model boxes ``pinned`` takes: (y0, x0, y1,
    x1) in 0..``scale`` of the page."""
    x0, y0, x1, y1 = box
    return y0 / height * scale, x0 / width * scale, y1 / height * scale, x1 / width * scale


def _unsure(page: dict[str, Any], markdown: str, texts: list[_Text]) -> list[tuple[str, ...]]:
    """Each line's unsure words: a word under UNSURE_BELOW is unsure on the line its
    ``start_index`` falls in, else on every line holding it (review, 2 Nov 2026). One in no
    line is never dropped (lead, 2 Nov 2026): the line nearest its ``start_index`` (the first
    line with none) gets its whole text as an unsure entry, so the whole line goes to review.
    O(words x log lines + words x lines for those whose start doesn't place them)."""
    unsure: list[list[str]] = [[] for _ in texts]
    words = _scores(page).get("word_confidence_scores") or []
    ranged = [(text.start, text.end, index) for index, text in enumerate(texts) if text.start is not None]
    starts = [start for start, _end, _index in ranged]
    for word in words:
        raw, confidence, start = word["text"], word["confidence"], word.get("start_index")
        if confidence >= UNSURE_BELOW:
            continue
        wanted = _clean(raw)
        lines: list[int] = []
        if wanted and start is not None and _starts_with(markdown, start, raw):
            at = bisect.bisect_right(starts, start) - 1
            if at >= 0 and start < ranged[at][1] and wanted in texts[ranged[at][2]].text:
                lines = [ranged[at][2]]
        if not lines and wanted:
            lines = [index for index, text in enumerate(texts) if wanted in text.text]
        if not lines:
            if not texts:
                continue  # no line at all: the page is no_text
            nearest = _nearest(ranged, start)
            lines, wanted = [nearest], texts[nearest].text
        for index in lines:
            if wanted not in unsure[index]:
                unsure[index].append(wanted)
    return [tuple(line) for line in unsure]


def _nearest(ranged: list[tuple[int, int, int]], start: int | None) -> int:
    """The line whose raw Markdown range [start, end) is nearest ``start`` (the earlier on a
    tie); the first line when ``start`` is None or no line was found in the Markdown."""
    if start is None or not ranged:
        return 0

    def distance(line: tuple[int, int, int]) -> int:
        return line[0] - start if start < line[0] else max(0, start - line[1] + 1)

    return min(ranged, key=distance)[2]


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
    return "good" if average >= GOOD_FROM else "fair" if average >= FAIR_FROM else "poor"


def _scores(page: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, Any] = page.get("confidence_scores") or {}
    return scores
