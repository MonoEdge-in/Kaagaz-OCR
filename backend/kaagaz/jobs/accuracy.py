"""An operator's accuracy check against max settings (#463, Tushar, 31 Oct 2026; 0064).

``AccuracyChecks`` is the worker's handler for a job with a ``check_id`` (kaagaz/worker_main.py):

* The organisation's running check is claimed (``kaagaz_accuracy_check_claim``: its own only,
  by the connection's customer); one already ended is a duplicate delivery, acked with nothing
  read. No scan reader (OCR_ENGINE is not gemini), or a lead that is no longer the check's
  model: the check fails.
* Up to the check's ``max_pages`` of the organisation's most recently read pages that a model
  read and whose picture is kept (``pages.has_image``) are each read twice through the scan
  reader, from the stored picture: at the check's settings, the way a real scan reads them
  (``models.scope_reading``: Dynamic's first read and Careful when Dynamic is on), and at max
  settings (the same lead, single, its highest thinking level, high picture detail). Never from
  the page cache, and nothing is cached: a check reads afresh and changes no document.
* The words compared: whitespace normalised, the word error rate of the check's read against
  the max read (``word_errors``, a word-level edit distance), every page's words together, as
  ``word_match`` = 1 - WER (never below 0). The numbers (council, 31 Oct 2026: a wrong amount
  is one word among hundreds): tokens with a digit, "," and a ₹ or Rs prefix taken off, and the
  share of the max read's that the check's read has as often (``numbers_matched``, a multiset,
  so lines read in another order lose nothing); ``numbers_match`` null with no number.
* A page whose picture is gone or that a model did not read both ways (an outage, a refusal,
  the local reader) is skipped and counted (``pages_failed``).
* Every call is billed to the organisation as ``accuracy_check`` (Careful's too:
  ``kaagaz.ai.usage``), never its budget; ``cost_usd`` is OpenRouter's reported cost of the
  check's calls, null when one came without it.
* Only numbers are stored (``kaagaz_accuracy_check_finish``) and logged: never a word read.

Time: O(pages x (two reads + w x v)) for w and v words a page (the edit distance).
"""

import io
import logging
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Protocol

from PIL import Image
from sqlalchemy import text

from kaagaz.ai.usage import ACCURACY_CHECK, usage_scope
from kaagaz.db import models
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.page_reads import PageKey
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound, Job
from kaagaz.jobs.worker import Heartbeat
from kaagaz.reading.ocr import DocumentPages, OcrEngine, PageRead, as_page_engine, reading_document
from kaagaz.settings import Settings

logger = logging.getLogger("kaagaz.worker")

_CLAIM = text("SELECT * FROM kaagaz_accuracy_check_claim(:k)")
_FINISH = text("SELECT kaagaz_accuracy_check_finish(:k, :s, :n, :f, :w, :x, CAST(:cost AS numeric))")
# The organisation's pages a model read (its id has a "/") with their picture kept, of current,
# ready documents out of Trash, the most recently read first (their document's last run).
_PAGES = text(
    "SELECT p.document_id, p.page_no FROM pages p"
    " JOIN documents d ON d.customer_id = p.customer_id AND d.id = p.document_id"
    " WHERE p.customer_id = :c AND p.has_image AND p.route = 'ocr' AND p.read_by LIKE '%/%'"
    " AND d.deleted_at IS NULL AND d.status = 'ready'"
    " ORDER BY (SELECT max(r.started_at) FROM processing_runs r"
    " WHERE r.customer_id = p.customer_id AND r.document_id = p.document_id) DESC NULLS LAST,"
    " p.document_id, p.page_no"
    " LIMIT :n"
)
# A number's ₹ or Rs prefix (the council's normalisation).
_PREFIX = re.compile(r"^(?:₹|rs\.?)", re.IGNORECASE)


class PageImages(Protocol):
    """The stored page pictures (``S3Storage.get_page_image``)."""

    def get_page_image(self, customer_id: str, document_id: str, page_no: int) -> bytes: ...


class _NoCache:
    """A page cache that keeps nothing: a check reads afresh and leaves no read behind."""

    def get(self, key: PageKey) -> tuple[dict[str, Any], int, int] | None:
        return None

    def put(self, key: PageKey, result: dict[str, Any], input_tokens: int, output_tokens: int) -> None:
        return None


def words(read: PageRead) -> list[str]:
    """The page's words, whitespace normalised (split on any run of it)."""
    return " ".join(line.text for line in read.lines).split()


