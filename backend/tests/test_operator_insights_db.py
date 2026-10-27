"""Operator insights against PostgreSQL (council 11 Oct 2026, F1-F4; 0024): what each
reader counts, where its edges are (K = 10 for an outcome value, 20 for a p95), the
range it refuses (check_violation), days in the zone, unknown and estimated costs,
keyless mode, the admin's open review, the first ready run, and what deleting a document
for good keeps (its spend) and loses (its runs). Customer separation and what never
leaves the database: ``test_isolation_operator_insights.py``.

Each test plants its rows on its own random day between 1950 and 1999, so the readers
with no organisation column (the board, the platform row) see that test's rows only.
Rows are written as the table owner (as the pipeline, the ledger and the routes leave
them), documents through the app role.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import RowMapping
from sqlalchemy.exc import DBAPIError

from kaagaz.db import admin as admin_sql
from kaagaz.db import operator_insights as insights
from kaagaz.db.documents import delete_forever
from kaagaz.db.engine import Database, OperatorDatabase
from kaagaz.db.sqlstate import sqlstate
from tests import test_migration_0002_db as m0002
from tests.db_support import insert_document, migrate, new_customer
from tests.fakes import MemoryStorage
from tests.test_api_support import Api
from tests.test_operator_auth_db import _Operator, _owner

pytestmark = pytest.mark.db

fresh = m0002.fresh  # the fixture, by its name
IST = "Asia/Kolkata"
K, P95_MIN = 10, 20  # 0024's PROVISIONAL constants
Reader = Callable[..., Sequence[RowMapping]]

_RUN = (
    "INSERT INTO processing_runs (customer_id, document_id, started_at, finished_at, status, error_code,"
    " config, pages_text_layer, pages_ocr, pages_gemini, pages_local, pages_cached, values_verified,"
    " values_needs_review) VALUES (:c, :d, :s, :f, :status, :code, CAST(:config AS jsonb), :text_layer, :ocr,"
    " :ai, :local, :cached, :verified, :needs_review)"
)
_USAGE = (
    "INSERT INTO model_usage (customer_id, document_id, kind, model, cached, calls, input_tokens,"
    " output_tokens, reasoning_tokens, cost_usd, cost_estimated, created_at)"
    " VALUES (:c, :d, :kind, :m, :cached, :calls, 100, 20, 0, :cost, :estimated, :at)"
)
_AUDIT = (
    "INSERT INTO audit_log (customer_id, actor_user_id, action, details, created_at)"
    " SELECT :c, :u, :a, CAST(:details AS jsonb), :at FROM generate_series(1, :n)"
)
_CONVERSATION = "INSERT INTO conversations (customer_id, user_id, title) VALUES (:c, :u, :t) RETURNING id"
_ANSWERS = (
    "INSERT INTO messages (customer_id, conversation_id, role, text, answer, created_at)"
    " SELECT :c, :v, 'assistant', :t, CAST(:answer AS jsonb), :at FROM generate_series(1, :n) RETURNING id"
)
_FEEDBACK = (
    "INSERT INTO feedback (customer_id, message_id, user_id, rating, reason)"
    " VALUES (:c, :m, :u, :rating, :reason)"
)
_PAGE = (
    "INSERT INTO pages (customer_id, document_id, page_no, width, height, unit, has_image, route, read_by,"
    " read_issues) VALUES (:c, :d, :n, 595, 842, 'px', true, 'ocr', :by, CAST(:issues AS text[]))"
)


# --- planting ----------------------------------------------------------------------------


def window_day() -> date:
    """A day of its own between 1950 and 1999 for one test's rows."""
    return date(1950, 1, 1) + timedelta(days=uuid.uuid4().int % 18000)


