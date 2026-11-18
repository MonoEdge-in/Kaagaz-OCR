"""The version finder (V17, #648, V-B) against PostgreSQL, as the worker runs it (no person, the
worker role): its groups and their order, once per organisation, resuming after a stop, the
switch and the fingerprints first, locked and Trash files left out (also when that happens during
the run), pairs spotting already suggested or a person answered left out, files uploaded since the
run began left to spotting, and another organisation's files never mixed in. Nothing is linked.
The API that shows the groups: test_isolation_version_groups.py. The groups' rule without a
database: test_versions.py."""

import logging
import threading
import uuid
from datetime import date
from typing import Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from kaagaz.db import version_finder
from kaagaz.db import versions as versions_sql
from kaagaz.db.engine import Database
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.worker_main import FINDER_PASS, FindVersions
from tests.test_ai_settings_db import switched_off
from tests.test_api_support import Api, Signed
from tests.test_operator_auth_db import _owner
from tests.test_versions import PLOT, PLOT_EDITED
from tests.test_versions_db import _doc, _old

pytestmark = pytest.mark.db

BASE = [f"w{n}" for n in range(300)]
# X~Y (296 of 326 shingles) and X~Z (296 of 356) pass V4's 80%; Y~Z (296 of 386, 77%) does not.
TEXTS = {
    "x": BASE,
    "y": [*BASE, *(f"y{n}" for n in range(30))],
    "z": [*BASE, *(f"z{n}" for n in range(60))],
    "w": [f"q{n}" for n in range(300)],  # a stranger
}
_MEMBERS = (
    "SELECT document_id::text, group_id::text, position, state FROM version_group_members"
    " WHERE customer_id = :c ORDER BY group_id, position"
)
_PAIRS = "SELECT first_id::text, second_id::text FROM version_pairs WHERE customer_id = :c ORDER BY 1, 2"
_RUN = (
    "SELECT looked_at, after_id::text, finished_at IS NOT NULL, groups FROM version_finder_runs"
    " WHERE customer_id = :c"
)
_TRASH = "UPDATE documents SET deleted_at = now(), deleted_by = uploaded_by WHERE id = :d"
_DOCUMENTS = "SELECT id, family_id, version_no, is_current FROM documents WHERE customer_id = :c ORDER BY id"


def _library(api: Api, signed: Signed, **columns: dict[str, Any]) -> dict[str, str]:
    """Files x (oldest upload), y (its own date years before), z and the stranger w, fingerprinted
    by the sweep as files read before spotting are; ``columns`` adds columns to a file by name."""
    hours = {"x": 72, "y": 48, "z": 24, "w": 12}
    found = {
        name: _doc(api, signed, words, f"Lease {name}.pdf", hours_ago=hours[name], **columns.get(name, {}))
        for name, words in TEXTS.items()
    }
    _old(api.db, signed.customer_id, *found.values())
    return found


def _ordered(count: int) -> list[str]:
    """New ids in their order, so a test knows which file of a pair is looked at first (the lower)."""
    return sorted(str(uuid.uuid4()) for _ in range(count))


def _members(db: Database, customer: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in _owner(db, _MEMBERS, {"c": customer})]


def _pairs(db: Database, customer: str) -> list[tuple[str, str]]:
    return [(str(a), str(b)) for a, b in _owner(db, _PAIRS, {"c": customer})]


def _run(db: Database, customer: str) -> tuple[Any, ...]:
    return tuple(_owner(db, _RUN, {"c": customer})[0])


def _pass(db: Database, customer: str, limit: int = FINDER_PASS) -> int:
    """One pass of the finder over this organisation alone."""
    return FindVersions(db, limit=limit).of(customer)


def _until_done(db: Database, customer: str, limit: int = 10) -> int:
    """Passes until the run has finished; how many passes it took (at most 50)."""
    for passes in range(1, 51):
        if _pass(db, customer, limit) == 0:
            return passes
    raise AssertionError("the run never finished")


