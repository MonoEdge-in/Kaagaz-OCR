"""Give a document's pieces their meaning vectors, so the meaning search finds them.

The pipeline calls ``embed_pieces`` after it saves a document's pieces. Only
pieces with no vector yet are embedded, so running it again does no extra
work. Pieces are read in ``seq`` order, ``batch_size`` at a time; each batch is
one model call and one batched UPDATE. The model call runs outside any
database transaction, so a slow model never holds a transaction open.

``ModelUnavailable`` propagates: the caller decides (the pipeline logs it and
marks the document ready without vectors; word and typo search still work).

Cost: ceil(n / batch_size) model calls and 2 * ceil(n / batch_size) + 1 short
transactions for n pieces; O(batch_size * dim) memory.
"""

from sqlalchemy import text

from kaagaz.ai.client import ModelClient, ModelOutputError
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import vector_literal

_NEXT_BATCH = text(
    "SELECT id, seq, text FROM pieces"
    " WHERE customer_id = :c AND document_id = :d AND embedding IS NULL AND seq > :after"
    " ORDER BY seq LIMIT :n"
)

_WRITE = text(
    "UPDATE pieces SET embedding = CAST(:v AS halfvec(1024)), embedding_model = :m"
    " WHERE customer_id = :c AND id = :id AND embedding IS NULL"
)


def embed_pieces(
    db: Database, customer_id: str, document_id: str, model: ModelClient, batch_size: int = 32
) -> int:
    """Embed the document's pieces that have no vector. Returns how many were written."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    written = 0
    after = -1
    while True:
        with db.customer(customer_id) as conn:
            rows = conn.execute(
                _NEXT_BATCH, {"c": customer_id, "d": document_id, "after": after, "n": batch_size}
            ).all()
        if not rows:
            return written
        after = int(rows[-1][1])
        vectors = model.embed([str(row[2]) for row in rows])
        if len(vectors) != len(rows):
            raise ModelOutputError("embedding_count")
        with db.customer(customer_id) as conn:
            result = conn.execute(
                _WRITE,
                [
                    {"c": customer_id, "id": str(row[0]), "v": vector_literal(vector), "m": model.embed_model}
                    for row, vector in zip(rows, vectors, strict=True)
                ],
            )
            written += result.rowcount
        if len(rows) < batch_size:
            return written
