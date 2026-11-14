"""Search inside documents as one person (PRD 3 Q2), shared by GET /api/search and Kaagaz on
WhatsApp's "Get a file" (#603): the same words, the same documents, the same audit row.

Keyword only (File management Q1): no embedding and no model call. The searcher's own words
(My words, Q2) are read and expanded exactly as Ask does (``PgWords`` inside ``Database.user``,
then ``expand``); another person's words are never read. Then one transaction, as the person,
searches through PRD 7's views and writes one ``search.run`` row (#482): how many documents came
back and whether the list was capped, never the words. It asks for one document more than
``most``, so a list that had more says ``capped`` (issue 374).
"""

from dataclasses import dataclass

from kaagaz.answering.expansion import NO_EXPANSION, Expansion, PgWords, expand
from kaagaz.db import audit
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import DocumentResult, search_documents
from kaagaz.search.indexing import recipe_tag
from kaagaz.search.scope import Scope


@dataclass(frozen=True, slots=True)
class Found:
    documents: list[DocumentResult]  # best first, at most ``most``
    capped: bool
    expansion: Expansion


def find_documents(
    db: Database,
    *,
    customer_id: str,
    user_id: str,
    question: str,
    scope: Scope,
    most: int,
    embed_model: str,
    ef_search: int,
) -> Found:
    """The documents ``user_id`` may see that match ``question`` in ``scope`` (module notes)."""
    document_id = (
        scope.document_ids[0] if scope.document_ids is not None and len(scope.document_ids) == 1 else None
    )
    words = PgWords(db).words(customer_id, user_id, document_id=document_id)
    expansion = expand(question, words) if words else NO_EXPANSION
    with db.user(customer_id, user_id) as conn:
        found = search_documents(
            conn,
            customer_id=customer_id,
            query_text=question,
            embedding=None,
            max_documents=most + 1,  # one more tells a capped list
            recipe=recipe_tag(embed_model),
            ef_search=ef_search,
            scope=scope,
            expansion=expansion,
        )
        capped = len(found) > most
        audit.record(
            conn,
            customer_id,
            actor_user_id=user_id,
            action="search.run",
            target_type="customer",
            target_id=customer_id,
            details={"documents": min(len(found), most), "capped": capped},
        )
    return Found(found[:most], capped, expansion)
