"""Give a document's pieces their meaning vectors, so the meaning search finds them.

The pipeline calls ``embed_pieces`` after it saves a document's pieces. Only
pieces with no vector yet are embedded, so running it again does no extra
work. Pieces are read in ``seq`` order, ``batch_size`` at a time; each batch is
one model call and one batched UPDATE. The model call runs outside any
database transaction, so a slow model never holds a transaction open.

After each batch's write has committed, ``on_batch(done, total)`` is told how far
it has got: the pipeline beats the heartbeat and shows "indexing done of total"
there, so a long document keeps its lease (a 20k-row sheet is about 626
batches, longer than one lease).

What is embedded (PRD 3 V3, the Qwen3-Embedding model card): a piece is its
reduced header, a blank line, then its text (``piece_input``); a question is
``Instruct: {QUERY_TASK}\nQuery:{question}`` (``query_input``), with no space
after "Query:". Documents get no instruction. A Gemini embedding model
(``google/gemini-``, PRD 5 M5) gets the question as plain text, recipe ``gem1``
(``recipe_for``). The reduced header keeps only the
Type, Date and Section parts of the stored context header: never "Before:" (the
previous piece's line would mix into this vector), the document name (a rename
would make every vector stale) or the page numbers.

Each vector is tagged with ``recipe_tag(model)``, and meaning search uses only
the current tag, so a vector from another model or recipe is left out until
``python -m kaagaz.cli reembed`` replaces it (PRD 3 V4, edge case 7).

``ModelUnavailable`` propagates: the caller decides (the pipeline logs it and
marks the document ready without vectors; word and typo search still work).
The worker then fills those pieces in later, a few at a time
(``embed_missing``, run by kaagaz/worker_main.py's ``EmbedMissing``). A
database that cannot be reached is ``BackendUnavailable("database")``, so the
job is retried.

Cost: ceil(n / batch_size) model calls and 2 * ceil(n / batch_size) + 2 short
transactions for n pieces; O(batch_size * dim) memory.
"""

from collections.abc import Callable, Sequence
from typing import NamedTuple

from sqlalchemy import Connection, text

from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable
from kaagaz.db.customers import is_active
from kaagaz.db.engine import Database, unavailable
from kaagaz.search.hybrid import vector_literal
from kaagaz.settings import is_gemini_model

_TO_EMBED = text(
    "SELECT count(*) FROM pieces WHERE customer_id = :c AND document_id = :d AND embedding IS NULL"
)

# The query task, in English (the model card: queries carry an instruction).
QUERY_TASK = "Given a question about business documents, retrieve passages that answer it"
# The recipe: what a vector was made from (V3). Change it when piece_input changes.
RECIPE = "ctx1"
# PRD 5 M5: the recipe of a Gemini embedding model, whose questions are plain text.
GEMINI_RECIPE = "gem1"
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

