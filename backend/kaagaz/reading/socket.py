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
nothing else has any.

``read`` takes an optional ``on_page`` callback. A reader that reads page by
page (OCR of scans and photos) calls it after each page, so a long reading
can say "still working" (the worker's heartbeat renews the job's lease). It
is keyword-only and optional, so a reader with nothing to beat for between
pages accepts it and ignores it.

PROVISIONAL until the reader contract (#3) is written. PROVISIONAL (plan
7.4): ``on_page`` is part of that contract, for Vrushit and PRD 2 to agree.
"""

import math
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from kaagaz.reading.spans import BLOCK_KINDS, ORIGINS, TABLE_ID, BoxLocation, CellLocation, ReadError, Span


class Reader(Protocol):
    name: str

    @property
    def version(self) -> str: ...  # read-only: asked for after reading

    def read(self, data: bytes, *, on_page: Callable[[], None] | None = None) -> Iterable[Span]: ...


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

    def read(
        self, detected_type: str, data: bytes, *, on_page: Callable[[], None] | None = None
    ) -> list[Span]:
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
    )


def _is_valid_layout(span: Span) -> bool:
    if (span.kind is not None and span.kind not in BLOCK_KINDS) or (
        span.origin is not None and span.origin not in ORIGINS
    ):
        return False
    table = (span.table_id, span.row, span.col, span.header)
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
