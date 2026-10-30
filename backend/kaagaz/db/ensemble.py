"""Ensemble after the scan (scan review UX rules, Tushar, 16 Oct 2026, section 5; the council
plan of 16 Oct 2026, PRs 5 and 6): its runs and readings, and a person's Keep, Revert and
Keep all.

Why here: the SQL in one reviewable place, as kaagaz/db/word_review.py. Every function takes
a connection from ``Database.customer(customer_id)`` and filters on ``customer_id`` as well.

* **What a run reads** (``plan``, ``targets``): a run on one word, some words or the flagged
  words (those still open) keeps their keys (``word_keys``, 0032), at most MAX_WORDS, the
  Review band first, then page order, and reads those still flagged when it gets to their
  page. A run on the whole document reads every flag and every line of the first reader on
  its scanned pages, at most MAX_DOCUMENT_PAGES of them. A word mark is read on its line.
* **Starting** (``start``, ``retry``) under the family lock, so a read again never
  interleaves: the document ready, current and out of Trash; one queued or running run per
  document (the same request again gets that run back, any other is ``ensemble_running``);
  at most RUNS_PER_HOUR runs an hour per customer, counted under the customer's advisory
  lock. A retry reads the pages its run did not finish. The caller queues the job last, in
  the same transaction, so a queue that is down changes nothing.
* **The job's writes** (``claim``, ``save_page``, ``finish``): one page is one transaction
  under the family lock (``save_page``): each reading inserted once per run and word (ON
  CONFLICT DO NOTHING, so a page delivered again writes nothing twice), an agreed flagged
  word's ``resolve`` change, then the page in ``pages_done``. A word whose latest change in
  force is a person's is ``skipped_user_edit``: user edits always win.
* **Keep, Revert, Keep all**: Keep is ``word_review.change_word`` with the reading, written
  into values and pieces like an edit but never Verifying; 409 ``changed_since`` when a
  person changed the word since, or its text is no longer the reading's ``before_text``.
  Revert marks the reading; the word stays flagged with every reading as an option.

Audit rows hold ids, codes and counts only, never a word.
"""

import json
import string
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db import audit, word_review
from kaagaz.db.word_review import Refused
from kaagaz.extraction.flags import LINE, MARK, READINGS_DIFFER, REVIEW, Flag, overlaps, word_key
from kaagaz.extraction.labels import majority
from kaagaz.reading.spans import BoxLocation, location_from_json

SCOPES = ("word", "words", "flagged", "document")
ACTIVE = ("queued", "running")
MAX_WORDS = 200
MAX_DOCUMENT_PAGES = 50
RUNS_PER_HOUR = 30
OTHERS = 2  # the models that read again (models.resolve_ensemble)
# ponytail: a fixed guess a model read takes, for the estimate; the ledger's own times if it misleads.
SECONDS_PER_READ = 8
_EDGES = string.punctuation + "\u0964\u0965"  # Latin and Devanagari punctuation at a word's ends