# embed_missing: this customer's ready, current documents out of Trash that still
# have a piece with no vector, after the cursor, in id order. A document still
# being read is left to its own run; one in Trash or an older version never is searched.
_MISSING = text(
    "SELECT d.id FROM documents d"
    " WHERE d.customer_id = :c AND d.id > CAST(:after AS uuid)"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND EXISTS (SELECT 1 FROM pieces p WHERE p.customer_id = :c AND p.document_id = d.id"
    " AND p.embedding IS NULL)"
    " ORDER BY d.id LIMIT :n"
)
# The cursor's start: below every document id.
_FIRST = "00000000-0000-0000-0000-000000000000"


def recipe_tag(model_embed: str) -> str:
    """The ``embedding_model`` value of a vector made by ``model_embed`` today."""
    return f"{model_embed}#{RECIPE}"


def recipe_for(model_embed: str) -> str:
    """The recipe ``model_embed`` embeds with (PRD 5 M5): ``gem1`` for a ``google/gemini-``
    model, otherwise ``ctx1`` (RECIPE)."""
    return GEMINI_RECIPE if is_gemini_model(model_embed) else RECIPE


def query_input(question: str, model_embed: str = "") -> str:
    """What a question is embedded as for ``model_embed`` (MODEL_EMBED): the plain question
    for recipe ``gem1`` (PRD 5 M5), otherwise the Qwen3 model card's exact format. The
    callers that pass no model get today's format until they pass MODEL_EMBED. O(length)."""
    if recipe_for(model_embed) == GEMINI_RECIPE:
        return question
    return f"Instruct: {QUERY_TASK}\nQuery:{question}"


def embed_question(model: ModelClient | None, question: str) -> list[float] | None:
    """The question's vector, or None (no model, model down, or unusable vectors), so
    search falls back to word and fuzzy matching. One model call, in ``query_input``'s
    format; chat and GET /api/search both embed this way."""
    if model is None or not question:
        return None
    try:
        vectors = model.embed([query_input(question)])
    except (ModelUnavailable, ModelOutputError):
        return None
    return vectors[0] if vectors else None


def piece_input(context_header: str, piece_text: str) -> str:
    """What a piece is embedded as: its reduced header, a blank line, its text.

    The stored header's parts are joined by " | ", and no part can hold a "|"
    (kaagaz.chunking.context), so splitting gives back exactly its parts. O(length).
    """
    kept = [part for part in context_header.split(" | ") if part.startswith(_KEPT_PARTS)]
    return f"{' | '.join(kept)}\n\n{piece_text}" if kept else piece_text


def embed_pieces(
    db: Database,
    customer_id: str,
    document_id: str,
    model: ModelClient,
    batch_size: int = 32,
    on_batch: Callable[[int, int], None] | None = None,
    *,
    limit: int | None = None,
) -> int:
    """Embed the document's pieces that have no vector. Returns how many were written.
    ``on_batch(done, total)`` follows each batch, outside any transaction: ``done`` of
    the ``total`` pieces that had no vector at the start. ``limit``: read at most that
    many pieces (the worker's pass), else all of them."""
    if batch_size < 1 or (limit is not None and limit < 1):
        raise ValueError("batch_size and limit must be at least 1")
    with unavailable(), db.customer(customer_id) as conn:
        total = int(conn.execute(_TO_EMBED, {"c": customer_id, "d": document_id}).scalar_one())
    written = done = 0
    after = -1
    left = limit
    tag = recipe_tag(model.embed_model)
    while True:
        size = batch_size if left is None else min(batch_size, left)
        with unavailable(), db.customer(customer_id) as conn:
            rows = conn.execute(
                _NEXT_BATCH, {"c": customer_id, "d": document_id, "after": after, "n": size}
            ).all()
        if not rows:
            return written
        after = int(rows[-1][1])
        vectors = model.embed([piece_input(str(row[3]), str(row[2])) for row in rows])
        if len(vectors) != len(rows):
            raise ModelOutputError("embedding_count")
        with unavailable(), db.customer(customer_id) as conn:
            result = conn.execute(
                _WRITE,
                [
                    {"c": customer_id, "id": str(row[0]), "v": vector_literal(vector), "m": tag}
                    for row, vector in zip(rows, vectors, strict=True)
                ],
            )
            written += result.rowcount
        done += len(rows)
        if on_batch is not None:
            on_batch(done, total)  # committed: the heartbeat never waits inside a transaction
        if left is not None:
            left -= len(rows)
        if len(rows) < size or left == 0:
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


class MissingPass(NamedTuple):
    """What one ``embed_missing`` pass did. ``after`` is the cursor the next pass
    starts after (None: from the first customer). ``skipped`` is (customer,
    document, code) of a document whose vectors came back unusable, and ``down``
    the model's code when it stopped answering; either one ends the pass."""

    written: int
    after: tuple[str, str] | None
    skipped: tuple[str, str, str] | None = None
    down: str | None = None


def embed_missing(
    db: Database, model: ModelClient, after: tuple[str, str] | None, *, limit: int = 64
) -> MissingPass:
    """Give at most ``limit`` pieces their missing vectors, through ``embed_pieces``,
    walking (customer, document) in id order from just after the cursor ``after``.

    Only the pieces of ready, current documents out of Trash (``_MISSING``), of
    active customers: one that is disabled or being deleted (0014) is passed
    over, so it costs no model call, and is picked up once enabled. The
    customers come from ``kaagaz_customer_ids()`` through ``Database.system()``;
    everything else runs inside ``Database.customer``, one customer at a time.
    The cursor moves past a document once it has no piece left without a vector,
    or once it was skipped, so one bad document never holds up the rest; a pass
    that reaches the last document starts the next one from the first again.

    * ``ModelUnavailable``: the pass stops; the cursor stays (``down`` is set).
    * ``ModelOutputError`` or ``ValueError`` (a NaN vector, from
      ``vector_literal``): that document is skipped, and the pass stops there,
      since an unknown share of ``limit`` went to it.

    Database errors propagate: the caller logs them. Running it again changes
    nothing that is done: ``_WRITE`` only fills a NULL vector.

    Cost: one statement for the C customers, one status lookup and one
    ``_MISSING`` per customer after the cursor (O(D) over its documents, each
    with an index probe of its pieces), then ``embed_pieces`` per document: at most ``limit`` pieces sent,
    in batches of 32 within a document (counted by pieces written: a piece
    another run fills at the same moment is sent but not counted).
    Memory O(C + limit * dim).
    """
    with db.system() as conn:
        customers = [c for c in customer_ids(conn) if after is None or c >= after[0]]
    written = 0
    for customer in customers:
        start = after[1] if after is not None and after[0] == customer else _FIRST
        with db.customer(customer) as conn:
            params = {"c": customer, "after": start, "n": limit - written}
            active = is_active(conn, customer)
            documents = [str(row[0]) for row in conn.execute(_MISSING, params)] if active else []
        for document in documents:
            budget = limit - written
            try:
                done = embed_pieces(db, customer, document, model, limit=budget)
            except ModelUnavailable as error:
                return MissingPass(written, after, down=error.code)
            except ModelOutputError as error:
                return MissingPass(written, (customer, document), (customer, document, error.code))
            except ValueError:
                return MissingPass(written, (customer, document), (customer, document, "bad_vector"))
            written += done
            if done == budget:  # the budget is spent; this document may have more
                return MissingPass(written, after)
            after = (customer, document)
    return MissingPass(written, None)
