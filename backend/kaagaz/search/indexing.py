"""Give a document's pieces their meaning vectors, so the meaning search finds them.

The pipeline calls ``embed_pieces`` after it saves a document's pieces. Only
pieces with no vector yet are embedded, so running it again does no extra
work. Pieces are read in ``seq`` order, ``batch_size`` at a time; each batch is
one model call and one batched UPDATE. The model call runs outside any
database transaction, so a slow model never holds a transaction open.

What is embedded (PRD 3 V3, the Qwen3-Embedding model card): a piece is its
reduced header, a blank line, then its text (``piece_input``); a question is
``Instruct: {QUERY_TASK}\nQuery:{question}`` (``query_input``), with no space
after "Query:". Documents get no instruction. The reduced header keeps only the
Type, Date and Section parts of the stored context header: never "Before:" (the
previous piece's line would mix into this vector), the document name (a rename
would make every vector stale) or the page numbers.

Each vector is tagged with ``recipe_tag(model)``, and meaning search uses only
the current tag, so a vector from another model or recipe is left out until
``python -m kaagaz.cli reembed`` replaces it (PRD 3 V4, edge case 7).

``ModelUnavailable`` propagates: the caller decides (the pipeline logs it and
marks the document ready without vectors; word and typo search still work).

Cost: ceil(n / batch_size) model calls and 2 * ceil(n / batch_size) + 1 short
transactions for n pieces; O(batch_size * dim) memory.
"""

from collections.abc import Sequence

from sqlalchemy import Connection, text

from sqlalchemy import Connection, text

from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import vector_literal

# The query task, in English (the model card: queries carry an instruction).
QUERY_TASK = "Given a question about business documents, retrieve passages that answer it"
# The recipe: what a vector was made from (V3). Change it when piece_input changes.
RECIPE = "ctx1"
# The context header parts a piece's vector is made with (kaagaz.chunking.context).
_KEPT_PARTS = ("Type: ", "Date: ", "Section: ")

_NEXT_BATCH = text(
    "SELECT id, seq, text, context_header FROM pieces"
    " WHERE customer_id = :c AND document_id = :d AND embedding IS NULL AND seq > :after"
    " ORDER BY seq LIMIT :n"
)

_WRITE = text(
    "UPDATE pieces SET embedding = CAST(:v AS halfvec(1024)), embedding_model = :m"
    " WHERE customer_id = :c AND id = :id AND embedding IS NULL"
)

# reembed: clear one document's vectors whose tag is not the current one, or
# (all) every vector, so embed_pieces makes them again. Both stay NULL together.
_CLEAR_STALE = text(
    "UPDATE pieces p SET embedding = NULL, embedding_model = NULL"
    " WHERE p.customer_id = :c AND p.document_id = :d AND p.embedding IS NOT NULL"
    " AND (:all OR p.embedding_model <> :m)"
)

# Every customer's id, through the one SECURITY DEFINER helper (migration 0006):
# row level security hides every customer from Database.system.
_CUSTOMERS = text("SELECT c.id FROM kaagaz_customer_ids() AS c (id) ORDER BY c.id")

_DOCUMENTS_WITH_PIECES = text(
    "SELECT DISTINCT p.document_id FROM pieces p WHERE p.customer_id = :c ORDER BY p.document_id"
)


def recipe_tag(model_embed: str) -> str:
    """The ``embedding_model`` value of a vector made by ``model_embed`` today."""
    return f"{model_embed}#{RECIPE}"


def query_input(question: str) -> str:
    """What a question is embedded as, in the model card's exact format. O(length)."""
    return f"Instruct: {QUERY_TASK}\nQuery:{question}"


def piece_input(context_header: str, piece_text: str) -> str:
    """What a piece is embedded as: its reduced header, a blank line, its text.

    The stored header's parts are joined by " | ", and no part can hold a "|"
    (kaagaz.chunking.context), so splitting gives back exactly its parts. O(length).
    """
    kept = [part for part in context_header.split(" | ") if part.startswith(_KEPT_PARTS)]
    return f"{' | '.join(kept)}\n\n{piece_text}" if kept else piece_text


def embed_pieces(
    db: Database, customer_id: str, document_id: str, model: ModelClient, batch_size: int = 32
) -> int:
    """Embed the document's pieces that have no vector. Returns how many were written."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    written = 0
    after = -1
    tag = recipe_tag(model.embed_model)
    while True:
        with db.customer(customer_id) as conn:
            rows = conn.execute(
                _NEXT_BATCH, {"c": customer_id, "d": document_id, "after": after, "n": batch_size}
            ).all()
        if not rows:
            return written
        after = int(rows[-1][1])
        vectors = model.embed([piece_input(str(row[3]), str(row[2])) for row in rows])
        if len(vectors) != len(rows):
            raise ModelOutputError("embedding_count")
        with db.customer(customer_id) as conn:
            result = conn.execute(
                _WRITE,
                [
                    {"c": customer_id, "id": str(row[0]), "v": vector_literal(vector), "m": tag}
                    for row, vector in zip(rows, vectors, strict=True)
                ],
            )
            written += result.rowcount
        if len(rows) < batch_size:
            return written


def customer_ids(conn: Connection) -> Sequence[str]:
    """Every customer, in id order, for reembed. ``conn`` comes from
    ``Database.system()``. O(C log C) for C customers."""
    return [str(row[0]) for row in conn.execute(_CUSTOMERS)]


def document_ids(conn: Connection, customer_id: str) -> Sequence[str]:
    """Every document of this customer that has pieces, in id order (reembed).
    O(P) over the customer's pieces index; O(D) space."""
    return [str(row[0]) for row in conn.execute(_DOCUMENTS_WITH_PIECES, {"c": customer_id})]


def clear_stale(
    conn: Connection, customer_id: str, document_id: str, model_embed: str, *, every: bool
) -> int:
    """Clear the document's vectors that ``embed_pieces`` would not make today
    (``every``: all of them). Returns how many. O(p) for its p pieces."""
    params = {"c": customer_id, "d": document_id, "m": recipe_tag(model_embed), "all": every}
    return conn.execute(_CLEAR_STALE, params).rowcount