# A run's row is every column of ensemble_runs (0030, 0032).
_RUN = text("SELECT * FROM ensemble_runs WHERE customer_id = :c AND document_id = :d AND id = :r")
_LATEST = text(
    "SELECT * FROM ensemble_runs WHERE customer_id = :c AND document_id = :d"
    " ORDER BY created_at DESC, id DESC LIMIT 1"
)
_ACTIVE_RUN = text(
    "SELECT * FROM ensemble_runs WHERE customer_id = :c AND document_id = :d"
    " AND status IN ('queued', 'running')"
)
# One customer's runs are counted one request after the other, so two at once can't both pass.
_RUNS_LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtextextended('kaagaz.ensemble:' || CAST(:c AS uuid)::text, 0))"
)
# ponytail: reads the customer's runs of every document (the index's first column); a
# (customer_id, created_at) index if one customer makes thousands of runs.
_RUNS_THIS_HOUR = text(
    "SELECT count(*) FROM ensemble_runs WHERE customer_id = :c AND created_at > now() - interval '1 hour'"
)
_INSERT_RUN = text(
    "INSERT INTO ensemble_runs (customer_id, document_id, scope, pages, models, requested_by, retry_of,"
    " word_keys) VALUES (:c, :d, :scope, CAST(:pages AS integer[]), CAST(:models AS text[]), :u, :retry_of,"
    " CAST(:keys AS text[])) RETURNING *"
)
# A queued run stops at once; a running one when its worker next looks (between pages).
_STOP = text(
    "UPDATE ensemble_runs SET stop_requested_at = coalesce(stop_requested_at, :now),"
    " status = CASE WHEN status = 'queued' THEN 'stopped' ELSE status END,"
    " finished_at = CASE WHEN status = 'queued' THEN CAST(:now AS timestamptz) ELSE finished_at END"
    " WHERE customer_id = :c AND document_id = :d AND id = :r AND status IN ('queued', 'running')"
    " RETURNING id"
)
_CLAIM = text(
    "UPDATE ensemble_runs SET status = 'running', started_at = coalesce(started_at, :now)"
    " WHERE customer_id = :c AND document_id = :d AND id = :r AND status IN ('queued', 'running')"
    " RETURNING *"
)
_STOP_ASKED = text(
    "SELECT stop_requested_at IS NOT NULL FROM ensemble_runs WHERE customer_id = :c AND id = :r"
)
_ATTEMPT = text(
    "UPDATE ensemble_runs SET attempts = greatest(attempts, :a) WHERE customer_id = :c AND id = :r"
)
# With a code, the pages not done are the ones a retry reads (pages_failed).
_FINISH = text(
    "UPDATE ensemble_runs SET status = :status, error_code = :code, finished_at = :now,"
    " pages_failed = CASE WHEN CAST(:code AS text) IS NULL THEN CAST('{}' AS integer[])"
    " ELSE ARRAY(SELECT p FROM unnest(pages) AS p WHERE NOT p = ANY (pages_done) ORDER BY p) END"
    " WHERE customer_id = :c AND id = :r AND status IN ('queued', 'running')"
)
_PAGE_DONE = text(
    "UPDATE ensemble_runs SET pages_done = array_append(pages_done, CAST(:p AS integer))"
    " WHERE customer_id = :c AND id = :r AND NOT CAST(:p AS integer) = ANY (pages_done)"
)
_FILE = text(
    "SELECT sha256, detected_type, status = 'ready' AND is_current AND deleted_at IS NULL AS ready"
    " FROM documents WHERE customer_id = :c AND id = :d"
)
_SCANNED = text(
    "SELECT page_no FROM pages WHERE customer_id = :c AND document_id = :d AND route = 'ocr' ORDER BY page_no"
)
# The first reader's OCR lines of some pages (word marks left out), in reading order.
_LINES = text(
    "SELECT CAST(e.span -> 'region' ->> 'page' AS integer) AS page_no, e.span ->> 'text' AS text,"
    " e.span -> 'region' AS region"
    " FROM reader_outputs ro"
    " CROSS JOIN LATERAL jsonb_array_elements(ro.output) WITH ORDINALITY AS e(span, ord)"
    " WHERE ro.customer_id = :c AND ro.document_id = :d AND ro.role = 'first'"
    " AND e.span ->> 'origin' = 'ocr' AND e.span -> 'region' ->> 'kind' = 'box'"
    " AND e.span ->> 'kind' IS NOT NULL AND btrim(e.span ->> 'text') <> ''"
    " AND CAST(e.span -> 'region' ->> 'page' AS integer) = ANY (CAST(:pages AS integer[]))"
    " ORDER BY e.ord"
)
_INSERT_READING = text(
    "INSERT INTO ensemble_readings (customer_id, document_id, run_id, word_key, page_no, region, before_text,"
    " outcome, proposal, reads) VALUES (:c, :d, :r, :key, :page_no, CAST(:region AS jsonb), :before,"
    " :outcome, :proposal, CAST(:reads AS jsonb)) ON CONFLICT (run_id, word_key) DO NOTHING RETURNING id"
)
_READING = text(
    "SELECT id, run_id, word_key, before_text, outcome, proposal, reads, decision FROM ensemble_readings"
    " WHERE customer_id = :c AND document_id = :d AND id = :id FOR UPDATE"
)
_DECIDE = text(
    "UPDATE ensemble_readings SET decision = :decision, decided_by = :u, decided_at = :now"
    " WHERE customer_id = :c AND id = :id"
)
_PENDING = text(
    "SELECT id FROM ensemble_readings WHERE customer_id = :c AND document_id = :d AND run_id = :r"
    " AND outcome = 'changed' AND decision IS NULL ORDER BY page_no, id"
)
_COUNTS = text(
    "SELECT outcome, decision, count(*) AS n FROM ensemble_readings WHERE customer_id = :c AND run_id = :r"
    " GROUP BY outcome, decision"
)
# Each word's latest reading, for its flag's state and readings (any run of the document).
_LATEST_READINGS = text(
    "SELECT DISTINCT ON (word_key) id, run_id, word_key, outcome, proposal, reads, decision"
    " FROM ensemble_readings WHERE customer_id = :c AND document_id = :d"
    " ORDER BY word_key, created_at DESC, id DESC"
)


