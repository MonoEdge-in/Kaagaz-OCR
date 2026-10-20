"""The Ensemble job (scan review UX rules, Tushar, 16 Oct 2026, section 5; the council plan
of 16 Oct 2026, PR 5): one run of ``ensemble_runs``, read page by page.

``EnsembleRuns`` is the worker's handler for a job with a ``run_id`` (kaagaz/worker_main.py):

* The run is claimed (``ensemble.claim``); one already done, stopped or failed is a
  duplicate delivery, acked with nothing read. Its models are checked again now
  (``models.resolve_ensemble``, S2): no longer allowed, the run fails ``ensemble_not_allowed``.
* The stored file must still hash to the document's sha256 and the document be ready,
  current and out of Trash, else the run stops (``document_changed``), finished pages kept.
* Every model call is billed to the person who asked, as ``ensemble_scan`` (SP1).
* Each page not done yet, in order: a Stop asked for ends the run (finished pages stay);
  the page is drawn again losslessly (``preparing.pages.page_picture``); a run on words
  reads a crop of each word's spot (a word mark's line, shared by the marks on it), grown by
  CHECK_MARGIN as the checker's are (``pipeline._check_crop``); a run on the whole document
  reads the page. Each other model reads them through the scan reader (``reading_document``
  and ``read_page``: its page cache, pinning and outage rules), one model after the other.
  ponytail: one model after the other; both at once halves the wait if runs feel slow.
* A model's reading of a word is its text there, its words lined up with the stored text by
  ``aligned`` (difflib); one that read nothing there, or marked a word of it unsure or
  unreadable, has no reading but is counted. The vote (``ensemble.settle``) is the stored
  text and the other models, 2 of 3.
* A word no other model could read (an outage, a refusal, a timeout), or a key the reader
  refuses, fails the run with that code at its page; the pages left are the retry's. A page
  is saved in one transaction (``ensemble.save_page``).
* A database or storage outage goes up (BackendUnavailable): the worker pauses and the job
  comes again; the run carries on from the pages saved.

Logs hold ids and codes only, never a word.
"""

import difflib
import hashlib
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from PIL import Image
from sqlalchemy import RowMapping

from kaagaz.ai.usage import usage_scope
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db import models
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.page_reads import PgPageReads
from kaagaz.db.word_review import Refused
from kaagaz.extraction.labels import VOTE_INSIDE, inside
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import BlobNotFound, BlobStorage, Job
from kaagaz.jobs.worker import KEY_PROBLEM, RETRIES_EXHAUSTED, Heartbeat
from kaagaz.pipeline import _check_crop
from kaagaz.preparing.pages import page_picture
from kaagaz.reading.ocr import (
    DocumentPages,
    OcrEngine,
    PageEngine,
    as_page_engine,
    is_word_mark,
    ocr_spans,
    reading_document,
)
from kaagaz.reading.page_quality import ILLEGIBLE_MARK, READ_LOCALLY
from kaagaz.reading.spans import BoxLocation, Span
from kaagaz.settings import Settings

logger = logging.getLogger("kaagaz.worker")

DOCUMENT_CHANGED = "document_changed"
TIMEOUT, OUTAGE, REFUSED, KEY = "timeout", "outage", "refused", "key_problem"
WHOLE_PAGE = ""  # the image key of a whole page (a run on the document)


class _Failed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Reading:
    """What one delivery reads with: its job, the scan reader, who reads again, the file
    and the heartbeat."""

    job: Job
    engine: OcrEngine
    pages: PageEngine
    reading: models.Reading
    detected_type: str
    data: bytes
    heartbeat: Heartbeat


def aligned(stored: str, read: str, words: tuple[int, int]) -> str | None:
    """The words of ``read`` that line up with ``stored``'s words ``words`` (start, end):
    difflib's blocks over the words compared without case or end punctuation; a replaced run
    that touches them is taken whole, words read inside them kept, words read before or after
    left out. None when nothing lines up. O(n x m) words at worst."""
    first, second = stored.split(), read.split()
    start, end = words
    matcher = difflib.SequenceMatcher(
        None, [ensemble_sql.plain(word) for word in first], [ensemble_sql.plain(word) for word in second],
        autojunk=False,
    )  # fmt: skip
    taken: list[int] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            low, high = max(i1, start), min(i2, end)
            taken.extend(range(j1 + low - i1, j1 + high - i1))
        elif (tag == "replace" and i1 < end and i2 > start) or (tag == "insert" and start < i1 < end):
            taken.extend(range(j1, j2))
    return " ".join(second[index] for index in taken) or None