def at(day: date, hours: float = 12) -> datetime:
    """``hours`` after midnight UTC of ``day``."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=hours)


def new_model() -> str:
    return f"example/insights-{uuid.uuid4().hex[:12]}"


def operator(db: Database) -> str:
    """An enabled operator whose two-step secret is confirmed."""
    return _Operator(Api(db)).id


def read(db: Database, operator_id: str, reader: Reader, *args: Any) -> list[dict[str, Any]]:
    with OperatorDatabase(db.engine).operator() as conn:
        return [dict(row) for row in reader(conn, operator_id, *args)]


def of(rows: list[dict[str, Any]], customer: str | None) -> list[dict[str, Any]]:
    """The rows of one organisation (None: the platform row)."""
    return [
        row for row in rows if (None if row["customer_id"] is None else str(row["customer_id"])) == customer
    ]


def one(rows: list[dict[str, Any]], key: str, value: str | None) -> dict[str, Any]:
    [row] = [row for row in rows if (None if row[key] is None else str(row[key])) == value]
    return row


def document(db: Database, customer: str, created: datetime, **columns: Any) -> str:
    with db.customer(customer) as conn:
        return insert_document(conn, customer, created_at=created, **columns)


def run(
    db: Database,
    customer: str,
    document_id: str,
    started: datetime,
    *,
    seconds: float = 60,
    status: str = "ready",
    code: str | None = None,
    config: dict[str, Any] | None = None,
    text_layer: int = 0,
    ocr: int = 1,
    ai: int = 0,
    local: int = 0,
    cached: int = 0,
    verified: int = 0,
    needs_review: int = 0,
) -> None:
    """One run, finished ``seconds`` after it started (a running one is not finished)."""
    _owner(
        db,
        _RUN,
        {
            "c": customer,
            "d": document_id,
            "s": started,
            "f": None if status == "running" else started + timedelta(seconds=seconds),
            "status": status,
            "code": code,
            "config": json.dumps(config or {}),
            "text_layer": text_layer,
            "ocr": ocr,
            "ai": ai,
            "local": local,
            "cached": cached,
            "verified": verified,
            "needs_review": needs_review,
        },
    )


def read_documents(
    db: Database, customer: str, count: int, created: datetime, model: str | None, **run_columns: Any
) -> list[str]:
    """``count`` documents uploaded at ``created``, each read once (ready a minute later,
    with ``model`` as its read_model, or keyless) and one scanned page read by that model
    (or rapidocr)."""
    config = {} if model is None else {"read_model": model, "seconds_reading": 4.0}
    documents = []
    for _ in range(count):
        document_id = document(db, customer, created)
        run(db, customer, document_id, created, config=config, **{"ai": 2 if model else 0, **run_columns})
        page(db, customer, document_id, model or "rapidocr")
        documents.append(document_id)
    return documents


def page(
    db: Database, customer: str, document_id: str, read_by: str, issues: Sequence[str] = (), page_no: int = 1
) -> None:
    _owner(db, _PAGE, {"c": customer, "d": document_id, "n": page_no, "by": read_by, "issues": list(issues)})


def usage(
    db: Database,
    customer: str,
    when: datetime,
    kind: str,
    model: str,
    calls: int,
    cost: str | None,
    *,
    cached: bool = False,
    estimated: bool = False,
    document_id: str | None = None,
) -> None:
    params = {
        "c": customer,
        "d": document_id,
        "kind": kind,
        "m": model,
        "cached": cached,
        "calls": calls,
        "cost": cost,
        "estimated": estimated,
        "at": when,
    }
    _owner(db, _USAGE, params)


def audit(
    db: Database,
    customer: str,
    action: str,
    when: datetime,
    *,
    count: int = 1,
    actor: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    params = {
        "c": customer,
        "u": actor,
        "a": action,
        "details": json.dumps(details or {}),
        "at": when,
        "n": count,
    }
    _owner(db, _AUDIT, params)


def answers(
    db: Database,
    customer: str,
    user: str,
    when: datetime,
    count: int,
    *,
    status: str = "answered",
    text_value: str = "The total is 1,200.",
    answer: dict[str, Any] | None = None,
) -> list[str]:
    """``count`` assistant answers in a new conversation of ``user``; their ids."""
    [(conversation,)] = _owner(db, _CONVERSATION, {"c": customer, "u": user, "t": "A chat"})
    body = answer or {"status": status, "label": "verified", "route": "search", "citations": [{"page": 1}]}
    rows = _owner(
        db,
        _ANSWERS,
        {
            "c": customer,
            "v": conversation,
            "t": text_value,
            "answer": json.dumps(body),
            "at": when,
            "n": count,
        },
    )
    return [str(row[0]) for row in rows]


def feedback(
    db: Database, customer: str, user: str, message: str, rating: str, reason: str | None = None
) -> None:
    _owner(db, _FEEDBACK, {"c": customer, "m": message, "u": user, "rating": rating, "reason": reason})


# --- the tests ---------------------------------------------------------------------------


def test_an_outcome_needs_k_of_its_total_and_a_p95_needs_20(db: Database) -> None:
    """9 vs 10 for every outcome value (answers, feedback, values, corrections, ready p50,
    seconds a page p50, page signals), 19 vs 20 for each p95. Volumes are never NULL."""
    op, day = operator(db), window_day()
    when = at(day)
    orgs: dict[int, tuple[str, str]] = {n: new_customer(db) for n in (9, 19, 20)}
    models = {n: new_model() for n in orgs}
    for n, (customer, user) in orgs.items():
        documents = read_documents(db, customer, n, when, models[n], verified=1)
        page(db, customer, documents[0], models[n], ["readers_disagree"], page_no=2)
        size = K - 1 if n == 9 else K  # 9 of each outcome total in the first, 10 elsewhere
        for message in answers(db, customer, user, when, size):
            feedback(db, customer, user, message, "up")
        audit(db, customer, "review.resolve", when, count=size, details={"choice": "typed"})

    quality = read(db, op, insights.quality, day, day, "UTC")
    nine, nineteen, twenty = (one(quality, "customer_id", orgs[n][0]) for n in (9, 19, 20))
    suppressed = [
        "answered",
        "not_found",
        "unchecked",
        "answers_verified",
        "answers_fixed_query",
        "citations",
        "feedback_up",
        "values_needs_review",
        "corrections_second",
        "corrections_typed",
        "ready_p50_seconds",
        "ready_p95_seconds",
    ]
    assert {key: nine[key] for key in suppressed} == dict.fromkeys(suppressed)
    assert (nine["answers"], nine["feedback"], nine["values_total"], nine["corrections"]) == (9, 9, 9, 9)
    assert (nine["ready_documents"], nine["runs"]) == (9, 9)
    assert (nineteen["answered"], nineteen["not_found"], nineteen["citations"]) == (10, 0, 10)
    assert (nineteen["feedback_up"], nineteen["corrections_typed"], nineteen["corrections_second"]) == (
        10,
        10,
        0,
    )
    assert (nineteen["values_total"], nineteen["values_needs_review"]) == (19, 0)
    assert (nineteen["ready_p50_seconds"], nineteen["ready_p95_seconds"]) == (60.0, None)
    assert (twenty["ready_p50_seconds"], twenty["ready_p95_seconds"]) == (60.0, 60.0)

    health = read(db, op, insights.org_health, day, "UTC")
    assert one(health, "customer_id", orgs[9][0])["not_found_30d"] is None
    assert one(health, "customer_id", orgs[19][0])["not_found_30d"] == 0

    board = read(db, op, insights.model_board, day, day, "UTC")
    by_model = {n: one(board, "model", models[n]) for n in orgs}
    assert (by_model[9]["seconds_per_page_p50"], by_model[9]["pages_disagree"]) == (None, None)
    assert (by_model[9]["ocr_pages"], by_model[9]["runs"]) == (10, 9)
    assert (by_model[19]["seconds_per_page_p50"], by_model[19]["seconds_per_page_p95"]) == (2.0, None)
    assert (by_model[19]["pages_disagree"], by_model[19]["pages_refused"]) == (1, 0)
    assert (by_model[20]["seconds_per_page_p50"], by_model[20]["seconds_per_page_p95"]) == (2.0, 2.0)


def test_a_backwards_or_long_range_and_an_unknown_zone_are_refused_in_sql(db: Database) -> None:
    op, day = operator(db), window_day()
    refused: list[tuple[Reader, tuple[Any, ...]]] = []
    for reader in (insights.daily, insights.quality, insights.model_board):
        refused += [
            (reader, (day, day - timedelta(days=1), "UTC")),  # backwards
            (reader, (day, day + timedelta(days=366), "UTC")),  # 367 days
            (reader, (day, day, "Mars/Olympus_Mons")),
        ]
        read(db, op, reader, day, day + timedelta(days=365), IST)  # 366 days: allowed
    refused.append((insights.org_health, (day, "Mars/Olympus_Mons")))
    for reader, args in refused:
        with pytest.raises(DBAPIError) as error:
            read(db, op, reader, *args)
        assert sqlstate(error.value) == "23514", (reader.__name__, args)  # check_violation


def test_days_are_counted_in_the_zone(db: Database) -> None:
    """23:30 and 00:30 India time, an hour apart, fall on two days there and one in UTC."""
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    audit(db, customer, "document.upload", at(day, 18))  # 23:30 in India
    audit(db, customer, "document.upload", at(day, 19))  # 00:30 the next day there

    in_india = of(read(db, op, insights.daily, day, day + timedelta(days=1), IST), customer)
    assert [(row["day"], row["uploads"]) for row in in_india] == [(day, 1), (day + timedelta(days=1), 1)]
    in_utc = of(read(db, op, insights.daily, day, day + timedelta(days=1), "UTC"), customer)
    assert [(row["day"], row["uploads"]) for row in in_utc] == [(day, 2)]


def test_cached_pages_cost_nothing_and_an_unknown_cost_is_left_out_and_marked(db: Database) -> None:
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    reader, chat = new_model(), new_model()
    when = at(day)
    usage(db, customer, when, "scan", reader, 5, "0.5")
    usage(db, customer, when, "scan", reader, 3, "0", cached=True)
    usage(db, customer, when, "card", reader, 1, "0.1", estimated=True)
    usage(db, customer, when, "chat", chat, 2, None)

    [daily] = of(read(db, op, insights.daily, day, day, "UTC"), customer)
    assert (daily["scan_pages"], daily["cached_pages"]) == (5, 3)
    money = (daily["cost_usd"], daily["cost_scan_usd"], daily["cost_documents_usd"])
    assert money == (Decimal("0.6"), Decimal("0.5"), Decimal("0.6"))
    assert daily["cost_questions_usd"] is None  # only an unknown cost
    assert (daily["cost_estimated"], daily["cost_part_unknown"]) == (True, True)
    assert (daily["runs"], daily["uploads"]) == (0, 0)

    board = read(db, op, insights.model_board, day, day, "UTC")
    scans = one(board, "model", reader)
    assert (scans["calls"], scans["scan_calls"], scans["cached_scan_calls"]) == (9, 8, 3)
    assert (scans["cost_usd"], scans["scan_cost_usd"], scans["cost_estimated"]) == (
        Decimal("0.6"),
        Decimal("0.5"),
        True,
    )
    assert (scans["input_tokens"], scans["runs"], scans["cost_part_unknown"]) == (300, 0, False)
    talk = one(board, "model", chat)
    assert (talk["calls"], talk["cost_usd"], talk["cost_part_unknown"], talk["ocr_pages"]) == (
        2,
        None,
        True,
        0,
    )

    health = one(read(db, op, insights.org_health, day, "UTC"), "customer_id", customer)
    month = (health["month_cost_usd"], health["month_cost_estimated"], health["month_cost_part_unknown"])
    assert month == (Decimal("0.6"), True, True)


def test_keyless_has_no_cost_and_one_rapidocr_row(db: Database) -> None:
    """OCR_ENGINE=rapidocr, MODEL_ENABLED=false: no read_model in a run, pages read by
    rapidocr, no ledger. A run that read no scan is no model's."""
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    read_documents(db, customer, 2, at(day), None)
    run(db, customer, document(db, customer, at(day)), at(day), ocr=0, text_layer=3)

    [daily] = of(read(db, op, insights.daily, day, day, "UTC"), customer)
    assert (daily["runs"], daily["pages"], daily["cost_usd"], daily["cost_part_unknown"]) == (
        3,
        5,
        None,
        False,
    )
    [board] = read(db, op, insights.model_board, day, day, "UTC")
    assert (board["model"], board["runs"], board["documents"], board["ocr_pages"]) == ("rapidocr", 2, 2, 2)
    assert (board["calls"], board["cost_usd"], board["cost_estimated"], board["cost_part_unknown"]) == (
        0,
        None,
        False,
        False,
    )
    health = one(read(db, op, insights.org_health, day, "UTC"), "customer_id", customer)
    assert (health["month_cost_usd"], health["month_cost_part_unknown"]) == (None, False)