@dataclass(frozen=True, slots=True)
class Plan:
    """What a new run reads: its pages, its words' keys (None: the whole document) and how
    many words that is."""

    pages: list[int]
    word_keys: list[str] | None
    words: int


@dataclass(frozen=True, slots=True)
class Target:
    """One word a run reads: its flag (a line of the whole document that nothing flags is a
    line flag too, ``flagged`` False), its text now, whether a person changed it (``person``:
    skipped), and the spot read again with the words of it compared: its own box and every
    word, or a word mark's line (``spot_text``) and the mark's words in it (``words``, a
    token range)."""

    flag: Flag
    text: str
    flagged: bool
    person: bool
    spot: Mapping[str, Any]
    spot_text: str
    words: tuple[int, int]


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the readings of one target came to (``ensemble_readings``)."""

    target: Target
    outcome: str
    proposal: str | None
    reads: list[dict[str, str | None]]


def plan(conn: Connection, customer_id: str, document_id: str, scope: str, keys: Sequence[str]) -> Plan:
    """The run ``scope`` would make now (the module notes). Raises Refused: no_pages,
    too_many_pages, not_found (a key that is not a flag now), no_words."""
    ids = {"c": customer_id, "d": document_id}
    if scope == "document":
        pages: list[int] = list(conn.execute(_SCANNED, ids).scalars())
        if not pages:
            raise Refused("no_pages")
        if len(pages) > MAX_DOCUMENT_PAGES:
            raise Refused("too_many_pages")
        found = targets(conn, customer_id, document_id, pages=pages, word_keys=None)
        return Plan(pages, None, sum(map(len, found.values())))
    review = word_review.load(conn, customer_id, document_id)
    if scope == "flagged":
        latest = review.latest()
        chosen = [flag for flag in review.listed if word_review.state(flag, latest) == word_review.OPEN]
    else:
        by_key = {flag.key: flag for flag in review.flags}
        if any(key not in by_key for key in keys):
            raise Refused("not_found")
        chosen = [by_key[key] for key in dict.fromkeys(keys)]
    if not chosen:
        raise Refused("no_words")
    chosen = sorted(chosen, key=lambda flag: (flag.band != REVIEW, flag.page_no))[:MAX_WORDS]
    return Plan(sorted({flag.page_no for flag in chosen}), [flag.key for flag in chosen], len(chosen))


def estimate(
    conn: Connection, customer_id: str, document_id: str, scope: str, keys: Sequence[str]
) -> dict[str, int]:
    """``{pages, words, seconds}`` of the run ``scope`` would make: one read a page (the whole
    document) or a word, by each other model, one after the other."""
    planned = plan(conn, customer_id, document_id, scope, keys)
    reads = len(planned.pages) if scope == "document" else planned.words
    return {"pages": len(planned.pages), "words": planned.words, "seconds": reads * OTHERS * SECONDS_PER_READ}


def targets(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    pages: Collection[int],
    word_keys: Sequence[str] | None,
) -> dict[int, list[Target]]:
    """The words a run reads on ``pages`` now, by page: those of ``word_keys`` still flagged,
    or with None every flag and line of the pages. Two statements and the flags' (``load``)."""
    review = word_review.load(conn, customer_id, document_id)
    latest = review.latest()
    params = {"c": customer_id, "d": document_id, "pages": sorted(pages)}
    lines = conn.execute(_LINES, params).mappings().all()
    flagged = {flag.key for flag in review.flags}
    wanted = None if word_keys is None else set(word_keys)
    chosen = {
        flag.key: flag
        for flag in review.flags
        if flag.page_no in pages and (wanted is None or flag.key in wanted)
    }
    if wanted is None:
        for line in lines:
            page_no, region, said = int(line["page_no"]), line["region"], str(line["text"])
            key = word_key(LINE, page_no, region, said)
            chosen.setdefault(
                key, Flag(key, LINE, page_no, region, said, said, REVIEW, READINGS_DIFFER, None)
            )
    found: dict[int, list[Target]] = {}
    for flag in chosen.values():
        change = latest.get(flag.key)
        now = word_review.current_text(flag, latest)
        spot, spot_text, words = _spot(flag, now, lines)
        person = change is not None and change["user_id"] is not None
        target = Target(flag, now, flag.key in flagged, person, spot, spot_text, words)
        found.setdefault(flag.page_no, []).append(target)
    return found