def _families(db: Database, customer: str) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in _owner(db, _DOCUMENTS, {"c": customer})]


def test_x_y_and_z_are_one_group_by_date_and_nothing_is_linked(db: Database) -> None:
    """X~Y and X~Z: one group of three though Y and Z are not alike (77%); the stranger in none.
    The order is each file's own date, else its upload: Y (dated 2020), X, Z; Z the latest. No
    suggestion, no link, no family changed."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    files = _library(api, signed, y={"document_date": date(2020, 1, 1)})
    before = _families(db, c)
    _until_done(db, c)
    members = _members(db, c)
    assert [m[0] for m in members] == [files["y"], files["x"], files["z"]]
    assert {m[1] for m in members} == {members[0][1]} and [m[2:] for m in members] == [
        (1, "open"), (2, "open"), (3, "open"),
    ]  # fmt: skip
    assert set(_pairs(db, c)) == {tuple(sorted((files["x"], files[o]))) for o in ("y", "z")}
    assert _families(db, c) == before
    assert _owner(db, "SELECT count(*) FROM version_suggestions WHERE customer_id = :c", {"c": c}) == [(0,)]
    assert _run(db, c)[2:] == (True, 1)


def test_the_finder_runs_once_per_organisation(db: Database) -> None:
    """A finished run never starts again: a near copy of X added afterwards (uploaded before it,
    as an old file) is never looked at, and the next pass does nothing. One added after the run
    began is spotting's, never the finder's, even while the run goes on."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    low, middle, high = _ordered(3)
    files = _library(api, signed, x={"id": middle})
    late = [  # after the run began, ids either side of X's, so both sides of a pair are checked
        _doc(api, signed, [*BASE, "late"], f"Lease late {n}.pdf", hours_ago=-100_000, id=each)
        for n, each in enumerate((low, high))
    ]
    _old(db, c, *late)
    _until_done(db, c)
    assert not set(late) & ({m[0] for m in _members(db, c)} | {f for pair in _pairs(db, c) for f in pair})
    done = (_members(db, c), _pairs(db, c), _run(db, c))
    copy = _doc(api, signed, [*BASE, "copy"], "Lease copy.pdf", hours_ago=100)
    _old(db, c, copy)
    with db.customer(c) as conn:
        assert not version_finder.due(conn, c)
    assert _pass(db, c) == 0
    assert (_members(db, c), _pairs(db, c), _run(db, c)) == done
    assert {m[0] for m in done[0]} == {files["x"], files["y"], files["z"]}


