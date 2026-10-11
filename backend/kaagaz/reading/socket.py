"""The reader plug socket (issue #21).

One interface every reader implements, and one place that picks the reader
for a file type. Application code only ever calls ``ReaderSocket.read``.

The readers are passed in as a plain mapping from file type to reader, built
where the application is wired together. Adding a reader means writing its
module and adding one entry to that mapping; nothing else changes. There is
no self-registration or plug-in discovery on purpose.

A file type with no reader raises NoReader: the document is stored and
marked "needs a reader" (scans and photos when no OCR engine is set up).

Some types also have a second reader: an independent reading of the same
file, kept next to the first so values the two agree on can be labelled
verified and the rest sent for review. ``read_second`` returns None for a
type without one. ``reader_id`` names the reader and its version, stored with
its output (``reader_outputs``), so a later change of reader can be traced.

Every span from either reader is checked before it leaves the socket: text
present, a real cell or a box inside the page, a confidence that is None or
between 0 and 1, and its layout fields (PRD 2 P4): a known kind and origin; a
cell has no kind and no table; a table cell has all its table fields, and
nothing else has any. Its word marks (PRD 5 F3): a known quality and box
source, set only on a span read by OCR.

``read`` takes an optional ``on_page`` callback. A reader that reads page by
page (PDFs, scans and photos) calls it after each page, so a long reading
can say "still working" (the worker's heartbeat renews the job's lease), with
how that page was read: its ``RouteDecision`` (PRD 2 R1, the route stored for
it, so a stored route is always the reader's), or None for a page it did not
read. It is keyword-only and optional, so a reader with nothing to beat for
between pages accepts it and ignores it.

Pages in parallel (PRD 5 G8), with the ``Reader`` protocol unchanged: the PDF
and image readers may be given a ``PagePool``, OCR_PAGE_CONCURRENCY threads.
Through ``PagesInOrder``, everything about a page except the engine's
``read_page`` stays on the reader's own thread (pdfplumber, pypdfium2, drawing
the page, the blank check, any second reading), only ``read_page`` goes to the
pool, and each page is finished, ``on_page`` included, strictly in page order,
so a reader's spans and its ``on_page`` calls are exactly as without a pool. At
most the pool's size of pages are being read at once; with no pool, each page
is read and finished on the spot, as before. A page thread runs in a copy of the
reader's context (``contextvars``), so its model calls are billed to the usage scope the
reader is in (model choice and AI spend, SP1).

PROVISIONAL until the reader contract (#3) is written. PROVISIONAL (plan
7.4): ``on_page`` is part of that contract, for Vrushit and PRD 2 to agree.
"""

import contextvars
import math
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self

from PIL import Image

from kaagaz.reading.ocr import PageEngine, PageRead
from kaagaz.reading.route import RouteDecision
from kaagaz.reading.spans import (
    BLOCK_KINDS,
    BOX_SOURCES,
    ORIGINS,
    QUALITIES,
    TABLE_ID,
    BoxLocation,
    CellLocation,
    ReadError,
    Span,
)

# After each page: how it was read (its route and why), or None when it was not read.
OnPage = Callable[[RouteDecision | None], None]


class Reader(Protocol):
    @property
    def name(self) -> str: ...  # read-only: the image reader's is its engine's

    @property
    def version(self) -> str: ...  # read-only: asked for after reading

    def read(self, data: bytes, *, on_page: OnPage | None = None) -> Iterable[Span]: ...


class NoReader(Exception):
    """No reader exists for this file type yet ("needs a reader")."""

    def __init__(self, detected_type: str) -> None:
        super().__init__(detected_type)
        self.detected_type = detected_type


class ReaderSocket:
    def __init__(
        self, readers: Mapping[str, Reader], second_readers: Mapping[str, Reader] | None = None
    ) -> None:
        # Copies: the set of readers is fixed once wired.
        self._readers = dict(readers)
        self._second_readers = dict(second_readers or {})

    def read(self, detected_type: str, data: bytes, *, on_page: OnPage | None = None) -> list[Span]:
        """All spans of the document, or ReadError / NoReader.

        The socket checks every span has a real location and non-empty text,
        so no reader can hand back a value that cannot be pointed at. A
        reader that breaks this is a bug in that reader; it fails with
        ``invalid_reader_output`` for good rather than being retried.
        ``on_page`` is handed to the reader as it is (see the module notes).
        """
        reader = self._readers.get(detected_type)  # O(1) dispatch
        if reader is None:
            raise NoReader(detected_type)
        return _checked(reader.read(data, on_page=on_page))

    def read_second(self, detected_type: str, data: bytes) -> list[Span] | None:
        """The second reader's spans, checked the same way, or None when the
        type has none. Its ReadError is raised like the first reader's."""
        reader = self._second_readers.get(detected_type)
        if reader is None:
            return None
        return _checked(reader.read(data))

    def reader_id(self, detected_type: str, second: bool = False) -> tuple[str, str]:
        """(name, version) of the first or second reader for this type, or NoReader."""
        reader = (self._second_readers if second else self._readers).get(detected_type)
        if reader is None:
            raise NoReader(detected_type)
        return reader.name, reader.version