def _spot(
    flag: Flag, now: str, lines: Sequence[RowMapping]
) -> tuple[Mapping[str, Any], str, tuple[int, int]]:
    """Where a word is read again, and which words there are compared (``Target``)."""
    if flag.kind == MARK:
        line = next(
            (one for one in lines if one["page_no"] == flag.page_no and overlaps(one["region"], flag.region)),
            None,
        )
        mark, box = box_of(flag.region), None if line is None else box_of(line["region"])
        if line is not None and mark is not None and box is not None and box.x1 > box.x0:
            share = (mark.x0 - box.x0) / (box.x1 - box.x0)
            start = word_at(str(line["text"]), flag.as_read, share)
            if start is not None:
                return line["region"], str(line["text"]), (start, start + len(flag.as_read.split()))
    return flag.region, now, (0, len(now.split()))


def word_at(line: str, word: str, share: float) -> int | None:
    """The token where ``word``'s tokens start in ``line``, the place nearest ``share`` of the
    line's length when it is there more than once (a mark's box is placed by its characters'
    position, ocr._word_marks); compared without case or end punctuation. None when absent."""
    tokens, wanted = [plain(token) for token in line.split()], [plain(token) for token in word.split()]
    size = len(wanted)
    starts = [
        index for index in range(len(tokens) - size + 1) if size and tokens[index : index + size] == wanted
    ]
    if not starts:
        return None
    lengths = [len(token) + 1 for token in line.split()]
    return min(starts, key=lambda index: abs(sum(lengths[:index]) - share * len(line)))


def plain(token: str) -> str:
    """A word compared without case or the punctuation at its ends."""
    return token.casefold().strip(_EDGES)


def box_of(region: Mapping[str, Any]) -> BoxLocation | None:
    try:
        location = location_from_json(dict(region))
    except (KeyError, TypeError, ValueError):
        return None
    return location if isinstance(location, BoxLocation) else None


def settle(target: Target, votes: Sequence[tuple[str, str | None]]) -> Outcome:
    """The vote on one word (``labels.majority``, 2 of 3): ``votes`` the main reader's (its
    text now) first, then each other model that read it. Agreed when the majority is the
    text now, changed (the majority's text, a proposal) when it is another, else unresolved."""
    result = majority(votes)
    winner = result["value"]
    if winner is None:
        return Outcome(target, "unresolved", None, result["reads"])
    if winner == target.text:
        return Outcome(target, "agreed", None, result["reads"])
    return Outcome(target, "changed", winner, result["reads"])


