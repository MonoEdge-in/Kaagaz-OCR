# How to add a new reader to the plug socket

> DRAFT (#32). PROVISIONAL until the reader contract (#3) is written; the
> interface below is what the local code uses today.

A reader turns a document's bytes into spans: pieces of text, each with
exactly where it came from. Every reader sits behind one plug socket, so
adding one touches only its own module and one mapping entry, if its file type
has no reader yet. Each type has exactly one reader: a second zip reader (for
.docx), or a PDF reader that needs page boxes, also changes `socket.py` and
`spans.py`, and waits for the reader contract (#3).

## The interface

```python
from kaagaz.reading.socket import NoReader
from kaagaz.reading.spans import CellLocation, ReadError, Span


class Reader(Protocol):
    def read(self, data: bytes) -> Iterable[Span]: ...
```

A reader gets the file's bytes, already virus scanned and checked against the
upload fingerprint. It returns spans:

```python
Span(text="INV-1", raw=" INV-1 ", location=CellLocation(sheet=1, row=2, column=1))
```

| Field | Must be |
|---|---|
| `text` | Non-empty and cleaned (surrounding spaces removed). The socket checks only that it is not empty, so the reader must do the cleaning. |
| `raw` | The stored value before cleaning, next to the cleaned text (the Excel reader resolves shared strings and writes booleans as TRUE / FALSE) |
| `location` | Where it came from. Only `CellLocation` exists today; a page box for PDFs arrives with the first PDF reader, once #3 fixes units and origin |

The socket refuses any span without a valid location or text
(`invalid_reader_output`).

## What a reader may refuse

| Situation | Do this |
|---|---|
| The file is damaged or breaks a limit | `raise ReadError("short_code")`. The code is a fixed word, never built from the file's content. Raise it outside any `except` block, so no library error (which can quote content) is attached. |
| The file is a type this reader does not handle (a .docx in the Excel reader) | `raise NoReader(sniff.ZIP)`, using the constant from `kaagaz/ingestion/sniff.py` for the type your reader is registered under (the reader is not told the type). The document is marked `needs_reader`, not failed. |
| A value cannot be read | Leave it out. Never guess. "Unreadable" is a valid answer. |

A reader must never mask, store, log or send anything: masking and saving
happen after the socket, in the pipeline, the same way for every reader.

## Steps

1. Create `backend/kaagaz/reading/<name>_reader.py` with a class that has `read`.
2. Take every limit as a constructor argument, with no default (limits are
   Vrushit's).
3. Write `backend/tests/test_<name>_reader.py`: normal values, positions,
   damaged files, every refusal code, and that errors have nothing chained.
4. Add one entry to the mapping given to `ReaderSocket`, keyed by the type
   from `kaagaz/ingestion/sniff.py`. There is no application wiring yet: today
   the mapping is built in `scripts/run_fifty_files.py` and `tests/test_pipeline.py`.
5. Outside `kaagaz/reading/`, only the wiring (`scripts/`, `tests/`) may import
   your reader. `tests/test_reader_socket.py` fails if any other module under
   `kaagaz/` imports it.
6. Any library the reader needs is a new dependency: propose it with its name
   and licence to Vrushit first (CLAUDE.md never-do 2).