@dataclass(frozen=True, slots=True)
class PagePool:
    """The threads a document's ocr pages are read on at the same time (PRD 5 G8):
    ``size`` is OCR_PAGE_CONCURRENCY, the most pages read at once. One document is read
    at a time, so the PDF and image readers share one."""

    executor: Executor
    size: int

    def __post_init__(self) -> None:
        if self.size < 1:
            raise ValueError("a page pool needs at least 1 thread")

    @classmethod
    def threads(cls, size: int) -> "PagePool":
        """A pool of ``size`` threads, started as pages need them."""
        return cls(ThreadPoolExecutor(max_workers=size, thread_name_prefix="kaagaz-page"), size)


@dataclass(slots=True)
class _Page:
    """A page in the window: being read (``future``, its ``image``), or ready (no future)."""

    future: "Future[PageRead] | None"
    image: Image.Image | None
    on_read: Callable[[PageRead], None] | None
    on_ready: Callable[[], None] | None
    release: Callable[[], None]


def _nothing() -> None:
    """Nothing to release."""


def _read_and_close(engine: PageEngine, image: Image.Image, page_no: int) -> PageRead:
    try:
        return engine.read_page(image, page_no=page_no)
    finally:
        image.close()


class PagesInOrder:
    """One read's pages, finished strictly in the order they were added (see the module
    notes). ``engine`` reads them, on ``pool``'s threads, or on the spot with no pool.

    Use it as a context manager and call ``drain`` at the end of the read: when the
    block raises, pages not yet started are cancelled, the pages already being read are
    waited for (each is bounded by the reader's own deadline, OCR_TIMEOUT_SECONDS), and
    every page left is dropped, unfinished, its picture closed. Nothing of a dropped page
    is passed on: not its read, not its error. Waiting keeps a dropped page's reading
    inside this document's read, so it can never land in the next document's (one
    worker reads another customer's document next). The first failure in page order is
    the one raised. O(1) per page beyond the engine's time; at most ``pool.size``
    pictures waiting or being read at once.
    """

    def __init__(self, engine: PageEngine | None, pool: PagePool | None) -> None:
        self._engine = engine
        self._pool = pool
        self._window: deque[_Page] = deque()
        self._reading = 0  # pages in the window still with a future

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        if error is not None:
            self._drop()

    def read(
        self,
        image: Image.Image,
        page_no: int,
        on_read: Callable[[PageRead], None],
        *,
        release: Callable[[], None] = _nothing,
    ) -> None:
        """Read ``image`` as page ``page_no`` with the engine, then call ``on_read`` with
        what was read, on this thread, after every page added before it. ``image`` is
        the window's: closed once read. ``release`` is called on this thread after
        ``on_read``, or when the page is dropped. Waits for the oldest page while the
        pool's size of pages are being read."""
        engine = self._engine
        if engine is None:
            raise ValueError("a read with no engine has no page to read")
        if self._pool is None:
            try:
                on_read(_read_and_close(engine, image, page_no))
            finally:
                release()
            return
        added = False
        try:
            while self._reading >= self._pool.size:
                self._finish_oldest()
            # A copy of this thread's context: the page thread bills its calls to the same
            # usage scope (kaagaz.ai.usage, SP1).
            context = contextvars.copy_context()
            future = self._pool.executor.submit(context.run, _read_and_close, engine, image, page_no)
            added = True
        finally:
            if not added:  # an older page failed first: this one is never read
                image.close()
                release()
        self._reading += 1
        self._window.append(_Page(future, image, on_read, None, release))
        self._finish_ready()

    def ready(self, on_ready: Callable[[], None]) -> None:
        """A page with nothing to read (a text page, a blank one): ``on_ready`` on this
        thread, after every page added before it."""
        if not self._window:
            on_ready()
            return
        self._window.append(_Page(None, None, None, on_ready, _nothing))
        self._finish_ready()

    def drain(self) -> None:
        """Finish every page left, in order, waiting for each."""
        while self._window:
            self._finish_oldest()

    def _finish_ready(self) -> None:
        """Finish the oldest pages while they are ready, so ``on_page`` (the heartbeat)
        moves as soon as it can."""
        while self._window and (self._window[0].future is None or self._window[0].future.done()):
            self._finish_oldest()

    def _finish_oldest(self) -> None:
        page = self._window.popleft()
        try:
            if page.future is not None:
                self._reading -= 1
                read = page.future.result()  # the page's own error, raised in page order
                if page.on_read is not None:
                    page.on_read(read)
            elif page.on_ready is not None:
                page.on_ready()
        finally:
            page.release()

    def _drop(self) -> None:
        """Every page left, unfinished: a page not yet started is cancelled; a page being
        read is waited for, and what it read or raised is dropped, never passed on. Each
        page's picture is closed (by its thread when it was being read) and released here,
        on this thread. Bounded by the reader's deadline for one page."""
        running: list[_Page] = []
        while self._window:
            page = self._window.popleft()
            if page.future is None or page.future.cancel():
                if page.image is not None:
                    page.image.close()
                page.release()
            else:
                running.append(page)
        self._reading = 0
        try:
            wait(page.future for page in running if page.future is not None)
        finally:
            for page in running:
                page.release()