def word_errors(reference: Sequence[str], read: Sequence[str]) -> int:
    """The fewest words substituted, left out or added to turn ``read`` into ``reference``
    (Levenshtein over words). O(n x m) time, O(m) space."""
    previous = list(range(len(read) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, other in enumerate(read, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1]


def numbers(tokens: Sequence[str]) -> Counter[str]:
    """Each token with a digit, its ₹ or Rs prefix and its "," taken off, counted. O(tokens)."""
    return Counter(
        _PREFIX.sub("", token).replace(",", "") for token in tokens if any(c.isdigit() for c in token)
    )


def numbers_matched(reference: Sequence[str], read: Sequence[str]) -> tuple[int, int]:
    """(the reference's numbers ``read`` has as often, the reference's numbers). O(tokens)."""
    wanted, found = numbers(reference), numbers(read)
    return sum(min(count, found[number]) for number, count in wanted.items()), sum(wanted.values())


class AccuracyChecks:
    """The worker's handler for accuracy checks (the module notes). ``engine`` is the scan
    reader (None: scans are not read by a model, and every check fails)."""

    def __init__(
        self, db: Database, storage: PageImages, engine: OcrEngine | None, *, settings: Settings
    ) -> None:
        self._db = db
        self._storage = storage
        self._engine = engine
        self._settings = settings

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        customer_id, check_id = job.customer_id, str(job.check_id)
        ids = {"customer_id": customer_id, "document_id": check_id}
        with unavailable(), self._db.customer(customer_id) as conn:
            claimed = conn.execute(_CLAIM, {"k": check_id}).mappings().first()
            if claimed is None:
                logger.info("duplicate delivery skipped", extra={**ids, "code": "check_settled"})
                return
            rows = models.usable_models(conn, customer_id, settings=self._settings)
            allowed = dict(models.effective_settings(conn, customer_id))
            picked = conn.execute(_PAGES, {"c": customer_id, "n": claimed["max_pages"]}).all()
        levels = ("scan_thinking", "scan_resolution", "careful_thinking", "careful_resolution")
        draft = models.scope_reading(
            rows, {**allowed, **{name: claimed[name] for name in levels}}, settings=self._settings
        )
        engine = self._engine
        if engine is None or draft.lead.model != claimed["model"]:
            self._finish(customer_id, check_id, "failed")
            logger.info(
                "accuracy check ended", extra={**ids, "outcome": "failed", "code": "check_unreadable"}
            )
            return
        lead_row = next((row for row in rows if row["id"] == draft.lead.model), None)
        best = models.Reading(
            "single", draft.lead, thinking=models.max_thinking(lead_row), resolution=models.MAX_RESOLUTION
        )
        checked = failed = errors = reference_words = matched = reference_numbers = 0
        with usage_scope(self._db, customer_id=customer_id, user_id=None, kind=ACCURACY_CHECK) as scope:
            for document_id, page_no in picked:
                heartbeat()
                image = self._picture(customer_id, str(document_id), int(page_no))
                at = (customer_id, str(document_id), int(page_no))
                draft_read = None if image is None else _read(engine, draft, *at, image)
                max_read = None if image is None or draft_read is None else _read(engine, best, *at, image)
                if draft_read is None or max_read is None:
                    failed += 1
                    continue
                reference, read = words(max_read), words(draft_read)
                errors += word_errors(reference, read)
                reference_words += len(reference)
                found, wanted = numbers_matched(reference, read)
                matched, reference_numbers = matched + found, reference_numbers + wanted
                checked += 1
            costs = [row.cost_usd for row in scope.rows()]
        cost = None if any(value is None for value in costs) else sum(value or 0.0 for value in costs)
        word_match = None if not reference_words else max(0.0, 1 - errors / reference_words)
        numbers_match = None if not reference_numbers else matched / reference_numbers
        self._finish(customer_id, check_id, "done", checked, failed, word_match, numbers_match, cost)
        logger.info("accuracy check ended", extra={**ids, "outcome": "done", "code": "done"})

    def exhausted(self, customer_id: str, check_id: str) -> None:
        """The job's last delivery came: the check fails."""
        self._finish(customer_id, check_id, "failed")

    def _finish(
        self,
        customer_id: str,
        check_id: str,
        status: str,
        checked: int = 0,
        failed: int = 0,
        word_match: float | None = None,
        numbers_match: float | None = None,
        cost: float | None = None,
    ) -> None:
        params = {
            "k": check_id,
            "s": status,
            "n": checked,
            "f": failed,
            "w": word_match,
            "x": numbers_match,
            "cost": cost,
        }
        with unavailable(), self._db.customer(customer_id) as conn:
            conn.execute(_FINISH, params)

    def _picture(self, customer_id: str, document_id: str, page_no: int) -> Image.Image | None:
        """The page's stored picture in RGB, or None when it is gone."""
        try:
            data = self._storage.get_page_image(customer_id, document_id, page_no)
        except BlobNotFound:
            return None
        with Image.open(io.BytesIO(data)) as stored:
            return stored.convert("RGB")


def _read(
    engine: OcrEngine,
    reading: models.Reading,
    customer_id: str,
    document_id: str,
    page_no: int,
    image: Image.Image,
) -> PageRead | None:
    """The page as ``reading`` reads it through the scan reader, or None when a model of it did
    not read it (an outage, a refusal, the local reader)."""
    pages = document_pages(reading, customer_id, document_id)
    try:
        with reading_document(engine, pages):
            read = as_page_engine(engine).read_page(image, page_no=page_no)
    except BackendUnavailable:
        return None
    readers = {reading.lead.model} | ({reading.careful.model} if reading.careful is not None else set())
    return read if read.read_by in readers else None


def document_pages(reading: models.Reading, customer_id: str, document_id: str) -> DocumentPages:
    """One page's ``DocumentPages`` for ``reading``, as the pipeline gives a one-page document
    (``pipeline._reading_document``): Dynamic's Careful on the page's own thread
    (``inline_careful``) when it has one; no cache and nothing read again."""
    careful = reading.careful
    return DocumentPages(
        customer_id=customer_id,
        document_id=document_id,
        cache=_NoCache(),
        model=reading.lead.model,
        provider=reading.lead.provider,
        thinking=reading.thinking,
        resolution=reading.resolution,
        careful_thinking=reading.careful_thinking,
        careful_resolution=reading.careful_resolution,
        careful=None if careful is None else (careful.model, careful.provider),
        memo=None if careful is None else {},
        inline_careful=careful is not None,
        page_count=1,
        escalated=None if careful is None else {},
        plain=frozenset(
            agent.model for agent in (reading.lead, careful) if agent is not None and not agent.thinks
        ),
    )
