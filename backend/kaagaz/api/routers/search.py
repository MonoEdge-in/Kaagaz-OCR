"""GET /api/search: search inside documents, grouped by document (PRD 3 Q2).

Why the order: the scope (Q1) is resolved in one short transaction; one that
matches nothing (an unknown or another customer's folder or tag, an empty
list) replies empty before the model or any search runs (edge cases 1 and 9).
The question is embedded outside any transaction; with the model off or down
words and typos still answer. Then one transaction searches.

The query string: ``q`` (1 to 200 characters), the Q1 fields (lists repeat
their key) and the list's ``detected_type`` (Q3 applies the Type filter to
both modes). Anything else is 400 ``bad_request``, as every invalid request.
``q`` is never logged (the access line has no query string). Snippets go
through ``clean_snippet``; only well-formed regions are sent.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from kaagaz.answering.evidence import valid_regions
from kaagaz.answering.sanitize import clean_snippet
from kaagaz.api.auth import User
from kaagaz.api.context import AppContext, get_context
from kaagaz.api.routers.documents import DetectedTypeParam
from kaagaz.search.hybrid import DocumentResult, search_documents
from kaagaz.search.indexing import embed_question, recipe_tag
from kaagaz.search.scope import ScopeBody, resolve

router = APIRouter()

Context = Annotated[AppContext, Depends(get_context)]

MAX_QUESTION = 200


class SearchParams(ScopeBody):
    q: str = Field(min_length=1, max_length=MAX_QUESTION)
    detected_type: DetectedTypeParam | None = None


@router.get("/search")
def search_inside_documents(
    params: Annotated[SearchParams, Query()], user: User, context: Context
) -> dict[str, Any]:
    """``{documents: [{id, display_name, doc_type, score, best: {snippet, page_from,
    page_to, regions}, more}]}``, best first, at most SEARCH_MAX_DOCUMENTS."""
    question = params.q.strip()
    if not question:
        return {"documents": []}
    settings = context.settings
    customer_id = user.customer_id
    with context.db.customer(customer_id) as conn:
        scope = resolve(
            conn, customer_id, params, timezone=settings.timezone, detected_type=params.detected_type
        )
    if scope.matches_nothing:
        return {"documents": []}
    embedding = embed_question(context.model, question)
    with context.db.customer(customer_id) as conn:
        found = search_documents(
            conn,
            customer_id=customer_id,
            query_text=question,
            embedding=embedding,
            max_documents=settings.search_max_documents,
            recipe=recipe_tag(settings.model_embed),
            ef_search=settings.search_ef,
            scope=scope,
        )
    return {"documents": [_document(result) for result in found]}


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
