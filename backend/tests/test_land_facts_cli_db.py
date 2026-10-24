"""The land facts re-read (PRD 6 D3, ``kaagaz.jobs.land_facts``) against a real PostgreSQL, with
a fake model whose replies are recorded in ``fixtures/land_replies.json``: who is eligible,
the count and keys with no model call, the fenced write that keeps the document Ready and
people's corrections, the audit row that makes a replay a no-op, the checker on each value's
own box, the hard USD cap and billing as ``land``. Never run against a real stack here."""

import hashlib
import io
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

import pytest
from PIL import Image
from sqlalchemy import text

from kaagaz import cli
from kaagaz.ai import usage
from kaagaz.ai.client import ModelClient, ModelUnavailable
from kaagaz.chunking.piece import Piece
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.land import LAND_PROMPT_HASH
from kaagaz.extraction.values import DocumentFacts, ExtractedValue
from kaagaz.jobs import land_facts
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.spans import BoxLocation, PageInfo, Span
from tests.db_support import insert_document, new_customer
from tests.fakes import FakeChat, MemoryStorage
from tests.settings_factory import make_settings
from tests.test_extraction_land import REPLIES
from tests.test_pipeline_gemini import FakeChecker
from tests.test_word_review_db import change, key, load

pytestmark = pytest.mark.db

SETTINGS = make_settings(ocr_engine="rapidocr")
TEXTS = ("7/12 Extract", "Survey No: 45/2", "Village: Kusgaon", "Nikhil Varde", "50%", "Asha Rao")
REPLY = {"records": [{**REPLIES["second_window"]["records"][0],
    "village": {"label_quote": "Village", "value_quote": "Kusgaon"}, "parties": [
        {"role": "owner", "name_quote": "Nikhil Varde", "share_quote": "50%"},
        {"role": "owner", "name_quote": "Asha Rao", "share_quote": None},
]}]}  # fmt: skip
LAND = "SELECT record_key, field, value_text, value_number, label, label_reason FROM extracted_values"
LAND += " WHERE document_id = :d AND record_key LIKE 'land:%' ORDER BY record_key, field"


class Billed(FakeChat):
    """The model client as the ledger sees it: each call costs ``cost`` (None: not reported)."""

    def __init__(self, *replies: Any, cost: float | None = 0.01) -> None:
        super().__init__(*replies)
        self.cost = cost

    def chat_json(self, **kwargs: Any) -> dict[str, Any]:
        usage.record_call("google/gemini-3.1-flash", None, 1000, 100, 0, self.cost)
        return super().chat_json(**kwargs)


def _box(k: int) -> BoxLocation:
    return BoxLocation(1, 0.1, 0.05 + 0.06 * k, 0.6, 0.09 + 0.06 * k)


def _document(
    db: Database,
    customer: str,
    *,
    origin: str = "text_layer",
    extra: Sequence[ExtractedValue] = (),
    doc_type: str = "land_record",
    data: bytes = b"",
) -> str:
    """A ready document whose stored reading is TEXTS, one line each, with a card owner."""
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, detected_type="png",
                                   sha256=hashlib.sha256(data or uuid.uuid4().bytes).hexdigest())  # fmt: skip
    spans = tuple(
        Span(t, t, _box(k), 0.99 if origin == "ocr" else None, origin=origin) for k, t in enumerate(TEXTS)
    )
    read_by = "google/gemini-3.1-pro-preview" if origin == "ocr" else "text_layer"
    page = PageRecord(PageInfo(1, 600.0, 800.0, "px"), True, origin, read_by=read_by)
    card = ExtractedValue(
        "card", "owner_name", "Nikhil Varde", "Nikhil Varde", _box(3), label_reason="model_chosen"
    )
    piece = Piece(customer, document, "\n".join(TEXTS), tuple(map(_box, range(6))), "needs_review")
    outputs, facts = [ReaderOutput("first", "reader", "1", spans)], DocumentFacts(doc_type=doc_type)
    content = DocumentContent(customer, document, [page], outputs, [piece], [card, *extra], "reader", facts)
    PgPipelineStore(db).save(content)
    return document


def _reread(db: Database, customer: str, chat: FakeChat, *, max_usd: float = 1.0, **options: Any) -> str:
    out = io.StringIO()
    options = {"storage": MemoryStorage(), "checker": None, **options}
    land_facts.reread(db, chat, SETTINGS, [customer], max_usd=max_usd, out=out, **options)
    return out.getvalue()


def _rows(db: Database, customer: str, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(sql), params)]


