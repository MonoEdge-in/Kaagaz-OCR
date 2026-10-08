"""The page cache, table ``page_reads`` (PRD 5 G7, migration 0021).

Every page the reading model reads is saved here at once, keyed by document, page
number, the image's sha256, model and prompt version (``PageKey``). Before calling
the model, the engine looks here first, so a retry after a crash, an outage or a lost
lease reuses every page already read: a 60-page document stopped at page 30 resumes
at 31 and is never billed twice.

The cache is written OUTSIDE the one replacing save (kaagaz.db.pipeline_store), each
page in its own short transaction as that customer, so a crash keeps every page read
before it. ``put`` is an upsert on the key: a page read again (F5) replaces its row,
result, token counts and time, and never adds a second one. The rows go with their
document (ON DELETE CASCADE), keeping PRD 2's rule that a deleted document is
forgotten at once.

Every statement names the customer as well (row level security is the second lock,
docs/architecture.md). A row for another customer's document cannot be written: the
(customer_id, document_id) foreign key refuses it, and ``put`` raises.

``result`` is the reader's reply for the page as a JSON object, before any box is
pinned (the council's architect finding), so a change to the pinning never needs a
page read again. It is data from the model, never instructions.

Cost: one statement by the unique key for each call, O(log P) in the cached pages.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from kaagaz.db.engine import Database, unavailable

_GET = text(
    "SELECT result, input_tokens, output_tokens FROM page_reads"
    " WHERE customer_id = :c AND document_id = :d AND page_no = :page_no"
    " AND image_sha256 = :image_sha256 AND model = :model AND prompt_version = :prompt_version"
)
# The key's columns are never updated (only result, the counts and the time are granted).
_PUT = text(
    "INSERT INTO page_reads (customer_id, document_id, page_no, image_sha256, model, prompt_version,"
    " result, input_tokens, output_tokens)"
    " VALUES (:c, :d, :page_no, :image_sha256, :model, :prompt_version, CAST(:result AS jsonb),"
    " :input_tokens, :output_tokens)"
    " ON CONFLICT (document_id, page_no, image_sha256, model, prompt_version) DO UPDATE"
    " SET result = EXCLUDED.result, input_tokens = EXCLUDED.input_tokens,"
    " output_tokens = EXCLUDED.output_tokens, created_at = now()"
)


@dataclass(frozen=True, slots=True)
class PageKey:
    """One page read's key (G7): the customer it belongs to, its document and page (from
    1), the sha256 of the page image's pixels in lower-case hex, the model (OCR_MODEL)
    and the prompt version (GEMINI_OCR_PROMPT_VERSION). A change to any of them is a
    different read."""

    customer_id: str
    document_id: str
    page_no: int
    image_sha256: str
    model: str
    prompt_version: str


def _params(key: PageKey) -> dict[str, Any]:
    return {
        "c": key.customer_id,
        "d": key.document_id,
        "page_no": key.page_no,
        "image_sha256": key.image_sha256,
        "model": key.model,
        "prompt_version": key.prompt_version,
    }


class PgPageReads:
    """The page cache, each call in its own short transaction as the key's customer."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: PageKey) -> tuple[dict[str, Any], int, int] | None:
        """The cached read for ``key`` as (result, input tokens, output tokens), or None
        when the page was not read with this image, model and prompt version. Another
        customer's row is never found (row level security and the customer filter)."""
        with unavailable(), self._db.customer(key.customer_id) as conn:
            row = conn.execute(_GET, _params(key)).first()
        if row is None:
            return None
        result, input_tokens, output_tokens = row
        return dict(result), int(input_tokens or 0), int(output_tokens or 0)

    def put(self, key: PageKey, result: dict[str, Any], input_tokens: int, output_tokens: int) -> None:
        """Save one page's read at once, replacing an earlier read with the same key
        (the upsert). Raises ValueError, and writes nothing, for a negative token count;
        the database refuses a ``result`` that is not a JSON object, and a document that
        is not this customer's."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts are never negative")
        params = {
            **_params(key),
            "result": json.dumps(result),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with unavailable(), self._db.customer(key.customer_id) as conn:
            conn.execute(_PUT, params)