def test_the_open_review_is_the_admins_number(db: Database) -> None:
    """db/admin.py's open_review_items: needs-review values of current, ready versions out
    of Trash, never an older version's, a failed one's or one in Trash."""
    op, day = operator(db), window_day()
    api = Api(db)
    customer, user = new_customer(db)
    family = str(uuid.uuid4())
    current = document(db, customer, at(day))
    older = document(db, customer, at(day), id=family, family_id=family, version_no=1, is_current=False)
    newer = document(db, customer, at(day), family_id=family, version_no=2)
    failed = document(db, customer, at(day), status="failed", failure_code="corrupt_file")
    trashed = document(db, customer, at(day), deleted_at=at(day), deleted_by=user)
    for document_id, label in (
        (current, "needs_review"),
        (current, "needs_review"),
        (current, "verified"),
        (older, "needs_review"),
        (newer, "needs_review"),
        (failed, "needs_review"),
        (trashed, "needs_review"),
    ):
        api.add_value(customer, document_id, label=label)

    health = one(read(db, op, insights.org_health, day, "UTC"), "customer_id", customer)
    with db.customer(customer) as conn:
        admin = admin_sql.stats(conn, customer)["open_review_items"]
    assert health["open_review"] == admin == 3
    assert health["oldest_open_review_at"] is not None