def _checked(output: Iterable[Span]) -> list[Span]:
    spans = list(output)
    for span in spans:
        if not _is_valid(span):
            raise ReadError("invalid_reader_output")
    return spans


def _is_valid(span: Span) -> bool:
    return (
        _is_valid_location(span.location)
        and isinstance(span.text, str)
        and bool(span.text)
        and isinstance(span.raw, str)
        and _is_valid_confidence(span.confidence)
        and _is_valid_layout(span)
        and _is_valid_marks(span)
    )


def _is_valid_marks(span: Span) -> bool:
    """PRD 5 F3: a known quality and box source, and only on a span read by OCR."""
    if not (_is_one_of(span.quality, QUALITIES) and _is_one_of(span.box_source, BOX_SOURCES)):
        return False
    return span.origin == "ocr" or (span.quality is None and span.box_source is None)


def _is_valid_layout(span: Span) -> bool:
    if not (_is_one_of(span.kind, BLOCK_KINDS) and _is_one_of(span.origin, ORIGINS)):
        return False
    table = (span.table_id, span.row, span.col, span.header)
    if isinstance(span.location, BoxLocation) and span.origin is None:
        return False  # a box must say how it was read (fail closed)
    if isinstance(span.location, CellLocation):
        return span.origin in {None, "cell"} and span.kind is None and table == (None, None, None, None)
    if table == (None, None, None, None):
        return span.kind != "table_cell"
    return (
        span.kind == "table_cell"
        and isinstance(span.table_id, str)
        and TABLE_ID.fullmatch(span.table_id) is not None
        and _is_index(span.row)
        and _is_index(span.col)
        and isinstance(span.header, bool)
    )


def _is_one_of(value: object, allowed: frozenset[str]) -> bool:
    """None, or a str in ``allowed`` (checked as a str first: a list is not even hashable)."""
    return value is None or (isinstance(value, str) and value in allowed)


def _is_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_valid_location(location: object) -> bool:
    if isinstance(location, CellLocation):
        values: tuple[object, ...] = (location.sheet, location.row, location.column)
        return (
            all(_is_whole(value) for value in values)
            and min(location.sheet, location.row, location.column) >= 1
        )
    if isinstance(location, BoxLocation):
        corners = (location.x0, location.y0, location.x1, location.y1)
        return (
            _is_whole(location.page)
            and all(isinstance(value, float | int) and not isinstance(value, bool) for value in corners)
            and location.is_valid()  # page >= 1, 0 <= x0 < x1 <= 1, 0 <= y0 < y1 <= 1 (NaN fails)
        )
    return False


def _is_whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_valid_confidence(confidence: object) -> bool:
    if confidence is None:
        return True
    if not isinstance(confidence, float | int) or isinstance(confidence, bool):
        return False
    return math.isfinite(confidence) and 0.0 <= confidence <= 1.0