def test_a_reread_writes_land_values_keys_and_its_audit_row_and_a_replay_does_nothing(db: Database) -> None:
    customer, _user = new_customer(db)
    document = _document(db, customer)
    printed = _reread(db, customer, Billed(REPLY))
    assert f"document {document}: done, 5 values, 1 calls, 1100 tokens, cost_usd 0.0100" in printed
    single = ("needs_review", "single_reader")  # a text layer with no second reading stored
    assert _rows(db, customer, LAND, d=document) == [
        ("land:1", "land_survey_number", "45/2", None, *single),
        ("land:1", "land_village", "Kusgaon", None, *single),
        ("land:1:party:1", "land_owner_name", "Nikhil Varde", None, *single),
        ("land:1:party:1", "land_share", "50%", Decimal(50), *single),
        ("land:1:party:2", "land_owner_name", "Asha Rao", None, *single),
    ]
    owners = _rows(
        db, customer, "SELECT field, value_text FROM extracted_values WHERE field LIKE '%owner_name'"
    )
    assert sorted(owners) == [
        ("land_owner_name", "Asha Rao"),
        ("land_owner_name", "Nikhil Varde"),
    ]  # card's gone
    assert len(_rows(db, customer, "SELECT 1 FROM value_keys WHERE document_id = :d", d=document)) == 4
    audited = "SELECT details ->> 'prompt', actor_user_id FROM audit_log WHERE action = :a"
    audits = _rows(db, customer, audited, a=land_facts.ACTION)
    assert audits == [(LAND_PROMPT_HASH, None)]
    assert _rows(db, customer, "SELECT status FROM documents WHERE id = :d", d=document) == [("ready",)]
    ledger = _rows(db, customer, "SELECT kind, document_id::text, calls FROM model_usage")
    assert ledger == [("land", document, 1)]
    assert _reread(db, customer, Billed()) == ""  # nothing eligible: no call, no line