def reading_of(spans: Sequence[Span], box: BoxLocation | None, target: ensemble_sql.Target) -> str | None:
    """What one model read at ``target``: its lines (inside ``box`` by VOTE_INSIDE of their
    own box, or every line of a crop) joined, lined up with the target's words. None when it
    read nothing there, or marked a word of it unsure or unreadable (no vote)."""
    here = [
        span
        for span in spans
        if box is None
        or (isinstance(span.location, BoxLocation) and span.location.page == box.page
            and inside(span.location, box) >= VOTE_INSIDE)
    ]  # fmt: skip
    lines = [span.text for span in here if not is_word_mark(span)]
    words = aligned(target.spot_text, " ".join(lines), target.words) if lines else None
    if words is None or ILLEGIBLE_MARK in words:
        return None
    unsure = {ensemble_sql.plain(span.text) for span in here if is_word_mark(span)}
    return None if unsure & {ensemble_sql.plain(word) for word in words.split()} else words


class EnsembleRuns:
    """The worker's handler for Ensemble runs (the module notes). ``engine`` is the scan
    reader (None: scans are not read by a model, and every run fails ensemble_not_allowed)."""

    def __init__(
        self,
        db: Database,
        storage: BlobStorage,
        engine: OcrEngine | None,
        *,
        settings: Settings,
        clock: Callable[[], datetime],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db = db
        self._storage = storage
        self._engine = engine
        self._settings = settings
        self._clock = clock
        self._monotonic = monotonic
        self._cache = PgPageReads(db)

    def __call__(self, job: Job, heartbeat: Heartbeat) -> None:
        customer_id, document_id, run_id = job.customer_id, job.document_id, str(job.run_id)
        ids = {"customer_id": customer_id, "document_id": document_id}
        engine = self._engine
        with unavailable(), self._db.customer(customer_id) as conn:
            run = ensemble_sql.claim(conn, customer_id, document_id, run_id, now=self._clock())
            if run is None:
                logger.info("duplicate delivery skipped", extra={**ids, "code": "ensemble_settled"})
                return
            try:
                if engine is None:
                    raise models.AiNotAllowed("ensemble_not_allowed")
                reading = models.resolve_ensemble(conn, customer_id, document_id, settings=self._settings)
            except models.AiNotAllowed as refused:
                ensemble_sql.finish(conn, customer_id, run_id, "failed", refused.code, now=self._clock())
                return
            file = ensemble_sql.document_file(conn, customer_id, document_id)
        data = None
        if file is not None and file["ready"]:
            try:
                data = self._storage.get(customer_id, document_id)
            except BlobNotFound:
                data = None
        if file is None or data is None or hashlib.sha256(data).hexdigest() != file["sha256"]:
            self._end(customer_id, run_id, "stopped", DOCUMENT_CHANGED)
            return
        read = _Reading(
            job, engine, as_page_engine(engine), reading, str(file["detected_type"]), data, heartbeat
        )
        user = str(run["requested_by"])
        with usage_scope(
            self._db, customer_id=customer_id, user_id=user, kind="ensemble_scan", document_id=document_id
        ):
            status, code = self._pages(read, run)
        self._end(customer_id, run_id, status, code)
        logger.info("ensemble run ended", extra={**ids, "outcome": status, "code": code or status})

    def record_attempt(self, customer_id: str, run_id: str, attempt: int) -> None:
        """The worker's try number, on the run (never the document)."""
        with unavailable(), self._db.customer(customer_id) as conn:
            ensemble_sql.record_attempt(conn, customer_id, run_id, attempt)

    def exhausted(self, customer_id: str, run_id: str) -> None:
        """The job's last delivery came: the run fails ``retries_exhausted``; the document
        is left as it is (ready)."""
        self._end(customer_id, run_id, "failed", RETRIES_EXHAUSTED)

    def _end(self, customer_id: str, run_id: str, status: str, code: str | None) -> None:
        with unavailable(), self._db.customer(customer_id) as conn:
            ensemble_sql.finish(conn, customer_id, run_id, status, code, now=self._clock())

    def _pages(self, read: _Reading, run: RowMapping) -> tuple[str, str | None]:
        """Read and save each page not done yet; (the run's status, its code)."""
        customer_id, document_id, run_id = read.job.customer_id, read.job.document_id, str(read.job.run_id)
        done = set(run["pages_done"])
        for page_no in run["pages"]:
            if page_no in done:
                continue
            with unavailable(), self._db.customer(customer_id) as conn:
                if ensemble_sql.stop_asked(conn, customer_id, run_id):
                    return "stopped", None
                found = ensemble_sql.targets(
                    conn, customer_id, document_id, pages={page_no}, word_keys=run["word_keys"]
                ).get(page_no, [])
            read.heartbeat()
            try:
                outcomes = self._read_page(read, page_no, found, whole=run["scope"] == "document")
            except _Failed as failed:
                return "failed", failed.code
            try:
                with unavailable(), self._db.customer(customer_id) as conn:
                    ensemble_sql.save_page(
                        conn, customer_id, document_id, run_id, page_no, outcomes, now=self._clock()
                    )
            except Refused:
                return "stopped", DOCUMENT_CHANGED
        return "done", None

    def _read_page(
        self, read: _Reading, page_no: int, found: Sequence[ensemble_sql.Target], *, whole: bool
    ) -> list[ensemble_sql.Outcome]:
        """One page's outcomes; raises _Failed when a word on it has no other model's reading."""
        skipped = [
            ensemble_sql.Outcome(target, "skipped_user_edit", None, []) for target in found if target.person
        ]
        wanted = [(target, ensemble_sql.box_of(target.spot)) for target in found if not target.person]
        boxed = [(target, box) for target, box in wanted if box is not None]
        if not boxed:
            return skipped
        settings = self._settings
        with page_picture(
            read.detected_type, read.data, page_no, dpi=settings.page_image_dpi,
            max_image_pixels=settings.max_image_pixels,
        ) as page:  # fmt: skip
            images = (
                {WHOLE_PAGE: page}
                if whole
                else {_spot_key(t.spot): _check_crop(page, box) for t, box in boxed}
            )
            reads = [
                (agent.model, *self._read(read, agent, images, page_no)) for agent in read.reading.others
            ]
        outcomes = []
        for target, box in boxed:
            name = WHOLE_PAGE if whole else _spot_key(target.spot)
            votes: list[tuple[str, str | None]] = [(read.reading.lead.model, target.text)]
            votes += [
                (model, reading_of(spans[name] or (), box if whole else None, target))
                for model, spans, _code in reads
                if spans[name] is not None
            ]
            if len(votes) == 1:  # no other model read it: the page is the retry's
                raise _Failed(next((code for _model, _spans, code in reads if code), REFUSED))
            outcomes.append(ensemble_sql.settle(target, votes))
        return skipped + outcomes

    def _read(
        self, read: _Reading, agent: models.Agent, images: dict[str, Image.Image], page_no: int
    ) -> tuple[dict[str, list[Span] | None], str | None]:
        """Each image as ``agent`` read it (None where it could not) and why it could not."""
        job = read.job
        pages = DocumentPages(
            customer_id=job.customer_id,
            document_id=job.document_id,
            cache=self._cache,
            model=agent.model,
            provider=agent.provider,
            plain=frozenset() if agent.thinks else frozenset({agent.model}),  # no reasoning field
        )
        read_by: dict[str, list[Span] | None] = dict.fromkeys(images)
        code = None
        with reading_document(read.engine, pages):
            for name, image in images.items():
                started = self._monotonic()
                try:
                    page = read.pages.read_page(image, page_no=page_no)
                except BackendUnavailable as error:
                    if str(error) == KEY_PROBLEM:
                        raise _Failed(KEY) from None
                    return read_by, OUTAGE  # its other images wait for the retry
                read.heartbeat()
                if page.read_by == agent.model:
                    read_by[name] = ocr_spans(list(page.lines), page_no, image.width, image.height)
                elif code is None:
                    timed_out = self._monotonic() - started >= self._settings.ocr_timeout_seconds
                    code = OUTAGE if READ_LOCALLY in page.issues else TIMEOUT if timed_out else REFUSED
        return read_by, code


def _spot_key(spot: object) -> str:
    return json.dumps(spot, sort_keys=True)
