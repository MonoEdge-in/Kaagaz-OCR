"""GET /api/search: search inside documents, grouped by document (PRD 3 Q2).

Keyword only (File management Q1, Phase 2 PRD 1): no embedding, no model call and no AI
spend per search. The words as written (English and simple), Latin spellings, the
glossary's equivalents, sound keys and typos still match; the meaning arm is off, and so
are story matches, which need the embedding (``story_matches`` stays an empty list).

My words (Q2): the searcher's own words, read and expanded exactly as Ask does
(``PgWords`` inside ``Database.user``, the own-user policy, then ``expand``), passed to
``search_documents`` as ``expansion``. Another person's words are never read. The reply's
``understood_words`` are the ones used (``Expansion.understood``). The search itself is
search/find.py's, shared with WhatsApp's "Get a file" (#603).

Why the order: the scope (Q1, and File management Q3's filters through
``scope.narrow``) is resolved in one short transaction; one that matches nothing (an
unknown or another customer's folder, tag or department, an empty list) replies empty
before any search runs (edge cases 1 and 9). Then one transaction searches.

The query string: ``q`` (1 to 200 characters), the Q1 fields (lists repeat their key), the
list's ``detected_type`` (Q3 applies the Type filter to both modes) and the document list's
Q3 filters (``FileFilters``). Anything else is 400 ``bad_request``, as every invalid
request. ``q`` is never logged (the access line has no query string). Snippets go through
``clean_snippet``; only well-formed regions are sent.

Logged (#482, 3 Nov 2026): every search that runs writes one ``search.run`` row in the
search's own transaction, with how many documents came back and whether the list was capped,
never the words (the audit log holds ids and counts only). A scope that matches nothing runs
no search and writes none.

The cap (issue 374): the search asks for one document more than SEARCH_MAX_DOCUMENTS, and
a reply that had more says ``capped: true`` (the key is left out otherwise), so the screen
never calls a cut list the whole count. The true total is not counted: the candidate lists
are themselves capped, so it would cost another search.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from kaagaz.answering.evidence import valid_regions
from kaagaz.answering.sanitize import clean_snippet
from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.routers.documents import DetectedTypeParam
from kaagaz.search.find import find_documents
from kaagaz.search.hybrid import DocumentResult
from kaagaz.search.scope import FileFilters, ScopeBody, narrow, resolve

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_QUESTION = 200


class SearchParams(ScopeBody, FileFilters):
    q: str = Field(min_length=1, max_length=MAX_QUESTION)
    detected_type: DetectedTypeParam | None = None


def _nothing() -> dict[str, Any]:
    return {"documents": [], "story_matches": [], "understood_words": []}


@router.get("/search")
def search_inside_documents(
    params: Annotated[SearchParams, Query()], user: User, context: Context
) -> dict[str, Any]:
    """``{documents: [{id, display_name, doc_type, score, best: {snippet, page_from,
    page_to, regions}, more}], story_matches: [], understood_words: [{word, meaning}]}``,
    best first, at most SEARCH_MAX_DOCUMENTS, with ``capped: true`` when more matched."""
    question = params.q.strip()
    if not question:
        return _nothing()
    settings = context.settings
    customer_id = user.customer_id
    with context.db.user(customer_id, user.user_id) as conn:
        scope = resolve(
            conn, customer_id, params, timezone=settings.timezone, detected_type=params.detected_type
        )
        scope = narrow(conn, customer_id, scope, params)
    if scope.matches_nothing:
        return _nothing()
    # My words: read once, for this search and this user only, never cached (as Ask does).
    found = find_documents(
        context.db,
        customer_id=customer_id,
        user_id=user.user_id,
        question=question,
        scope=scope,
        most=settings.search_max_documents,
        embed_model=settings.model_embed,
        ef_search=settings.search_ef,
    )
    return {
        "documents": [_document(result) for result in found.documents],
        "story_matches": [],
        "understood_words": [
            {"word": w, "meaning": m, **({"origin": origin} if origin != "personal" else {})}
            for w, m, origin in found.expansion.understood()
        ],
        **({"capped": True} if found.capped else {}),
    }


def _document(result: DocumentResult) -> dict[str, Any]:
    best = result.best
    return {
        "id": best.document_id,
        "display_name": best.document_name,
        "doc_type": best.doc_type,
        "score": best.score,
        "best": {
            "snippet": clean_snippet(best.text),
            "page_from": best.page_from,
            "page_to": best.page_to,
            "regions": valid_regions(best.regions),
        },
        "more": result.more,
    }
