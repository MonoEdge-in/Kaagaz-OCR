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
present, a real cell or a box inside the page, and a confidence that is None
or between 0 and 1.

PROVISIONAL until the reader contract (#3) is written.
"""

import math
from collections.abc import Iterable, Mapping
from typing import Protocol

from kaagaz.reading.spans import BoxLocation, CellLocation, ReadError, Span


class Reader(Protocol):
    name: str
    version: str

    def read(self, data: bytes) -> Iterable[Span]: ...


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

    def read(self, detected_type: str, data: bytes) -> list[Span]:
        """All spans of the document, or ReadError / NoReader.

        The socket checks every span has a real location and non-empty text,
        so no reader can hand back a value that cannot be pointed at. A
        reader that breaks this is a bug in that reader; it fails with
        ``invalid_reader_output`` for good rather than being retried.
        """
        reader = self._readers.get(detected_type)  # O(1) dispatch
        if reader is None:
            raise NoReader(detected_type)
        return _checked(reader.read(data))

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
    )


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
