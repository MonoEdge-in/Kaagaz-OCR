"""The Ensemble job against a real PostgreSQL (kaagaz/jobs/ensemble.py, kaagaz/db/ensemble.py,
0030 and 0032), with a fake scan reader.

A run reads page by page and saves each page as it goes; Stop keeps the finished pages; a
crash and a second delivery carry on with no row written twice; a word a person edits during
the run is skipped (user edits always win); Keep after an edit is changed_since; Keep never
Verifies; the last delivery fails the run and the document stays ready; every call is billed
to the asker as ensemble_scan; read again and the upgrade sweep leave a document in a run
alone. The API: test_api_ensemble.py; customer separation: test_isolation_api_ensemble.py.
"""

import contextlib
import hashlib
import io
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import text

from kaagaz.ai.usage import record_call
from kaagaz.db import ensemble as ensemble_sql
from kaagaz.db import word_review
from kaagaz.db.documents import EnsembleRunning, mark_for_reprocess, mark_for_upgrade
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ensemble import EnsembleRuns
from kaagaz.reading.ocr import DocumentPages, OcrLine, PageRead
from tests.db_support import insert_document, new_customer
from tests.fakes import MemoryStorage
from tests.settings_factory import make_settings
from tests.test_api_model_choice import add_model, allow
from tests.test_word_review_db import scanned

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
GEMINI = make_settings(ocr_engine="gemini")
READ_AGAIN = {1: "Total Rs 4,350 paid 12", 2: "Bill to Patil Stores"}


def two_pages() -> bytes:
    """A two-page TIFF, so each page can be drawn again."""
    first, second = Image.new("RGB", (600, 800), "white"), Image.new("RGB", (600, 800), "white")
    out = io.BytesIO()
    first.save(out, format="TIFF", save_all=True, append_images=[second])
    return out.getvalue()


class FakeReader:
    """The scan reader: each model reads ``texts[page]`` on whatever it is shown, billed 10
    and 5 tokens a read. ``before(model, page)`` runs first and may raise."""

    name = "gemini"
    version = "fake"

    def __init__(
        self, texts: dict[int, str], before: Callable[[str, int], None] = lambda model, page: None
    ) -> None:
        self.texts = texts
        self.before = before
        self.model: str | None = None
        self.reads: list[tuple[str, int]] = []

    @contextlib.contextmanager
    def document(self, pages: DocumentPages) -> Iterator[None]:
        self.model = pages.model
        yield
        self.model = None

    def read(self, image: Image.Image) -> list[OcrLine]:
        raise AssertionError("Ensemble reads numbered pages")

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        model = str(self.model)
        self.before(model, page_no)
        self.reads.append((model, page_no))
        record_call(model, "google-vertex", 10, 5, 0, 0.001)
        line = OcrLine(self.texts[page_no], 0, 0, image.width, image.height, None, kind="paragraph",
                       box_source="found")  # fmt: skip
        return PageRead(
            page_no=page_no,
            lines=(line,),
            quality="good",
            handwritten=False,
            scripts=(),
            issues=(),
            read_by=model,
        )


@contextlib.contextmanager
def org(db: Database) -> Iterator[dict[str, Any]]:
    """A customer with three usable models and the Ensemble grant, and a ready scan (3 flags on
    2 pages, test_word_review_db.scanned) whose file is stored."""
    customer, user = new_customer(db)
    lead, second, third = (add_model(db, name=name) for name in ("Lead", "Second", "Third"))
    allow(db, customer, [lead, second, third], default=lead)
    with db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customer_ai_settings (customer_id, ensemble) VALUES (:c, true)"),
            {"c": customer},
        )
    data = two_pages()
    with db.customer(customer) as conn:
        document = insert_document(
            conn, customer, uploaded_by=user, detected_type="tiff", sha256=hashlib.sha256(data).hexdigest()
        )
    PgPipelineStore(db).save(scanned(customer, document))
    storage = MemoryStorage()
    storage.put(customer, document, io.BytesIO(data))
    yield {
        "c": customer,
        "u": user,
        "d": document,
        "storage": storage,
        "models": [second, third],
        "lead": lead,
    }


def start(db: Database, o: dict[str, Any], scope: str = "flagged", keys: tuple[str, ...] = ()) -> str:
    with db.customer(o["c"]) as conn:
        run, made = ensemble_sql.start(
            conn, o["c"], o["d"], scope=scope, word_keys=list(keys), models=o["models"], user_id=o["u"]
        )
    assert made
    return str(run["id"])


def handler(db: Database, o: dict[str, Any], reader: FakeReader) -> EnsembleRuns:
    return EnsembleRuns(db, o["storage"], reader, settings=GEMINI, clock=lambda: T0)


def deliver(db: Database, o: dict[str, Any], reader: FakeReader, run_id: str) -> None:
    handler(db, o, reader)(Job(o["c"], o["d"], run_id=run_id), lambda: None)