def test_upload_to_ready_counts_each_documents_first_ready_run_only(db: Database) -> None:
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    model = new_model()
    for document_id in read_documents(db, customer, K, at(day), model):  # ready a minute after upload
        # Fixed (#239, 27 Oct 2026): an hour after the noon upload; at(day, 1) was 01:00, before it.
        run(db, customer, document_id, at(day, 13), seconds=600, config={"read_model": model})  # read again
    earlier = document(db, customer, at(day - timedelta(days=5)))
    run(db, customer, earlier, at(day - timedelta(days=5)))  # first ready before the window
    run(db, customer, earlier, at(day, 2), seconds=600)  # read again in it: not a first

    row = one(read(db, op, insights.quality, day, day, "UTC"), "customer_id", customer)
    assert (row["runs"], row["documents_run"], row["ready_documents"], row["ready_p50_seconds"]) == (
        2 * K + 1,
        K + 1,
        K,
        60.0,
    )


def test_seconds_a_page_take_a_number_only(db: Database) -> None:
    """A seconds_reading that is not a JSON number, or a run with no model page, is left out."""
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    model = new_model()
    read_documents(db, customer, K, at(day), model)  # 4 s over 2 pages each
    for config, pages in (({"seconds_reading": "999"}, 1), ({"seconds_reading": 999}, 0)):
        run(
            db,
            customer,
            document(db, customer, at(day)),
            at(day),
            config={"read_model": model, **config},
            ai=pages,
        )

    row = one(read(db, op, insights.model_board, day, day, "UTC"), "model", model)
    assert (row["runs"], row["seconds_per_page_p50"], row["seconds_per_page_p95"]) == (K + 2, 2.0, None)


