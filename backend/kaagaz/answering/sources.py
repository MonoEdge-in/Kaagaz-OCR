"""Sources by file (#718) and the files searched (#719), filled in after an answer is shaped.

Each document an answer lists shows its folder path and up to PAGES_SHOWN pages: the pages its
citations are on first (most relevant first, each page once with every number citing it), then
the other pages of it the search's fused list found ("Also matches"), in that list's order. The
files searched are the first SEARCHED_FILES documents of the same list, each marked ``used`` when
the answer cites it, with its pages chosen the same way.

No search and no model call of its own: the list is the one the answer's search already fetched
(``Answer.searched``: the first SEARCHED_PIECES of the fused order, of which the model reads only
ANSWER_MAX_EVIDENCE), and the hits came through row level security as the asker, so every file
is one the asker can open. The paths are one statement for every document, as the asker
(``PgPaths``, ``_DOCUMENT_PATHS``): a path never names a folder the asker can't see.
Time: O(d * (c + s)) for d documents, c citations and s searched pieces (at most 20 * 70).
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from kaagaz.answering.evidence import Evidence
from kaagaz.answering.sanitize import clean_snippet
from kaagaz.db.engine import Database, user_or_customer

if TYPE_CHECKING:
    from kaagaz.answering.service import Citation  # the service imports this module

PAGES_SHOWN = 5  # a file's pages, at most (Tushar's design board 1)
SEARCHED_FILES = 10  # "Searched 10 files" (design board 2)
# The fused list's best pieces a search fetches, so SEARCHED_FILES documents can be found in it;
# one more indexed fetch of the same order, no other search (Rag board, step 2).
SEARCHED_PIECES = 50
# Each document's folder names, through the documents and folders the person can see, walked as
# db/audit.py's _FOLDER_PATHS walks them: up from its own folder, stopping below the first hidden
# one (at most 100 levels). Its own folder hidden: no names (NULL); at the top level: folder_id NULL.
_DOCUMENT_PATHS = text(
    "WITH RECURSIVE up (document_id, parent_id, name, depth) AS ("
    " SELECT d.id, f.parent_id, f.name, 1 FROM visible_documents d"
    " JOIN visible_folders f ON f.customer_id = d.customer_id AND f.id = d.folder_id"
    " WHERE d.customer_id = :c AND d.id = ANY(CAST(:ids AS uuid[]))"
    " UNION ALL SELECT up.document_id, f.parent_id, f.name, up.depth + 1 FROM up"
    " JOIN visible_folders f ON f.customer_id = :c AND f.id = up.parent_id WHERE up.depth < 101)"
    " SELECT d.id::text, d.folder_id IS NULL,"
    " (SELECT array_agg(up.name ORDER BY up.depth DESC) FROM up WHERE up.document_id = d.id)"
    " FROM visible_documents d WHERE d.customer_id = :c AND d.id = ANY(CAST(:ids AS uuid[]))"
)


def file_pages(
    document_id: str, citations: Sequence["Citation"], searched: Sequence[Evidence]
) -> list[dict[str, Any]]:
    """``document_id``'s pages: cited first (by rank, then number), then the ones that also
    matched, each once, at most PAGES_SHOWN; each with what opens its exact spot."""
    pages: dict[int, dict[str, Any]] = {}
    own = [c for c in citations if c.document_id == document_id]
    for c in sorted(own, key=lambda c: (c.rank is None, c.rank or 0, c.n)):
        if c.page_no in pages:
            pages[c.page_no]["citations"].append(c.n)
        else:
            pages[c.page_no] = _page(c.page_no, c.regions, c.snippet, [c.n])
    for item in searched:
        if item.hit.document_id == document_id:
            page_no = int(item.regions[0]["page"])
            if page_no not in pages:
                pages[page_no] = _page(page_no, item.regions, clean_snippet(item.hit.text), [])
    return list(pages.values())[:PAGES_SHOWN]


def _page(
    page_no: int, regions: Sequence[Mapping[str, Any]], snippet: str, cited: list[int]
) -> dict[str, Any]:
    on_page = [dict(region) for region in regions if region.get("page") == page_no]
    return {"page_no": page_no, "regions": on_page, "snippet": snippet, "citations": cited}


def searched_files(citations: Sequence["Citation"], searched: Sequence[Evidence]) -> list[dict[str, Any]]:
    """The first SEARCHED_FILES documents of the searched list, best first, each with its name,
    whether the answer cites it, and its pages (``file_pages``)."""
    names: dict[str, str] = {}
    for item in searched:
        names.setdefault(item.hit.document_id, item.hit.document_name)
        if len(names) == SEARCHED_FILES:
            break
    cited = {c.document_id for c in citations}
    return [
        {"document_id": d, "name": name, "used": d in cited, "pages": file_pages(d, citations, searched)}
        for d, name in names.items()
    ]


def with_paths(
    rows: Sequence[Mapping[str, Any]], paths: Mapping[str, list[str] | None]
) -> list[dict[str, Any]]:
    """Each row with its document's ``path``: folder names, root first; [] at the top level;
    None when its own folder is hidden from the asker, or its path is unknown ("Shared with you")."""
    return [{**row, "path": paths.get(str(row["document_id"]))} for row in rows]


class PgPaths:
    """The documents' folder paths, one statement inside ``db.user`` as the asker
    (``user_or_customer``: ``db.customer`` with no person)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def paths(
        self, customer_id: str, document_ids: Sequence[str], *, user_id: str | None
    ) -> dict[str, list[str] | None]:
        """Each document's folder names, root first, only those the person can see; [] at the
        top level; None when its own folder is hidden. A document the person can't see is left
        out. O(k * d) for k documents of depth d <= 100."""
        if not document_ids:
            return {}
        with user_or_customer(self._db, customer_id, user_id) as conn:
            rows = conn.execute(_DOCUMENT_PATHS, {"c": customer_id, "ids": sorted(set(document_ids))})
            return {str(row[0]): [] if row[1] else (list(row[2]) if row[2] else None) for row in rows}