def run_row(db: Database, o: dict[str, Any], run_id: str) -> Any:
    with db.customer(o["c"]) as conn:
        return ensemble_sql.get_run(conn, o["c"], o["d"], run_id)


def readings(db: Database, o: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """{before_text: (outcome, proposal)} of the document's readings."""
    with db.customer(o["c"]) as conn:
        rows = conn.execute(
            text("SELECT before_text, outcome, proposal FROM ensemble_readings WHERE document_id = :d"),
            {"d": o["d"]},
        ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def count(db: Database, o: dict[str, Any], sql: str) -> int:
    with db.customer(o["c"]) as conn:
        return int(conn.execute(text(sql), {"d": o["d"]}).scalar_one())


def key(db: Database, o: dict[str, Any], words: str) -> str:
    with db.customer(o["c"]) as conn:
        review = word_review.load(conn, o["c"], o["d"])
    return next(flag.key for flag in review.flags if flag.as_read == words)


def test_a_run_reads_page_by_page_votes_and_bills_the_asker(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)
        seen_done: list[list[int]] = []
        reader = FakeReader(READ_AGAIN)
        reader.before = lambda model, page: seen_done.append(list(run_row(db, o, run_id)["pages_done"]))
        deliver(db, o, reader, run_id)

        run = run_row(db, o, run_id)
        assert (run["status"], run["pages_done"], run["error_code"]) == ("done", [1, 2], None)
        assert seen_done == [[], [], [1], [1]]  # each page saved before the next is read
        assert sorted({model for model, _page in reader.reads}) == sorted(o["models"])  # never the lead
        assert readings(db, o) == {
            "4,850": ("changed", "4,350"),  # 2 of 3 read another text: a proposal only
            "[?]": ("changed", "12"),
            "Bill to Patil Stores": ("agreed", None),
        }
        assert (
            count(db, o, "SELECT count(*) FROM word_changes WHERE document_id = :d AND action = 'resolve'")
            == 1
        )
        assert count(db, o, "SELECT count(*) FROM pieces WHERE document_id = :d AND text LIKE '%4,350%'") == 0
        with db.customer(o["c"]) as conn:
            ledger = conn.execute(
                text("SELECT DISTINCT kind, user_id FROM model_usage WHERE document_id = :d"), {"d": o["d"]}
            ).all()
        assert [(kind, str(user)) for kind, user in ledger] == [("ensemble_scan", o["u"])]


def test_stop_keeps_the_finished_pages(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)

        def stop_on_page_one(model: str, page: int) -> None:
            with db.customer(o["c"]) as conn:
                ensemble_sql.stop(conn, o["c"], o["d"], run_id, user_id=o["u"], now=T0)

        deliver(db, o, FakeReader(READ_AGAIN, stop_on_page_one), run_id)
        run = run_row(db, o, run_id)
        assert (run["status"], run["pages_done"], run["error_code"]) == ("stopped", [1], None)
        assert set(readings(db, o)) == {"4,850", "[?]"}


def test_a_crash_and_a_second_delivery_carry_on_with_no_row_twice(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)
        crashed: list[int] = []

        def crash_once_on_page_two(model: str, page: int) -> None:
            if page == 2 and not crashed:
                crashed.append(page)
                raise RuntimeError("a bug")

        reader = FakeReader(READ_AGAIN, crash_once_on_page_two)
        with pytest.raises(RuntimeError):
            deliver(db, o, reader, run_id)
        assert (run_row(db, o, run_id)["status"], run_row(db, o, run_id)["pages_done"]) == ("running", [1])
        deliver(db, o, reader, run_id)  # the queue hands it out again
        deliver(db, o, reader, run_id)  # and once more after it finished: nothing is read

        assert run_row(db, o, run_id)["status"] == "done"
        assert [page for _model, page in reader.reads].count(1) == 2  # page 1 read once by each model
        assert count(db, o, "SELECT count(*) FROM ensemble_readings WHERE document_id = :d") == 3
        assert count(db, o, "SELECT count(*) FROM word_changes WHERE document_id = :d") == 1
        with db.customer(o["c"]) as conn:  # a page saved twice writes nothing new
            found = ensemble_sql.targets(conn, o["c"], o["d"], pages={2}, word_keys=None)[2]
            outcomes = [ensemble_sql.settle(t, [(o["lead"], t.text), ("x", t.text)]) for t in found]
            ensemble_sql.save_page(conn, o["c"], o["d"], run_id, 2, outcomes, now=T0)
        assert (
            count(
                db, o, "SELECT count(*) FROM ensemble_readings WHERE run_id IS NOT NULL AND document_id = :d"
            )
            == 3
        )


def test_a_word_a_person_edits_during_the_run_is_skipped(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)
        mark = key(db, o, "4,850")

        def edit_first(model: str, page: int) -> None:
            if page == 1 and model == o["models"][0]:
                with db.customer(o["c"]) as conn:
                    word_review.change_word(
                        conn,
                        o["c"],
                        o["d"],
                        mark,
                        expected_text="4,850",
                        new_text="4,860",
                        user_id=o["u"],
                        now=T0,
                    )

        deliver(db, o, FakeReader(READ_AGAIN, edit_first), run_id)
        assert readings(db, o)["4,850"] == ("skipped_user_edit", None)
        assert key(db, o, "4,850") == mark  # still the person's text
        with db.customer(o["c"]) as conn:
            assert word_review.latest_changes(conn, o["c"], o["d"])[mark]["after_text"] == "4,860"


def test_keep_writes_the_proposal_never_verifies_and_refuses_after_an_edit(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)
        deliver(db, o, FakeReader(READ_AGAIN), run_id)
        with db.customer(o["c"]) as conn:
            ids = dict(conn.execute(text("SELECT before_text, id FROM ensemble_readings WHERE run_id = :r"),
                                    {"r": run_id}).all())  # fmt: skip
            ensemble_sql.keep(conn, o["c"], o["d"], str(ids["4,850"]), user_id=o["u"], now=T0)
        with db.customer(o["c"]) as conn:
            review = word_review.load(conn, o["c"], o["d"])
            latest = review.latest()
            flag = review.flag(key(db, o, "4,850"))
            assert flag is not None
            assert (word_review.current_text(flag, latest), word_review.state(flag, latest)) == (
                "4,350",
                "resolved",
            )
            labels = conn.execute(
                text("SELECT label FROM extracted_values WHERE document_id = :d"), {"d": o["d"]}
            ).all()
            assert [label for (label,) in labels] == ["needs_review"]  # Keep never Verifies
            with pytest.raises(word_review.Refused) as decided:
                ensemble_sql.keep(conn, o["c"], o["d"], str(ids["4,850"]), user_id=o["u"], now=T0)
            assert decided.value.code == "already_decided"
        illegible = key(db, o, "[?]")
        with db.customer(o["c"]) as conn:  # a person edits it, then Keep comes
            word_review.change_word(conn, o["c"], o["d"], illegible, expected_text="[?]", new_text="15",
                                    user_id=o["u"], now=T0)  # fmt: skip
        with db.customer(o["c"]) as conn, pytest.raises(word_review.Refused) as since:
            ensemble_sql.keep(conn, o["c"], o["d"], str(ids["[?]"]), user_id=o["u"], now=T0)
        assert since.value.code == "changed_since"
        with db.customer(o["c"]) as conn:
            assert ensemble_sql.keep_all(conn, o["c"], o["d"], run_id, user_id=o["u"], now=T0) == {
                "kept": 0,
                "skipped": 1,
            }


def test_revert_keeps_the_word_flagged(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o, "word", (key(db, o, "4,850"),))
        deliver(db, o, FakeReader(READ_AGAIN), run_id)
        with db.customer(o["c"]) as conn:
            reading = conn.execute(
                text("SELECT id FROM ensemble_readings WHERE run_id = :r"), {"r": run_id}
            ).scalar_one()
            ensemble_sql.revert(conn, o["c"], o["d"], str(reading), user_id=o["u"], now=T0)
            review = word_review.load(conn, o["c"], o["d"])
        flag = review.flag(key(db, o, "4,850"))
        assert flag is not None and word_review.state(flag, review.latest()) == "open"


def test_the_last_delivery_fails_the_run_and_the_document_stays_ready(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)
        handler(db, o, FakeReader(READ_AGAIN)).exhausted(o["c"], run_id)
        run = run_row(db, o, run_id)
        assert (run["status"], run["error_code"], run["pages_failed"]) == (
            "failed",
            "retries_exhausted",
            [1, 2],
        )
        assert count(db, o, "SELECT count(*) FROM documents WHERE id = :d AND status = 'ready'") == 1


def test_a_reader_outage_fails_the_run_at_its_page(db: Database) -> None:
    with org(db) as o:
        run_id = start(db, o)

        def down_on_page_two(model: str, page: int) -> None:
            if page == 2:
                raise BackendUnavailable("gemini")

        deliver(db, o, FakeReader(READ_AGAIN, down_on_page_two), run_id)
        run = run_row(db, o, run_id)
        assert (run["status"], run["error_code"], run["pages_done"], run["pages_failed"]) == (
            "failed",
            "outage",
            [1],
            [2],
        )
        with db.customer(o["c"]) as conn:
            again = ensemble_sql.retry(conn, o["c"], o["d"], run_id, models=o["models"], user_id=o["u"])
        assert (again["pages"], str(again["retry_of"])) == ([2], run_id)


def test_read_again_and_the_upgrade_sweep_leave_a_document_in_a_run_alone(db: Database) -> None:
    with org(db) as o:
        start(db, o)
        with db.customer(o["c"]) as conn, pytest.raises(EnsembleRunning):
            mark_for_reprocess(conn, o["c"], o["d"], per_hour=100, requested_by=o["u"])
        with db.customer(o["c"]) as conn:
            assert mark_for_upgrade(conn, o["c"], o["d"]) is False