def test_an_empty_platform_gives_no_rows_but_the_platform_row(fresh: tuple[str, Database]) -> None:
    url, database = fresh
    migrate(url, "head")
    op, day = operator(database), window_day()
    assert read(database, op, insights.daily, day, day, "UTC") == []
    assert read(database, op, insights.org_health, day, "UTC") == []
    assert read(database, op, insights.model_board, day, day, "UTC") == []
    [platform] = read(database, op, insights.quality, day, day, "UTC")
    assert platform["customer_id"] is None
    assert (platform["runs"], platform["answers"], platform["ready_documents"]) == (0, 0, 0)
    assert (platform["answered"], platform["ready_p50_seconds"]) == (None, None)


def test_a_document_deleted_for_good_keeps_its_spend_and_loses_its_runs(db: Database) -> None:
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    model = new_model()
    [document_id] = read_documents(db, customer, 1, at(day), model)
    usage(db, customer, at(day), "scan", model, 2, "0.5", document_id=document_id)
    with db.customer(customer) as conn:
        delete_forever(conn, MemoryStorage(), customer, document_id)

    [daily] = of(read(db, op, insights.daily, day, day, "UTC"), customer)
    assert (daily["runs"], daily["pages"], daily["scan_pages"], daily["cost_usd"]) == (
        0,
        0,
        2,
        Decimal("0.5"),
    )
    quality = one(read(db, op, insights.quality, day, day, "UTC"), "customer_id", customer)
    assert (quality["runs"], quality["ready_documents"]) == (0, 0)
    board = one(read(db, op, insights.model_board, day, day, "UTC"), "model", model)
    assert (board["runs"], board["ocr_pages"], board["scan_calls"], board["cost_usd"]) == (
        0,
        0,
        2,
        Decimal("0.5"),
    )