# --- starting, stopping, retrying (the API) ------------------------------------------------


def start(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    scope: str,
    word_keys: Sequence[str],
    models: Sequence[str],
    user_id: str,
) -> tuple[RowMapping, bool]:
    """(the run, True when it was made now; False: the same request's active run). Raises
    Refused: not_found, in_trash, not_ready, ensemble_running, rate_limited and ``plan``'s."""
    ids = {"c": customer_id, "d": document_id}
    word_review.lock_ready(conn, ids)
    planned = plan(conn, customer_id, document_id, scope, word_keys)
    active = conn.execute(_ACTIVE_RUN, ids).mappings().first()
    if active is not None:
        same = sorted(active["word_keys"] or []) == sorted(planned.word_keys or [])
        if active["scope"] == scope and active["retry_of"] is None and same:
            return active, False
        raise Refused("ensemble_running")
    run = _insert(conn, ids, scope, planned.pages, planned.word_keys, models, user_id, None)
    _audit(conn, customer_id, user_id, "ensemble.start", document_id, run_id=str(run["id"]),
           scope=scope, pages=len(planned.pages), words=planned.words)  # fmt: skip
    return run, True


def retry(
    conn: Connection, customer_id: str, document_id: str, run_id: str, *, models: Sequence[str], user_id: str
) -> RowMapping:
    """A new run on the pages ``run_id`` (failed or stopped) did not finish. Raises Refused:
    not_found, in_trash, not_ready, not_retryable, ensemble_running, rate_limited."""
    ids = {"c": customer_id, "d": document_id}
    word_review.lock_ready(conn, ids)
    old = conn.execute(_RUN, {**ids, "r": run_id}).mappings().first()
    if old is None:
        raise Refused("not_found")
    left = [page for page in old["pages"] if page not in old["pages_done"]]
    if old["status"] not in ("failed", "stopped") or not left:
        raise Refused("not_retryable")
    if conn.execute(_ACTIVE_RUN, ids).first() is not None:
        raise Refused("ensemble_running")
    run = _insert(conn, ids, old["scope"], left, old["word_keys"], models, user_id, run_id)
    _audit(conn, customer_id, user_id, "ensemble.retry", document_id, run_id=str(run["id"]),
           retry_of=run_id, pages=len(left))  # fmt: skip
    return run


def stop(
    conn: Connection, customer_id: str, document_id: str, run_id: str, *, user_id: str, now: datetime
) -> None:
    """Ask a queued or running run to stop (a finished one is left as it is). Its finished
    pages stay. Raises Refused("not_found")."""
    params = {"c": customer_id, "d": document_id, "r": run_id, "now": now}
    if conn.execute(_RUN, params).first() is None:
        raise Refused("not_found")
    if conn.execute(_STOP, params).first() is not None:
        _audit(conn, customer_id, user_id, "ensemble.stop", document_id, run_id=run_id)


def get_run(conn: Connection, customer_id: str, document_id: str, run_id: str) -> RowMapping | None:
    return conn.execute(_RUN, {"c": customer_id, "d": document_id, "r": run_id}).mappings().first()


