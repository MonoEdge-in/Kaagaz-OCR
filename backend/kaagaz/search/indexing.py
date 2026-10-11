"""Give a document's pieces their meaning vectors, so the meaning search finds them.

The pipeline calls ``embed_pieces`` after it saves a document's pieces. Only
pieces with no vector yet, or a vector of another recipe tag (PRD 5 M5: a stale
tag is missing), are embedded, so running it again does no extra work. Then the
same for the document's section summaries (``document_sections``, PRD 5 CX2): a
section with a summary is embedded as ``section_input`` and tagged in
``embed_recipe``. Pieces, then sections, are read in ``seq`` order,
``batch_size`` at a time; each batch is one model call and one batched UPDATE. The model call runs outside any
database transaction, so a slow model never holds a transaction open.

After each batch's write has committed, ``on_batch(done, total)`` is told how far
it has got: the pipeline beats the heartbeat and shows "indexing done of total"
there, so a long document keeps its lease (a 20k-row sheet is about 626
batches, longer than one lease).

What is embedded (PRD 3 V3, PRD 5 M5): a piece is its reduced header, a blank line,
then its text (``piece_input``); a question is embedded as plain text. Documents get no
instruction. A vector is tagged with its model and recipe (``recipe_tag``: ``gem1`` for a
google/gemini- model, ``recipe_for``). The reduced header keeps only the
Type, Date and Section parts of the stored context header: never "Before:" (the
previous piece's line would mix into this vector), the document name (a rename
would make every vector stale) or the page numbers.

Each vector is tagged with ``recipe_tag(model)``, and meaning search uses only
the current tag, so a vector from another model or recipe is left out until
``python -m kaagaz.cli reembed`` replaces it (PRD 3 V4, edge case 7), or the
worker's ``embed_missing`` does, a few at a time (M5).

``ModelUnavailable`` propagates: the caller decides (the pipeline logs it and
marks the document ready without vectors; word and typo search still work).
The worker then fills those pieces in later, a few at a time
(``embed_missing``, run by kaagaz/worker_main.py's ``EmbedMissing``). A
database that cannot be reached is ``BackendUnavailable("database")``, so the
job is retried.

Cost: ceil(n / batch_size) + ceil(s / batch_size) model calls and
2 * (ceil(n / batch_size) + ceil(s / batch_size)) + 3 short transactions for n pieces
and s sections; O(batch_size * dim) memory.
"""

from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

from sqlalchemy import Connection, TextClause, text

from kaagaz.ai.client import ModelClient, ModelOutputError, ModelUnavailable
from kaagaz.ai.usage import usage_scope
from kaagaz.db.customers import is_active
from kaagaz.db.engine import Database, unavailable
from kaagaz.search.hybrid import vector_literal
from kaagaz.settings import is_gemini_model

# A vector is missing when there is none or it has another tag (PRD 5 M5: a stale tag is
# made again). A section is embedded only when it has a summary.
_TO_EMBED = text(
    "SELECT (SELECT count(*) FROM pieces WHERE customer_id = :c AND document_id = :d"
    " AND (embedding IS NULL OR embedding_model <> :m))"
    " + (SELECT count(*) FROM document_sections WHERE customer_id = :c AND document_id = :d"
    " AND summary IS NOT NULL AND (embedding IS NULL OR embed_recipe <> :m))"
)

# The recipe: what a vector was made from (V3). Change it when piece_input changes.
RECIPE = "ctx1"
# PRD 5 M5: the recipe of a Gemini embedding model, whose questions are plain text.
GEMINI_RECIPE = "gem1"
# The context header parts a piece's vector is made with (kaagaz.chunking.context).
_KEPT_PARTS = ("Type: ", "Date: ", "Section: ")

_NEXT_BATCH = text(
    "SELECT id, seq, text, context_header FROM pieces"
    " WHERE customer_id = :c AND document_id = :d AND (embedding IS NULL OR embedding_model <> :m)"
    " AND seq > :after"
    " ORDER BY seq LIMIT :n"
)

_WRITE = text(
    "UPDATE pieces SET embedding = CAST(:v AS halfvec(1024)), embedding_model = :m"
    " WHERE customer_id = :c AND id = :id AND (embedding IS NULL OR embedding_model <> :m)"
)