def test_runs_by_outcome_and_the_platform_row_adds_up_every_organisation(db: Database) -> None:
    """Ended runs only (never running or superseded); failed by the platform (internal,
    retries out, a backend unavailable) apart from a bad file; degraded: ready with an error."""
    op, day = operator(db), window_day()
    (a, _), (b, _) = new_customer(db), new_customer(db)
    for status, code in (
        ("ready", None),
        ("ready", "model_card_failed"),
        ("failed", "gemini_unavailable"),
        ("failed", "internal_error"),
        ("failed", "retries_exhausted"),
        ("failed", "corrupt_file"),
        ("superseded", None),
        ("running", None),
    ):
        run(db, a, document(db, a, at(day)), at(day), status=status, code=code)
    run(db, b, document(db, b, at(day)), at(day), status="failed", code="scanner_unavailable")

    quality = read(db, op, insights.quality, day, day, "UTC")
    outcome = ("runs", "runs_failed", "runs_failed_platform", "runs_degraded", "pages_ocr")
    assert [one(quality, "customer_id", a)[key] for key in outcome] == [6, 4, 3, 1, 6]
    assert [one(quality, "customer_id", b)[key] for key in outcome] == [1, 1, 1, 0, 1]
    assert [of(quality, None)[0][key] for key in outcome] == [7, 5, 4, 1, 7]  # this day: these two only
    [daily] = of(read(db, op, insights.daily, day, day, "UTC"), a)
    assert (daily["runs"], daily["runs_failed"], daily["pages"]) == (6, 4, 6)


