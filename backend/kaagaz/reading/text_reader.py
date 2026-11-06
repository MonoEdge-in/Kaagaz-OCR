"""Read a plain text, Markdown, JSON or HTML file directly, without any AI (#522).

Each kind (``kaagaz/ingestion/resolve.py``) is made plain text first:

* ``txt`` and ``md``: as they are. Markdown's markup stays as typed (``#``, ``*``, ``|``),
  harmless as text; nothing is rendered.
* ``json``: refused with ``too_deep`` when it nests deeper than ``JSON_MAX_DEPTH``, counted
  before it is parsed, so ``json.loads`` never recurses that far; then parsed and written out
  again with an indent of 2 and every character as itself, one value to a line.
* ``html``: its text only, by the standard library's ``html.parser``: ``script``, ``style``,
  ``noscript``, ``template`` and ``head`` are dropped with all they hold, except the title,
  which is the first line; comments too. A block (a paragraph, a heading, a list item, a table
  row, a line break) starts a new line and a table's cells are joined by " | ". Nothing is
  rendered and no URL is ever fetched: a link keeps its words, never its address.

Then the text is cut into pages of about ``PAGE_CHARS`` characters, at line breaks: a page
ends before the line that would take it past ``PAGE_CHARS``, and never starts with a blank
line. A line longer than ``LINE_CHARS`` is cut, at its last space before that when it has
one, so every line fits the page viewer. Each page is shown as a one-column sheet: every line
with text is one span, ``CellLocation(sheet=page, row=its line on the page, column=1)``, read
from the file itself (origin ``cell``), so it can be pointed at; a blank line is a row with no
cell.

Like the CSV reader: the file must be UTF-8 (a byte order mark is allowed), or it fails with
``not_utf8``; control characters other than tab, line feed, form feed and carriage return fail
with ``not_text``, checked over the whole file. A file with no text at all fails with
``no_text``. Errors are raised outside any ``except`` block, so nothing that quotes the file
is chained on.

Time and memory are O(n) in the file size, bounded by the upload size limit.
"""

import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser

from kaagaz.ingestion import resolve
from kaagaz.ingestion.resolve import JSON_MAX_DEPTH, json_depth
from kaagaz.ingestion.sniff import FORBIDDEN_CONTROL
from kaagaz.reading.socket import OnPage
from kaagaz.reading.spans import CellLocation, ReadError, Span

PAGE_CHARS = 3000
LINE_CHARS = 200
KINDS = frozenset({resolve.TXT, resolve.MD, resolve.JSON, resolve.HTML})

_SPACES = re.compile(r"\s+")


class TextReader:
    # Stored with the output (reader_outputs). Only the standard library is used, so the
    # version is this module's own: raise it when what it reads changes.
    version = "1"

    def __init__(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"not a text kind: {kind!r}")
        self._kind = kind

    @property
    def name(self) -> str:
        return f"text-{self._kind}"

    def read(self, data: bytes, *, on_page: OnPage | None = None) -> Iterator[Span]:
        # on_page is ignored: the file is read whole, like a CSV (kaagaz/reading/socket.py).
        text = _plain(self._kind, data)
        found = False
        for page_no, page in enumerate(pages(text), start=1):
            for row, line in enumerate(page, start=1):
                cleaned = line.strip()
                if cleaned:
                    found = True
                    yield Span(text=cleaned, raw=line, location=CellLocation(page_no, row, 1), origin="cell")
        if not found:
            raise ReadError("no_text")


def _plain(kind: str, data: bytes) -> str:
    """The file as plain text, by its kind."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise ReadError("not_utf8")
    if FORBIDDEN_CONTROL.search(data):  # the same rule as the upload type check
        raise ReadError("not_text")
    if kind == resolve.JSON:
        return _json_text(text)
    if kind == resolve.HTML:
        return html_text(text)
    return text


def _json_text(text: str) -> str:
    if json_depth(text) > JSON_MAX_DEPTH:
        raise ReadError("too_deep")
    malformed = False
    try:
        value = json.loads(text)
    except ValueError:  # JSONDecodeError, or an integer too long to convert
        malformed = True
    if malformed:
        raise ReadError("malformed_json")
    return json.dumps(value, indent=2, ensure_ascii=False)


def pages(text: str) -> list[list[str]]:
    """The text's lines, each at most LINE_CHARS long, in pages of about PAGE_CHARS characters
    cut at line breaks; no page starts with a blank line, and none is all blank. O(n)."""
    result: list[list[str]] = []
    page: list[str] = []
    size = 0
    for line in _lines(text):
        if page and size + len(line) > PAGE_CHARS:
            result.append(page)
            page, size = [], 0
        if not page and not line.strip():
            continue
        page.append(line)
        size += len(line) + 1
    if page:
        result.append(page)
    return result


def _lines(text: str) -> Iterator[str]:
    for line in text.splitlines():
        while len(line) > LINE_CHARS:
            cut = line.rfind(" ", LINE_CHARS // 2, LINE_CHARS) + 1 or LINE_CHARS
            yield line[:cut]
            line = line[cut:]
        yield line


# --- HTML ---------------------------------------------------------------------------------------

# Dropped with everything inside them. ``head`` too, but its title is kept.
_HIDDEN = frozenset({"script", "style", "noscript", "template"})
# What may be in ``head``: any other tag means the head has ended, closed or not.
_HEAD_ONLY = frozenset({"title", "meta", "link", "base", "style", "script", "noscript", "template"})
# A block: it starts and ends a line.
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "caption", "dd", "details", "dialog",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "option", "p", "pre",
        "section", "summary", "table", "tbody", "tfoot", "thead", "tr", "ul",
    }
)  # fmt: skip
_CELLS = frozenset({"td", "th"})


def html_text(markup: str) -> str:
    """The words of an HTML page, its title first, as described in the module notes. O(n)."""
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    return parser.text()


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = []
        self._line: list[str] = []
        self._title: list[str] | None = None  # the first title's words, once one has started
        self._in_title = False
        self._hidden = 0  # how deep inside script, style, noscript or template
        self._in_head = False
        self._pre = 0  # how deep inside pre, whose line breaks are kept

    def text(self) -> str:
        self._break()
        title = _SPACES.sub(" ", "".join(self._title or [])).strip()
        return "\n".join([title, *self._lines] if title else self._lines)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN:
            self._hidden += 1
        elif tag == "head":
            self._in_head = True
        elif tag == "title":
            self._in_title = self._title is None
            if self._in_title:
                self._title = []
        elif self._in_head and tag not in _HEAD_ONLY:
            self._in_head = False  # the body has started without </head>
        if self._hidden or self._in_head or self._in_title:
            return
        if tag in _CELLS:
            if "".join(self._line).strip():
                self._line.append(" | ")
        elif tag in _BLOCKS:
            self._break()
        if tag == "pre":
            self._pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False
        elif not (self._hidden or self._in_head) and tag in _BLOCKS:
            self._break()
            if tag == "pre":
                self._pre = max(0, self._pre - 1)

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        if self._in_title and self._title is not None:
            self._title.append(data)
        elif not self._in_head:
            first, *more = data.split("\n") if self._pre else [data]
            self._line.append(first)
            for line in more:
                self._break()
                self._line.append(line)

    def _break(self) -> None:
        line = _SPACES.sub(" ", "".join(self._line)).strip()
        if line:
            self._lines.append(line)
        self._line = []
