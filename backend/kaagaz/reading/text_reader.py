"""Read a plain text, Markdown, JSON or HTML file directly, without any AI (#522).

Each kind (``kaagaz/ingestion/resolve.py``) is made plain text first:

* ``txt`` and ``md``: as they are. Markdown's markup stays as typed (``#``, ``*``, ``|``),
  harmless as text; nothing is rendered.
* ``json``: refused with ``too_deep`` when it nests deeper than ``JSON_MAX_DEPTH``, counted
  before it is parsed, so ``json.loads`` never recurses that far; then parsed and written out
  again with an indent of 2 and every character as itself, one value to a line; one with a
  repeated key is read as it is typed, so no value is lost (PRD 9 F-10).
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
``no_text``. A file of more than ``max_lines`` lines with text (CSV_MAX_CELLS, as the CSV
reader's cells; security review, 6 Nov 2026) fails whole with ``too_large``, before any is
read: a 10 MB file of one-letter lines would be 5 million spans to store and search. Errors are
raised outside any ``except`` block, so nothing that quotes the file is chained on.

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
    version = "2"  # PRD 9 F-10: a JSON with a repeated key is read as typed

    def __init__(self, kind: str, *, max_lines: int) -> None:
        """``max_lines``: the most lines with text a file may have (no default: limits are
        Vrushit's decision, PRD section 6)."""
        if kind not in KINDS:
            raise ValueError(f"not a text kind: {kind!r}")
        if max_lines < 1:
            raise ValueError("max_lines must be at least 1")
        self._kind = kind
        self._max_lines = max_lines

    @property
    def name(self) -> str:
        return f"text-{self._kind}"

    def read(self, data: bytes, *, on_page: OnPage | None = None) -> Iterator[Span]:
        # on_page is ignored: the file is read whole, like a CSV (kaagaz/reading/socket.py).
        paged = pages(_plain(self._kind, data))
        if sum(1 for page in paged for line in page if line.strip()) > self._max_lines:
            raise ReadError("too_large")
        found = False
        for page_no, page in enumerate(paged, start=1):
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
    repeated: list[bool] = []

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        if len({key for key, _ in items}) < len(items):
            repeated.append(True)
        return dict(items)

    try:
        value = json.loads(text, object_pairs_hook=pairs)
    except ValueError:  # JSONDecodeError, or an integer too long to convert
        malformed = True
    if malformed:
        raise ReadError("malformed_json")
    if repeated:  # PRD 9 F-10: parsed, a repeated key keeps only its last value; read it as typed
        return text
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
    """Each line, a long one cut by position (never by slicing the rest again: O(n))."""
    for line in text.splitlines():
        start = 0
        while len(line) - start > LINE_CHARS:
            cut = line.rfind(" ", start + LINE_CHARS // 2, start + LINE_CHARS) + 1 or start + LINE_CHARS
            yield line[start:cut]
            start = cut
        yield line[start:]


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

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """``<script/>``: a browser takes it as a start tag (a self-closing slash means nothing
        on a tag that is not void), so all up to ``</script>`` stays hidden; any other is opened
        and closed, as html.parser does."""
        self.handle_starttag(tag, attrs)
        if tag not in _HIDDEN:
            self.handle_endtag(tag)

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