def test_the_platform_row_never_reveals_an_organisation_below_k_by_subtraction(db: Database) -> None:
    """One organisation with 20 answers and one with 3: the platform's outcome values and
    the totals they are shares of count the first only, so the platform minus the shown
    organisation leaves nothing of the second's answered or not found (nor its thumbs up,
    values needing review or kinds of fix)."""
    op, day = operator(db), window_day()
    when = at(day)
    (big, big_user), (small, small_user) = new_customer(db), new_customer(db)
    answers(db, big, big_user, when, 12)
    answers(db, big, big_user, when, 8, status="not_found")
    answers(db, small, small_user, when, 1)
    for message in answers(db, small, small_user, when, 2, status="not_found"):
        feedback(db, small, small_user, message, "up")
    audit(db, small, "review.resolve", when, count=3, details={"choice": "typed"})
    run(db, small, document(db, small, when), when, verified=1, needs_review=2)

    quality = read(db, op, insights.quality, day, day, "UTC")
    big_row, small_row, [platform] = (
        one(quality, "customer_id", big),
        one(quality, "customer_id", small),
        of(quality, None),
    )
    hidden = ("answered", "not_found", "feedback_up", "values_needs_review", "corrections_typed")
    assert [small_row[key] for key in hidden] == [None] * len(hidden)
    totals = ("answers", "feedback", "values_total", "corrections")
    assert [small_row[key] for key in totals] == [3, 2, 3, 3]  # volumes are never NULL
    assert (big_row["answers"], big_row["answered"], big_row["not_found"]) == (20, 12, 8)
    outcomes = ("answers", "answered", "not_found", "unchecked", "answers_verified", "citations")
    assert [platform[key] for key in outcomes] == [20, 12, 8, 0, 20, 20]  # the big one's only
    left = {key: platform[key] - big_row[key] for key in ("answers", "answered", "not_found")}
    assert left == dict.fromkeys(left, 0)  # the platform minus the shown one: nothing of the small one
    assert [platform[key] for key in totals[1:]] == [0, 0, 0]  # no organisation with K of these
    assert [platform[key] for key in hidden[2:]] == [None, None, None]
    assert platform["runs"] == 1  # volumes not shares of a total stay every organisation's


def test_a_page_handed_to_the_local_reader_counts_under_its_documents_newest_ready_model(
    db: Database,
) -> None:
    """A rapidocr page with reader_refused or read_locally is the model's of its
    document's newest ready run (not an older ready one, not a newer failed one); keyless
    (no read_model), rapidocr's."""
    op, day = operator(db), window_day()
    customer, _ = new_customer(db)
    model, older, failed = new_model(), new_model(), new_model()
    for n in range(K):
        document_id = document(db, customer, at(day, 1))
        run(db, customer, document_id, at(day, 2), config={"read_model": older})
        run(db, customer, document_id, at(day, 3), config={"read_model": model})  # the newest ready
        run(db, customer, document_id, at(day, 4), status="failed", config={"read_model": failed})
        page(db, customer, document_id, model)
        page(db, customer, document_id, "rapidocr", ["reader_refused"], page_no=2)
        if n < 3:
            page(db, customer, document_id, "rapidocr", ["read_locally"], page_no=3)

    board = read(db, op, insights.model_board, day, day, "UTC")
    assert "rapidocr" not in {row["model"] for row in board}
    row = one(board, "model", model)
    assert (row["ocr_pages"], row["pages_refused"], row["pages_fell_back"], row["pages_disagree"]) == (
        2 * K + 3,
        K,
        3,
        0,
    )
    assert (one(board, "model", older)["ocr_pages"], one(board, "model", failed)["ocr_pages"]) == (0, 0)

    keyless_day = window_day()
    for document_id in read_documents(db, customer, K, at(keyless_day), None):
        page(db, customer, document_id, "rapidocr", ["read_locally"], page_no=2)
    [keyless] = read(db, op, insights.model_board, keyless_day, keyless_day, "UTC")
    assert (keyless["model"], keyless["ocr_pages"], keyless["pages_fell_back"], keyless["pages_refused"]) == (
        "rapidocr",
        2 * K,
        K,
        0,
    )


def test_active_people_windows_and_the_months_spend_end_on_today_in_the_zone(db: Database) -> None:
    """7 days and 30 days to today (today included), the previous 30 before those; the
    month is today's calendar month in the zone, its first second included."""
    op, today = operator(db), window_day()
    api = Api(db)
    customer, _ = new_customer(db)
    for back in (6, 7, 29, 30, 59, 60, -1):  # 60 days back and tomorrow are in no window
        user, _ = api.add_user(customer, "member")
        audit(db, customer, "auth.login", at(today - timedelta(days=back)), actor=user)  # 17:30 in India
    audit(db, customer, "document.upload", at(today))  # no actor: nobody to count
    first = today.replace(day=1)
    model = new_model()
    usage(db, customer, at(first - timedelta(days=1), 18), "scan", model, 1, "0.5")  # 23:30 the day before
    usage(db, customer, at(first - timedelta(days=1), 19), "scan", model, 1, "0.25")  # 00:30 on the 1st

    health = one(read(db, op, insights.org_health, today, IST), "customer_id", customer)
    people = (health["active_users_7d"], health["active_users_30d"], health["active_users_prev_30d"])
    assert people == (1, 3, 2)
    assert health["month_cost_usd"] == Decimal("0.25")