_NEXT_SECTIONS = text(
    "SELECT id, seq, path, summary FROM document_sections"
    " WHERE customer_id = :c AND document_id = :d AND summary IS NOT NULL"
    " AND (embedding IS NULL OR embed_recipe <> :m) AND seq > :after"
    " ORDER BY seq LIMIT :n"
)

# 0021 grants the app UPDATE of these two columns only.
_WRITE_SECTION = text(
    "UPDATE document_sections SET embedding = CAST(:v AS halfvec(1024)), embed_recipe = :m"
    " WHERE customer_id = :c AND id = :id AND (embedding IS NULL OR embed_recipe <> :m)"
)

# reembed: clear one document's vectors whose tag is not the current one, or
# (all) every vector, so embed_pieces makes them again. Both stay NULL together.
_CLEAR_STALE = text(
    "UPDATE pieces p SET embedding = NULL, embedding_model = NULL"
    " WHERE p.customer_id = :c AND p.document_id = :d AND p.embedding IS NOT NULL"
    " AND (:all OR p.embedding_model <> :m)"
)
_CLEAR_STALE_SECTIONS = text(
    "UPDATE document_sections s SET embedding = NULL, embed_recipe = NULL"
    " WHERE s.customer_id = :c AND s.document_id = :d AND s.embedding IS NOT NULL"
    " AND (:all OR s.embed_recipe <> :m)"
)

# Every customer's id, through the one SECURITY DEFINER helper (migration 0006):
# row level security hides every customer from Database.system.
_CUSTOMERS = text("SELECT c.id FROM kaagaz_customer_ids() AS c (id) ORDER BY c.id")

_DOCUMENTS_WITH_PIECES = text(
    "SELECT DISTINCT p.document_id FROM pieces p WHERE p.customer_id = :c ORDER BY p.document_id"
)

# embed_missing: this customer's ready, current documents out of Trash that still
# have a piece or a section summary with no vector or a stale one (M5), after the cursor,
# in id order. A document still being read is left to its own run; one in Trash or an
# older version never is searched.
_MISSING = text(
    "SELECT d.id FROM documents d"
    " WHERE d.customer_id = :c AND d.id > CAST(:after AS uuid)"
    " AND d.deleted_at IS NULL AND d.is_current AND d.status = 'ready'"
    " AND (EXISTS (SELECT 1 FROM pieces p WHERE p.customer_id = :c AND p.document_id = d.id"
    " AND (p.embedding IS NULL OR p.embedding_model <> :m))"
    " OR EXISTS (SELECT 1 FROM document_sections s WHERE s.customer_id = :c AND s.document_id = d.id"
    " AND s.summary IS NOT NULL AND (s.embedding IS NULL OR s.embed_recipe <> :m)))"
    " ORDER BY d.id LIMIT :n"
)
# The cursor's start: below every document id.
_FIRST = "00000000-0000-0000-0000-000000000000"


def recipe_tag(model_embed: str) -> str:
    """The ``embedding_model`` value of a vector made by ``model_embed`` today: the model,
    then # and its recipe (``recipe_for``, PRD 5 M5), such as ``google/gemini-embedding-2#gem1``."""
    return f"{model_embed}#{recipe_for(model_embed)}"


def recipe_for(model_embed: str) -> str:
    """The recipe ``model_embed`` embeds with (PRD 5 M5): ``gem1`` for a ``google/gemini-``
    model, otherwise ``ctx1`` (RECIPE)."""
    return GEMINI_RECIPE if is_gemini_model(model_embed) else RECIPE


def embed_question(model: ModelClient | None, question: str) -> list[float] | None:
    """The question's vector, or None (no model, model down, or unusable vectors), so
    search falls back to word and fuzzy matching. One model call, with the question as plain
    text; chat and GET /api/search both embed this way."""
    if model is None or not question:
        return None
    try:
        vectors = model.embed([question])
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


def section_input(path: Sequence[str], summary: str) -> str:
    """What a section is embedded as (CX2): its heading path as a piece header's Section
    part, a blank line, its summary; the summary alone with no path. O(length)."""
    return f"Section: {' > '.join(path)}\n\n{summary}" if path else summary