def test_count_and_keys_only_call_no_model(db: Database) -> None:
    customer, _user = new_customer(db)
    document = _document(db, customer)
    _document(db, customer, doc_type="receipt")  # not a land document
    out = io.StringIO()
    assert (
        cli.main(["land-facts", "--count", "--customer-id", customer], db=db, out=out, settings=SETTINGS) == 0
    )
    assert out.getvalue() == f"customer {customer}: 1 eligible documents, {sum(map(len, TEXTS))} characters\n"
    with db.customer(customer) as conn:  # a land value stored with no key (as before 0049)
        copy = (
            "INSERT INTO extracted_values (customer_id, document_id, record_key, field, value_text,"
            " value_raw, page_no, region, reader, label, label_reason) SELECT customer_id, document_id,"
            " 'land:1',"
            " 'land_village', 'Kusgaon', 'Kusgaon', page_no, region, reader, label, label_reason"
            " FROM extracted_values WHERE document_id = :d"
        )
        conn.execute(text(copy), {"d": document})
    out = io.StringIO()
    assert (
        cli.main(["land-facts", "--keys-only", "--customer-id", customer], db=db, out=out, settings=SETTINGS)
        == 0
    )
    assert out.getvalue() == f"customer {customer}: 1 documents, 1 keys\n"
    assert _rows(db, customer, "SELECT kind, place_level, latin FROM value_keys") == [
        ("place", "village", "kusgaon")
    ]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--sample", "2", "--max-usd", "1"], "--sample N needs N of at least 1 and --customer-id"),
        (["--all"], "--sample and --all need --max-usd, more than 0"),
        (["--all", "--customer-id", "not-an-id", "--max-usd", "1"], "the customer id is not valid"),
    ],
)
def test_the_model_modes_need_a_customer_for_a_sample_and_always_a_cap(
    db: Database, argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    chat = cast(ModelClient, Billed())
    assert cli.main(["land-facts", *argv], db=db, out=io.StringIO(), settings=SETTINGS, model=chat) == 2
    assert capsys.readouterr().err == f"error: {message}\n"


def test_a_document_changed_meanwhile_or_corrected_is_left_as_it_is(db: Database) -> None:
    customer, _user = new_customer(db)
    corrected = ExtractedValue("land:1:party:1", "land_owner_name", "N. Varde", "Nikhil Varde", _box(3),
                               label="verified", label_reason="corrected")  # fmt: skip
    kept = _document(db, customer, extra=[corrected])
    changing = _document(db, customer)

    class ReadAgainMeanwhile(Billed):
        def chat_json(self, **kwargs: Any) -> dict[str, Any]:
            with db.customer(customer) as conn:  # a Read again, asked while the model reads
                conn.execute(text("UPDATE documents SET status = 'received' WHERE id = :d"), {"d": changing})
            return super().chat_json(**kwargs)

    printed = _reread(db, customer, ReadAgainMeanwhile(REPLY))
    assert f"document {kept}: corrected, skipped" in printed and f"document {changing}: changed" in printed
    assert _rows(db, customer, "SELECT value_text FROM extracted_values WHERE record_key LIKE 'land:%'") == [
        ("N. Varde",)
    ]
    assert _rows(db, customer, "SELECT 1 FROM audit_log WHERE action = :a", a=land_facts.ACTION) == []


def test_a_word_a_person_changed_is_written_into_the_new_values_once(db: Database) -> None:
    customer, user = new_customer(db)
    document = _document(db, customer)
    with db.customer(customer) as conn:  # "Asha" was read unsure: a mark on its line
        conn.execute(
            text("UPDATE reader_outputs SET output = output || CAST(:mark AS jsonb) WHERE document_id = :d"),
            {
                "d": document,
                "mark": '[{"text": "Asha", "raw": "Asha", "confidence": 0.5, "origin": "ocr",'
                ' "quality": "unsure", "region": {"kind": "box", "page": 1, "box": [0.1, 0.35, 0.3, 0.39]}}]',
            },
        )
    word = key(load(db, customer, document), "mark", "Asha")  # fmt: skip
    change(db, customer, document, word, "Asha", "Asha Devi", user)
    _reread(db, customer, Billed(REPLY))
    names = _rows(db, customer, "SELECT value_text FROM extracted_values WHERE field = 'land_owner_name'")
    assert sorted(names) == [("Asha Devi Rao",), ("Nikhil Varde",)]
    pieces = _rows(db, customer, "SELECT text FROM pieces WHERE document_id = :d", d=document)
    assert pieces == [("\n".join(TEXTS).replace("Asha", "Asha Devi"),)]  # never "Asha Devi Devi"


def test_the_checker_reads_each_values_own_box_on_a_page_a_model_read(db: Database) -> None:
    customer, _user = new_customer(db)
    picture = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(picture, "PNG")
    document = _document(db, customer, origin="ocr", data=picture.getvalue())
    storage = MemoryStorage()
    storage.put(customer, document, io.BytesIO(picture.getvalue()))
    checker = FakeChecker("Nikhil Varde")
    _reread(db, customer, Billed(REPLY), storage=storage, checker=checker)
    assert len(checker.crops) == 5  # each located box once
    labels = dict(_rows(db, customer, "SELECT value_text, label_reason FROM extracted_values"
                                      " WHERE record_key LIKE 'land:%'"))  # fmt: skip
    assert labels["Nikhil Varde"] == "readers_agree" and labels["50%"] == "readers_disagree"


def test_the_cap_and_an_unknown_cost_and_a_model_error_stop_the_run(db: Database) -> None:
    customer, _user = new_customer(db)
    first, second = _document(db, customer), _document(db, customer)
    printed = _reread(db, customer, Billed(REPLY, REPLY, cost=0.6), max_usd=1.0)
    assert f"document {first}: done" in printed and str(second) not in printed.split("stopped")[0]
    assert "stopped: the next document could pass --max-usd 1 (0.6000 spent)" in printed
    printed = _reread(db, customer, Billed(REPLY, cost=None))
    assert f"document {second}: done" in printed and "a call's cost is not known" in printed
    third = _document(db, customer)
    printed = _reread(db, customer, Billed(ModelUnavailable("server_error")))
    assert f"stopped: document {third}: the model failed (model_server_error)" in printed
    assert _rows(db, customer, LAND, d=third) == []


def test_the_audit_row_says_when_text_past_the_last_window_went_unread(db: Database) -> None:
    customer, _user = new_customer(db)
    document = _document(db, customer)
    one_window = make_settings(ocr_engine="rapidocr", extract_window_chars=40, extract_window_overlap=5,
                               extract_max_windows=1)  # fmt: skip
    out = io.StringIO()
    storage = MemoryStorage()
    land_facts.reread(db, Billed(REPLY), one_window, [customer], max_usd=1.0, out=out, storage=storage,
                      checker=None)  # fmt: skip
    assert f"document {document}: done" in out.getvalue()
    audited = "SELECT details ->> 'truncated' FROM audit_log WHERE action = :a"
    assert _rows(db, customer, audited, a=land_facts.ACTION) == [("true",)]