def latest_run(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    return conn.execute(_LATEST, {"c": customer_id, "d": document_id}).mappings().first()


def run_counts(conn: Connection, customer_id: str, run_id: str) -> dict[str, int]:
    """A run's readings by outcome, ``kept`` and ``total``. One statement."""
    counts = {"agreed": 0, "changed": 0, "unresolved": 0, "skipped_user_edit": 0, "kept": 0, "total": 0}
    for row in conn.execute(_COUNTS, {"c": customer_id, "r": run_id}).mappings():
        counts[row["outcome"]] += row["n"]
        counts["kept"] += row["n"] if row["decision"] == "kept" else 0
        counts["total"] += row["n"]
    return counts


def latest_readings(conn: Connection, customer_id: str, document_id: str) -> dict[str, RowMapping]:
    """Each word's latest reading, by word key. One statement."""
    rows = conn.execute(_LATEST_READINGS, {"c": customer_id, "d": document_id}).mappings()
    return {str(row["word_key"]): row for row in rows}


def _insert(
    conn: Connection,
    ids: dict[str, str],
    scope: str,
    pages: Sequence[int],
    word_keys: Sequence[str] | None,
    models: Sequence[str],
    user_id: str,
    retry_of: str | None,
) -> RowMapping:
    conn.execute(_RUNS_LOCK, ids)  # held to the end of the transaction
    if int(conn.execute(_RUNS_THIS_HOUR, ids).scalar_one()) >= RUNS_PER_HOUR:
        raise Refused("rate_limited")
    params = {
        **ids,
        "scope": scope,
        "pages": list(pages),
        "models": list(models),
        "u": user_id,
        "retry_of": retry_of,
        "keys": None if word_keys is None else list(word_keys),
    }
    return conn.execute(_INSERT_RUN, params).mappings().one()


# --- the job (kaagaz/jobs/ensemble.py) -----------------------------------------------------


def claim(
    conn: Connection, customer_id: str, document_id: str, run_id: str, *, now: datetime
) -> RowMapping | None:
    """The run, now running; None when it is done, stopped, failed or not this customer's
    (a duplicate delivery)."""
    params = {"c": customer_id, "d": document_id, "r": run_id, "now": now}
    return conn.execute(_CLAIM, params).mappings().first()


def document_file(conn: Connection, customer_id: str, document_id: str) -> RowMapping | None:
    """The document's sha256, detected_type and whether it is ready, current and out of Trash."""
    return conn.execute(_FILE, {"c": customer_id, "d": document_id}).mappings().first()


def stop_asked(conn: Connection, customer_id: str, run_id: str) -> bool:
    return bool(conn.execute(_STOP_ASKED, {"c": customer_id, "r": run_id}).scalar_one_or_none())


def record_attempt(conn: Connection, customer_id: str, run_id: str, attempt: int) -> None:
    conn.execute(_ATTEMPT, {"c": customer_id, "r": run_id, "a": attempt})


def finish(
    conn: Connection, customer_id: str, run_id: str, status: str, code: str | None, *, now: datetime
) -> None:
    """End a queued or running run: done, stopped or failed; with ``code`` the pages not
    done are its ``pages_failed``. A run already ended is left as it is."""
    params = {"c": customer_id, "r": run_id, "status": status, "code": code, "now": now}
    conn.execute(_FINISH, params)


def save_page(
    conn: Connection,
    customer_id: str,
    document_id: str,
    run_id: str,
    page_no: int,
    outcomes: Sequence[Outcome],
    *,
    now: datetime,
) -> None:
    """One page's readings, in one transaction (the module notes). Raises Refused
    (not_found, in_trash, not_ready) and writes nothing when the document is no longer
    ready, current and out of Trash."""
    ids = {"c": customer_id, "d": document_id}
    word_review.lock_ready(conn, ids)
    latest = word_review.latest_changes(conn, customer_id, document_id)
    for item in outcomes:
        flag = item.target.flag
        change = latest.get(flag.key)
        person = change is not None and change["user_id"] is not None  # a person changed it meanwhile
        outcome, proposal = ("skipped_user_edit", None) if person else (item.outcome, item.proposal)
        params = {
            **ids,
            "r": run_id,
            "key": flag.key,
            "page_no": flag.page_no,
            "region": json.dumps(flag.region),
            "before": item.target.text,
            "outcome": outcome,
            "proposal": proposal,
            "reads": json.dumps(item.reads),
        }
        reading_id = conn.execute(_INSERT_READING, params).scalar_one_or_none()
        if reading_id is not None and outcome == "agreed" and item.target.flagged and item.target.text:
            word_review.resolve_word(
                conn, customer_id, document_id, flag, item.target.text, str(reading_id), now
            )
    conn.execute(_PAGE_DONE, {"c": customer_id, "r": run_id, "p": page_no})


# --- Keep, Revert, Keep all ----------------------------------------------------------------


def keep(
    conn: Connection, customer_id: str, document_id: str, reading_id: str, *, user_id: str, now: datetime
) -> str:
    """Keep a changed reading's proposal (the module notes), and return the change's id.
    Raises Refused: not_found, already_decided, changed_since, in_trash, not_ready."""
    ids = {"c": customer_id, "d": document_id}
    word_review.lock_ready(conn, ids)
    reading = _pending(conn, ids, reading_id)
    change = word_review.latest_changes(conn, customer_id, document_id).get(str(reading["word_key"]))
    if change is not None and change["user_id"] is not None:
        raise Refused("changed_since")  # save_page skipped words a person had changed, so this is newer
    votes = majority([(read["model"], read["text"]) for read in reading["reads"]])
    change_id = word_review.change_word(
        conn,
        customer_id,
        document_id,
        str(reading["word_key"]),
        expected_text=str(reading["before_text"]),
        new_text=str(reading["proposal"]),
        user_id=user_id,
        now=now,
        reading_id=reading_id,
        votes=votes,
    )
    conn.execute(_DECIDE, {"c": customer_id, "id": reading_id, "decision": "kept", "u": user_id, "now": now})
    return change_id


def revert(
    conn: Connection, customer_id: str, document_id: str, reading_id: str, *, user_id: str, now: datetime
) -> None:
    """Turn down a changed reading's proposal: the word stays flagged, every reading an option.
    Raises Refused: not_found, already_decided, in_trash, not_ready."""
    ids = {"c": customer_id, "d": document_id}
    word_review.lock_ready(conn, ids)
    reading = _pending(conn, ids, reading_id)
    conn.execute(
        _DECIDE, {"c": customer_id, "id": reading_id, "decision": "reverted", "u": user_id, "now": now}
    )
    _audit(conn, customer_id, user_id, "ensemble.revert", document_id, reading_id=reading_id,
           run_id=str(reading["run_id"]))  # fmt: skip


def keep_all(
    conn: Connection, customer_id: str, document_id: str, run_id: str, *, user_id: str, now: datetime
) -> dict[str, int]:
    """Keep every changed reading of ``run_id`` not yet decided; ``{kept, skipped}``, skipped
    the words changed since or no longer on the page. Raises Refused("not_found")."""
    ids = {"c": customer_id, "d": document_id}
    if conn.execute(_RUN, {**ids, "r": run_id}).first() is None:
        raise Refused("not_found")
    kept = skipped = 0
    pending: list[object] = list(conn.execute(_PENDING, {**ids, "r": run_id}).scalars())
    for reading_id in pending:
        try:
            keep(conn, customer_id, document_id, str(reading_id), user_id=user_id, now=now)
        except Refused as error:
            if error.code not in ("changed_since", "not_found"):
                raise
            skipped += 1  # refused before anything was written
        else:
            kept += 1
    _audit(
        conn,
        customer_id,
        user_id,
        "ensemble.keep_all",
        document_id,
        run_id=run_id,
        kept=kept,
        skipped=skipped,
    )
    return {"kept": kept, "skipped": skipped}


def _pending(conn: Connection, ids: dict[str, str], reading_id: str) -> RowMapping:
    reading = conn.execute(_READING, {**ids, "id": reading_id}).mappings().first()
    if reading is None or reading["outcome"] != "changed":
        raise Refused("not_found")
    if reading["decision"] is not None:
        raise Refused("already_decided")
    return reading


def _audit(
    conn: Connection,
    customer_id: str,
    user_id: str,
    action: str,
    document_id: str,
    **details: audit.DetailValue,
) -> None:
    audit.record(
        conn,
        customer_id,
        actor_user_id=user_id,
        action=action,
        target_type="document",
        target_id=document_id,
        details=details,
    )