def test_the_last_active_day_is_the_newest_upload_question_or_run_within_366_days(db: Database) -> None:
    """In the zone; a sign-in or a superseded run is not activity; nothing in the 366 days
    to today (today - 365 is the first of them) is NULL."""
    op, today = operator(db), window_day()
    asked, reading, edge, past, never = (new_customer(db)[0] for _ in range(5))
    audit(db, asked, "chat.ask", at(today - timedelta(days=20)))
    audit(db, asked, "auth.login", at(today - timedelta(days=1)))
    run(
        db,
        asked,
        document(db, asked, at(today - timedelta(days=40))),
        at(today - timedelta(days=3)),
        status="superseded",
    )
    run(db, reading, document(db, reading, at(today - timedelta(days=40))), at(today - timedelta(days=10)))
    audit(db, edge, "document.version", at(today - timedelta(days=365)))
    audit(db, past, "document.upload", at(today - timedelta(days=366)))
    audit(db, past, "document.upload", at(today + timedelta(days=1)))  # after today: not yet

    health = read(db, op, insights.org_health, today, IST)
    days = [
        one(health, "customer_id", customer)["last_active_day"]
        for customer in (asked, reading, edge, past, never)
    ]
    assert days == [
        today - timedelta(days=20),
        today - timedelta(days=10),
        today - timedelta(days=365),
        None,
        None,
    ]


def test_the_overview_counts_pages_read_from_the_ledger_and_only_the_reported_cost(db: Database) -> None:
    """0034: pages read are the non-cached scan and upgrade calls, read again the careful and
    ensemble_scan ones, apart; a superseded run's pages still count (the ledger keeps them);
    the real cost is what OpenRouter reported, and a NULL or estimated cost counts its calls
    as unknown, never as dollars."""
    day, model = window_day(), new_model()
    customer, _ = new_customer(db)
    when = at(day)
    document_id = document(db, customer, when)
    run(db, customer, document_id, when, status="superseded", ocr=27)  # left out of the old pages
    usage(db, customer, when, "scan", model, 20, "0.40", document_id=document_id)
    usage(db, customer, when, "upgrade", model, 7, None, document_id=document_id)  # cost not reported
    usage(db, customer, when, "scan", model, 5, "0", cached=True, document_id=document_id)  # cache: no read
    usage(db, customer, when, "careful", model, 3, "0.09", document_id=document_id)
    usage(db, customer, when, "ensemble_scan", model, 2, "0.05", estimated=True, document_id=document_id)
    usage(db, customer, when, "chat", model, 4, "0.08")
    usage(db, customer, when, "ensemble_chat", model, 1, None)
    usage(db, customer, when, "card", model, 1, "0.10", document_id=document_id)
    [row] = of(read(db, operator(db), insights.daily, day, day, "UTC"), customer)
    assert (row["pages"], row["runs"]) == (0, 0)  # the old run-based count missed all 27 pages
    assert (row["pages_read"], row["pages_reread"], row["pages_read_costed"]) == (27, 5, 20)
    assert row["cost_real_usd"] == Decimal("0.67")  # 0.40 + 0.09 + 0.08 + 0.10 (+ the cached 0)
    assert row["cost_scan_real_usd"] == Decimal("0.49")  # 0.40 + 0.09
    assert row["cost_answer_real_usd"] == Decimal("0.08")
    assert (row["cost_unknown_calls"], row["scan_unknown_calls"], row["answer_unknown_calls"]) == (10, 9, 1)