def test_a_stopped_run_resumes_after_the_last_file_it_looked_at(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One file a pass; the database goes away while the worker looks at the second (its
    transaction rolls back, and an outage is never counted against the file): the run goes on from
    that file, and the groups are those of a run never stopped."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    files = _library(api, signed)
    assert _pass(db, c, 1) == 1
    first = _run(db, c)
    assert first[:2] == (1, min(files.values())) and not first[2]
    real = version_finder.look_at

    def stop(*_: Any) -> int:
        raise BackendUnavailable("database")

    monkeypatch.setattr(version_finder, "look_at", stop)
    with pytest.raises(BackendUnavailable):
        _pass(db, c, 1)
    assert _run(db, c) == first
    monkeypatch.setattr(version_finder, "look_at", real)
    assert _until_done(db, c, 1) == 5  # the 3 files left, the pass that groups, the one that finds it done
    assert _run(db, c)[0] == 4
    members = _members(db, c)
    assert {m[0] for m in members} == {files["x"], files["y"], files["z"]}
    assert len(_pairs(db, c)) == 2


def test_the_finder_waits_for_the_switch_and_the_fingerprints(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    _library(api, signed)
    with db.customer(c) as conn:
        assert version_finder.due(conn, c)
    with switched_off(db, "version_spotting"), db.customer(c) as conn:
        assert not version_finder.due(conn, c)
    unprinted = _doc(api, signed, BASE, "Lease new.pdf", hours_ago=1)  # ready, no fingerprint yet
    with db.customer(c) as conn:
        assert not version_finder.due(conn, c)
    _old(db, c, unprinted)
    with db.customer(c) as conn:
        assert version_finder.due(conn, c)


def test_locked_and_trashed_files_are_never_grouped(db: Database) -> None:
    """Y and a second copy in Trash (ids either side of X's, so both sides of a pair are checked)
    and Z locked before the run: none is ever paired (a locked file is never ready and keeps no
    fingerprint, 0093); X and the copy C are a group."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    low, middle, high = _ordered(3)
    files = _library(api, signed, x={"id": middle}, y={"id": low})
    copy = _doc(api, signed, [*BASE, "copy"], "Lease copy.pdf", hours_ago=80)
    again = _doc(api, signed, [*BASE, "again"], "Lease again.pdf", hours_ago=80, id=high)
    _old(db, c, copy, again)
    for trashed in (files["y"], again):
        _owner(db, _TRASH, {"d": trashed})
    _lock(db, files["z"])
    _until_done(db, c)
    assert {m[0] for m in _members(db, c)} == {files["x"], copy}
    assert _pairs(db, c) == [tuple(sorted((files["x"], copy)))]


def _lock(db: Database, document: str) -> None:
    """Locked as 0093 leaves a version: the state ``locked`` and no fingerprint or type."""
    _owner(
        db,
        "UPDATE documents SET locked_at = now(), locked_by = uploaded_by, status = 'locked', doc_type = NULL,"
        " text_minhash = NULL, text_bands = NULL WHERE id = :d",
        {"d": document},
    )


def test_a_file_trashed_or_locked_during_the_run_is_left_out_of_its_group(db: Database) -> None:
    """Every file looked at and paired, then Y goes to Trash and Z is locked before the groups are
    made: the group is X and the copy alone."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    files = _library(api, signed)
    copy = _doc(api, signed, [*BASE, "copy"], "Lease copy.pdf", hours_ago=80)
    _old(db, c, copy)
    assert _pass(db, c, 5) == 5  # all 5 looked at, the groups not made yet
    assert len(_pairs(db, c)) == 5  # x~y, x~z, x~copy, y~copy, z~copy
    _owner(db, _TRASH, {"d": files["y"]})
    _lock(db, files["z"])
    _until_done(db, c)
    assert {m[0] for m in _members(db, c)} == {files["x"], copy}


def test_a_file_trashed_while_it_is_scored_is_never_paired(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review (#648): the candidates are scored with no row lock on them (a lock or a move never
    waits on the finder's 200), then only the scored files are taken FOR SHARE, in id order, and
    checked again before any pair is written: Y, trashed while X's candidates were scored, is
    never paired; X and Z are a group."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    low, middle, high = _ordered(3)
    files = _library(api, signed, x={"id": low}, y={"id": middle}, z={"id": high})
    real = versions_sql.scored

    def trashing(*args: Any) -> Any:
        found = real(*args)
        if args[2] == files["x"]:
            with db.engine.begin() as conn:  # another session, as a person's Trash would be
                conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                conn.execute(text(_TRASH), {"d": files["y"]})
        return found

    monkeypatch.setattr(versions_sql, "scored", trashing)
    _until_done(db, c)
    assert _pairs(db, c) == [(files["x"], files["z"])]
    assert {m[0] for m in _members(db, c)} == {files["x"], files["z"]}


def test_a_busy_candidate_fails_the_pass_at_once_and_writes_nothing(db: Database) -> None:
    """Review (#648): the scored files are taken FOR SHARE NOWAIT, so the finder never waits on, or
    deadlocks with, a lock, a Trash or a move that holds one (they lock in family order, the finder
    by id). Y row-locked by another transaction: X's look fails at once (55P03), counted as X's
    failure, with nothing written; once Y is free the next pass pairs it."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    # Changed on purpose (#648, after PR 9): the stranger w gets the highest id too. With a random one
    # below x's (1 in 4), w was looked at first, so the pass ended at its second file (found [2]).
    low, middle, high, highest = _ordered(4)
    files = _library(api, signed, x={"id": low}, y={"id": middle}, z={"id": high}, w={"id": highest})
    finder = FindVersions(db, customers=lambda: [c])
    found: list[int] = []
    with db.engine.connect() as other:
        held = other.begin()
        other.execute(text("SELECT 1 FROM documents WHERE id = :d FOR UPDATE"), {"d": files["y"]})
        looking = threading.Thread(target=lambda: found.append(finder()))
        looking.start()
        looking.join(timeout=10)
        done_in_time = not looking.is_alive()
        held.rollback()
    looking.join()
    assert done_in_time and found == [1]  # failed at once: no lock wait, the pass ended there
    assert _pairs(db, c) == [] and _run(db, c)[:2] == (0, None)
    assert finder() == 4  # Y free: X looked at again, then the rest, then grouped
    assert set(_pairs(db, c)) == {(files["x"], files["y"]), (files["x"], files["z"])}


def test_a_pair_spotting_suggested_or_a_person_answered_is_left_out(db: Database) -> None:
    """Y was kept separate from X's family once (a rejected suggestion): only X~Z is a group."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    files = _library(api, signed)
    _owner(
        db,
        "INSERT INTO version_suggestions (customer_id, document_id, candidate_family_id, outcome, status)"
        " VALUES (:c, :y, :x, 'suggested', 'rejected')",
        {"c": c, "x": files["x"], "y": files["y"]},
    )
    _until_done(db, c)
    assert {m[0] for m in _members(db, c)} == {files["x"], files["z"]}


def test_another_organisations_files_are_never_mixed_in(db: Database) -> None:
    """Two organisations with the same files: each one's groups and pairs hold its own files only."""
    api = Api(db)
    ours, theirs = api.new_signed(), api.new_signed()
    mine, other = _library(api, ours), _library(api, theirs)
    for signed in (ours, theirs):
        _until_done(db, signed.customer_id)
    for signed, files in ((ours, mine), (theirs, other)):
        c = signed.customer_id
        assert {m[0] for m in _members(db, c)} == {files["x"], files["y"], files["z"]}
        assert {f for pair in _pairs(db, c) for f in pair} <= set(files.values())
    rows = _owner(
        db,
        "SELECT count(*) FROM version_group_members m JOIN documents d ON d.id = m.document_id"
        " WHERE d.customer_id <> m.customer_id",
        {},
    )
    assert rows == [(0,)]


def test_a_document_with_text_like_none_is_in_no_group(db: Database) -> None:
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    alone = _doc(api, signed, TEXTS["w"], "Alone.pdf", hours_ago=5)
    _old(db, c, alone)
    _until_done(db, c)
    assert _members(db, c) == [] and _run(db, c)[2:] == (True, 0)
    assert _pairs(db, c) == []


def test_prd9_f06_two_short_files_of_one_name_are_a_group_word_by_word(db: Database) -> None:
    """PRD 9 F-06: the finder scores as spotting does (``versions_sql.scored``), so two short files
    of one name, two of their 11 words changed, are a group; a third of that name with other words
    is in none."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    first = _doc(api, signed, PLOT.split(), "plot data.json", hours_ago=48, doc_type=None)
    second = _doc(api, signed, PLOT_EDITED.split(), "plot data.json", hours_ago=24, doc_type=None)
    other = _doc(api, signed, ["other", "words"], "plot data.json", hours_ago=12, doc_type=None)
    _old(db, c, first, second, other)
    _until_done(db, c)
    assert _pairs(db, c) == [tuple(sorted((first, second)))]
    assert [m[0] for m in _members(db, c)] == [first, second]


def test_a_switch_turned_on_later_still_gets_its_run(db: Database) -> None:
    """Review (#648): with version_spotting off the pass finds nothing due, but the organisation's
    run is still pending, so the sweep goes on; switched on, the finder runs; once it has finished,
    nothing is pending."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    _library(api, signed)
    finder = FindVersions(db, customers=lambda: [c])
    with switched_off(db, "version_spotting"):
        assert finder() == 0 and finder.pending()
    assert finder() == 4 and _run(db, c)[2]
    assert not finder.pending()


def test_a_file_whose_look_keeps_timing_out_is_passed_over(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review (#648): a statement cancelled by the finder's own time limit (SQLSTATE 57014) is that
    file's failure, not an outage: it is counted, and the file is passed over on the third try, so
    the organisation's run still finishes."""
    api = Api(db)
    signed = api.new_signed()
    c = signed.customer_id
    stranger = f"00000000-0000-4000-8000-{uuid.uuid4().hex[:12]}"
    files = _library(api, signed, w={"id": stranger})
    real = version_finder.look_at

    def timing_out(conn: Any, customer: str, document: str) -> int:
        if document == stranger:
            cancelled = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
            raise OperationalError("SELECT ...", {}, cancelled)
        return real(conn, customer, document)

    monkeypatch.setattr(version_finder, "look_at", timing_out)
    finder = FindVersions(db, customers=lambda: [c])
    assert finder() == 1 and finder() == 1  # counted, not raised: the pass ends there
    assert finder() == 4 and _run(db, c)[2]  # the third: passed over, the rest looked at, grouped
    assert {m[0] for m in _members(db, c)} == {files["x"], files["y"], files["z"]}


def test_one_failing_file_never_stalls_the_finder(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review (#648): the first organisation's first file (the stranger W, lowest id) fails each
    time it is looked at. Each failure is logged by organisation, file and error type, never its
    text, and ends that organisation's pass; the organisations take turns, so the second finishes
    meanwhile; the third failure in this process passes the file over (a warning), and the first
    organisation's run finishes with the rest: X, Y and Z still a group."""
    api = Api(db)
    ours, theirs = api.new_signed(), api.new_signed()
    stranger = f"00000000-0000-4000-8000-{uuid.uuid4().hex[:12]}"
    mine = _library(api, ours, w={"id": stranger})
    other = _library(api, theirs)
    real = version_finder.look_at

    def failing(conn: Any, customer: str, document: str) -> int:
        if document == stranger:
            raise ValueError("Zebra, a line of the file's text")
        return real(conn, customer, document)

    monkeypatch.setattr(version_finder, "look_at", failing)
    caplog.set_level(logging.INFO, logger="kaagaz.worker")
    finder = FindVersions(db, customers=lambda: [ours.customer_id, theirs.customer_id])
    assert finder() == 1  # ours: W fails at once, the pass ends there
    assert _run(db, ours.customer_id)[:2] == (0, None)
    assert finder() == 4 and _run(db, theirs.customer_id)[2]  # theirs' turn: all 4, then grouped
    assert finder() == 1  # ours again (theirs is done): W fails a second time
    assert finder() == 4  # the third: W passed over, the other 3 looked at, then grouped
    assert _run(db, ours.customer_id)[:3] == (4, max(mine.values()), True)
    assert {m[0] for m in _members(db, ours.customer_id)} == {mine["x"], mine["y"], mine["z"]}
    assert {m[0] for m in _members(db, theirs.customer_id)} == {other["x"], other["y"], other["z"]}
    failed = [r for r in caplog.records if getattr(r, "document_id", None) == stranger]
    assert [(r.levelname, r.__dict__["customer_id"], r.__dict__["code"]) for r in failed] == [
        ("WARNING", ours.customer_id, "ValueError"),
    ] * 3 + [("WARNING", ours.customer_id, "passed_over")]
    assert not any("Zebra" in r.getMessage() for r in caplog.records)