class _Kind(NamedTuple):
    """What ``embed_pieces`` embeds: its next batch, its write, and each row's input."""

    next_batch: TextClause
    write: TextClause
    input: Callable[[Sequence[Any]], str]


_KINDS = (
    _Kind(_NEXT_BATCH, _WRITE, lambda row: piece_input(str(row[3]), str(row[2]))),
    _Kind(_NEXT_SECTIONS, _WRITE_SECTION, lambda row: section_input(list(row[2]), str(row[3]))),
)


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
    """Embed the document's pieces, then its section summaries, that have no vector of the
    current tag. Returns how many were written. ``on_batch(done, total)`` follows each
    batch, outside any transaction: ``done`` of the ``total`` pieces and sections that
    had none at the start. ``limit``: read at most that many (the worker's pass), else
    all of them."""
    if batch_size < 1 or (limit is not None and limit < 1):
        raise ValueError("batch_size and limit must be at least 1")
    tag = recipe_tag(model.embed_model)
    ids = {"c": customer_id, "d": document_id, "m": tag}
    with unavailable(), db.customer(customer_id) as conn:
        total = int(conn.execute(_TO_EMBED, ids).scalar_one())
    written = done = 0
    left = limit
    for kind in _KINDS:
        after = -1
        while left != 0:
            size = batch_size if left is None else min(batch_size, left)
            with unavailable(), db.customer(customer_id) as conn:
                rows = conn.execute(kind.next_batch, {**ids, "after": after, "n": size}).all()
            if not rows:
                break
            after = int(rows[-1][1])
            vectors = model.embed([kind.input(row) for row in rows])
            if len(vectors) != len(rows):
                raise ModelOutputError("embedding_count")
            with unavailable(), db.customer(customer_id) as conn:
                result = conn.execute(
                    kind.write,
                    [
                        {**ids, "id": str(row[0]), "v": vector_literal(vector)}
                        for row, vector in zip(rows, vectors, strict=True)
                    ],
                )
                written += result.rowcount
            done += len(rows)
            if on_batch is not None:
                on_batch(done, total)  # committed: the heartbeat never waits inside a transaction
            if left is not None:
                left -= len(rows)
            if len(rows) < size:
                break
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
    """Clear the document's piece and section vectors that ``embed_pieces`` would not make
    today (``every``: all of them). Returns how many. O(p + s) for its p pieces and s
    sections."""
    params = {"c": customer_id, "d": document_id, "m": recipe_tag(model_embed), "all": every}
    pieces = conn.execute(_CLEAR_STALE, params).rowcount
    return pieces + conn.execute(_CLEAR_STALE_SECTIONS, params).rowcount


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

    Only the pieces and sections of ready, current documents out of Trash (``_MISSING``),
    with no vector or a stale one (M5), of
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
    nothing that is done: ``_WRITE`` only fills a NULL vector. Each document's calls are
    billed in its own usage scope, kind embed, with no user (model choice, SP1).

    Cost: one statement for the C customers, one status lookup and one
    ``_MISSING`` per customer after the cursor (O(D) over its documents, each
    with an index probe of its pieces), then ``embed_pieces`` per document: at most ``limit`` pieces sent,
    in batches of 32 within a document (counted by pieces written: a piece
    another run fills at the same moment is sent but not counted).
    Memory O(C + limit * dim).
    """
    tag = recipe_tag(model.embed_model)
    with db.system() as conn:
        customers = [c for c in customer_ids(conn) if after is None or c >= after[0]]
    written = 0
    for customer in customers:
        start = after[1] if after is not None and after[0] == customer else _FIRST
        with db.customer(customer) as conn:
            params = {"c": customer, "after": start, "n": limit - written, "m": tag}
            active = is_active(conn, customer)
            documents = [str(row[0]) for row in conn.execute(_MISSING, params)] if active else []
        for document in documents:
            budget = limit - written
            try:
                # SP1: no user asked for this pass (user NULL).
                with usage_scope(db, customer_id=customer, user_id=None, kind="embed", document_id=document):
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
