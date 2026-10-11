"""Read with (model choice, council 11 Oct 2026, MS4, MS5, MC3): the models an upload may
read its scans with, and the server's check of a ``read_model``.

Without a database: the readers ``/upload-limits`` lists (Auto alone in keyless mode or
with no models; else the usable models, the default first, with an estimated price a
page), the 403 for a model the organisation can't use, and the ``read_model`` form field.
Against a real database (marker ``db``): uploads, versions and Read again store the model
and who asked, a model the organisation can't use is 403 with nothing stored, and a page
says the catalogue name of the model that read it. Across customers:
``test_isolation_model_choice.py``.
"""

import uuid
from collections.abc import Sequence
from typing import Any, cast

import httpx2 as httpx
import pytest
from sqlalchemy import Connection, RowMapping, text

from kaagaz.api import readers
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import _form_model
from kaagaz.db import models as models_sql
from kaagaz.db.engine import Database
from kaagaz.reading.gemini_ocr import GEMINI_OCR_PROMPT_VERSION
from kaagaz.reading.ocr import ocr_choices
from kaagaz.settings import Settings
from tests.settings_factory import make_settings
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed

GEMINI = make_settings(ocr_engine="gemini")
KEYLESS = make_settings(ocr_engine="rapidocr")
V2_BYTES = b"%PDF-1.7\n" + b"2" * 200


def _row(
    model_id: str, *, default: bool = False, tokens: tuple[int, int] | None = (10_000, 2_000)
) -> RowMapping:
    tokens_in, tokens_out = tokens if tokens is not None else (None, None)
    return cast(
        RowMapping,
        {
            "id": model_id,
            "name": f"Name of {model_id}",
            "price_in_usd_per_m": 2.0,
            "price_out_usd_per_m": 12.0,
            "tokens_per_page_in": tokens_in,
            "tokens_per_page_out": tokens_out,
            "is_default_scan": default,
            "providers": ["google-vertex"],
        },
    )


class _NoQuery:
    """A connection that fails any query: keyless mode and a None model ask nothing."""

    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("no query expected")


def _usable_returns(monkeypatch: pytest.MonkeyPatch, rows: Sequence[RowMapping]) -> list[dict[str, Any]]:
    """``db.models.usable_models`` (Track A) gives ``rows``; returns each call's arguments."""
    calls: list[dict[str, Any]] = []

    def usable_models(conn: Connection, customer_id: str, **kwargs: Any) -> Sequence[RowMapping]:
        calls.append({"customer_id": customer_id, **kwargs})
        return rows

    monkeypatch.setattr(models_sql, "usable_models", usable_models, raising=False)
    return calls


def _no_conn() -> Connection:
    return cast(Connection, _NoQuery())


# --- without a database ----------------------------------------------------------------


@pytest.mark.parametrize("settings", [KEYLESS, make_settings(ocr_engine="none")], ids=["rapidocr", "none"])
def test_keyless_mode_keeps_auto_alone_and_asks_nothing(settings: Settings) -> None:
    rows = readers.usable(_no_conn(), str(uuid.uuid4()), settings)
    assert rows == []
    assert readers.readers(rows, settings) == ocr_choices(settings)


def test_ms4_no_usable_model_keeps_auto_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _usable_returns(monkeypatch, [])
    customer = str(uuid.uuid4())
    rows = readers.usable(_no_conn(), customer, GEMINI)
    assert readers.readers(rows, GEMINI) == ocr_choices(GEMINI)
    assert calls == [{"customer_id": customer, "settings": GEMINI}]


def test_ms4_mc3_the_usable_models_default_first_with_a_price_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _usable_returns(
        monkeypatch,
        [
            _row("anthropic/claude-haiku-5.5", tokens=(1_000, 200)),
            _row("google/gemini-3.1-pro-preview", default=True),
            _row("openai/gpt-6-luna", tokens=None),
        ],
    )
    rows = readers.usable(_no_conn(), str(uuid.uuid4()), GEMINI)
    assert readers.readers(rows, GEMINI) == [
        {
            "id": "google/gemini-3.1-pro-preview",
            "title": "Name of google/gemini-3.1-pro-preview",
            "hint": "about $0.04 a page",  # 10,000 x $2/M + 2,000 x $12/M
            "available": True,
            "reason": None,
        },
        {
            "id": "anthropic/claude-haiku-5.5",
            "title": "Name of anthropic/claude-haiku-5.5",
            "hint": "under $0.01 a page",
            "available": True,
            "reason": None,
        },
        {
            "id": "openai/gpt-6-luna",
            "title": "Name of openai/gpt-6-luna",
            "hint": "Cost not measured yet",  # never a made-up figure
            "available": True,
            "reason": None,
        },
    ]


def test_mc3_the_estimate_a_page_is_the_measured_tokens_times_the_price() -> None:
    assert readers.page_usd(_row("google/a", tokens=(10_000, 2_000))) == pytest.approx(0.044)
    assert readers.page_usd(_row("google/a", tokens=None)) is None


def test_the_provider_is_the_first_inside_openrouter_providers() -> None:
    row = cast(
        RowMapping, {**_row("anthropic/claude-haiku-5.5"), "providers": ["amazon-bedrock", "google-vertex"]}
    )
    assert readers.page_provider(row, GEMINI) == "google-vertex"
    assert readers.page_provider(row, make_settings(openrouter_providers=frozenset({"azure"}))) is None


def test_ms5_a_model_outside_the_usable_ones_is_403_and_none_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _usable_returns(monkeypatch, [_row("google/gemini-3.1-pro-preview", default=True)])
    customer = str(uuid.uuid4())
    readers.check_read_model(_no_conn(), customer, GEMINI, None)
    readers.check_read_model(_no_conn(), customer, GEMINI, "google/gemini-3.1-pro-preview")
    with pytest.raises(ApiError) as refused:
        readers.check_read_model(_no_conn(), customer, GEMINI, "anthropic/claude-opus-5.5")
    assert (refused.value.status, refused.value.code) == (403, "model_not_allowed")


def test_ms5_keyless_mode_refuses_every_model() -> None:
    with pytest.raises(ApiError) as refused:
        readers.check_read_model(_no_conn(), str(uuid.uuid4()), KEYLESS, "google/gemini-3.1-pro-preview")
    assert refused.value.code == "model_not_allowed"


def test_the_read_model_form_field_is_optional_and_text() -> None:
    assert _form_model(None) is None
    assert _form_model("") is None
    assert _form_model("google/gemini-3.1-pro-preview") == "google/gemini-3.1-pro-preview"


# --- against a real database ------------------------------------------------------------


def add_model(db: Database, *, name: str = "Test model", tokens: tuple[int, int] = (10_000, 2_000)) -> str:
    """A catalogue model as the refresh, ``verify-models --record`` and an operator's enable
    leave it (the owner writes the columns the app can't). A new id each time: the
    catalogue is the platform's, shared by every test."""
    model_id = f"google/test-{uuid.uuid4().hex[:12]}"
    with db.engine.begin() as conn:
        operator = conn.execute(
            text(
                "INSERT INTO operators (email, display_name, password_hash)"
                " VALUES (:e, 'Ops', 'not-a-real-hash') RETURNING id"
            ),
            {"e": f"ops-{uuid.uuid4().hex[:12]}@example.test"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO model_catalog (id, name, price_in_usd_per_m, price_out_usd_per_m, providers,"
                " efforts, eligible, verified_prompt_version, verified_at, tokens_per_page_in,"
                " tokens_per_page_out, enabled_at, enabled_by)"
                " VALUES (:id, :name, 2, 12, ARRAY['google-vertex'], ARRAY['low'], true, :version, now(),"
                " :tokens_in, :tokens_out, now(), :operator)"
            ),
            {
                "id": model_id,
                "name": name,
                "version": GEMINI_OCR_PROMPT_VERSION,
                "tokens_in": tokens[0],
                "tokens_out": tokens[1],
                "operator": operator,
            },
        )
    return model_id


def allow(db: Database, customer_id: str, models: Sequence[str], *, default: str) -> None:
    """The organisation's models, as ``kaagaz_op_set_org_models`` leaves them."""
    with db.engine.begin() as conn:
        for model in models:
            conn.execute(
                text("INSERT INTO customer_models (customer_id, model, is_default_scan) VALUES (:c, :m, :d)"),
                {"c": customer_id, "m": model, "d": model == default},
            )


def upload(signed: Signed, data: bytes = PDF_BYTES, **form: str) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        "/api/documents", data=form, files={"file": ("bill.pdf", data, "application/pdf")}, headers=CSRF
    )
    return reply


def reading(db: Database, customer_id: str, document_id: str) -> tuple[str | None, str | None]:
    """(read_model, read_requested_by) of one document."""
    with db.customer(customer_id) as conn:
        row = conn.execute(
            text("SELECT read_model, read_requested_by FROM documents WHERE customer_id = :c AND id = :d"),
            {"c": customer_id, "d": document_id},
        ).one()
    return row[0], None if row[1] is None else str(row[1])


def documents_of(db: Database, customer_id: str) -> int:
    with db.customer(customer_id) as conn:
        return int(conn.execute(text("SELECT count(*) FROM documents")).scalar_one())


def two_models(api: Api, customer_id: str) -> tuple[str, str]:
    default = add_model(api.db, name="Default model")
    other = add_model(api.db, name="Other model", tokens=(500, 100))
    allow(api.db, customer_id, [default, other], default=default)
    return default, other


@pytest.mark.db
def test_ms4_upload_limits_list_the_usable_models_default_first(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    default, other = two_models(api, member.customer_id)

    offered = member.client.get("/api/upload-limits").json()["readers"]

    assert offered == [
        {
            "id": default,
            "title": "Default model",
            "hint": "about $0.04 a page",
            "available": True,
            "reason": None,
        },
        {
            "id": other,
            "title": "Other model",
            "hint": "under $0.01 a page",
            "available": True,
            "reason": None,
        },
    ]


@pytest.mark.db
def test_ms4_an_organisation_with_no_models_and_keyless_mode_offer_auto_alone(db: Database) -> None:
    for settings in (GEMINI, KEYLESS):
        api = Api(db, settings=settings)
        member = api.new_signed("member")
        if settings is KEYLESS:
            two_models(api, member.customer_id)  # set, but keyless mode reads locally
        offered = member.client.get("/api/upload-limits").json()["readers"]
        assert offered == ocr_choices(settings)


@pytest.mark.db
def test_ms5_an_upload_stores_the_model_chosen_and_who_asked(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    _, other = two_models(api, member.customer_id)

    reply = upload(member, read_model=other, ocr_choice="rapidocr")  # ocr_choice: still ignored

    assert reply.status_code == 201, reply.text
    document = reply.json()["document"]["id"]
    assert reading(db, member.customer_id, document) == (other, member.user_id)
    assert [job.document_id for job in api.queue.jobs] == [document]


@pytest.mark.db
def test_ms5_an_upload_without_a_model_is_read_with_the_default_and_billed_to_the_uploader(
    db: Database,
) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    two_models(api, member.customer_id)
    document = upload(member).json()["document"]["id"]
    assert reading(db, member.customer_id, document) == (None, member.user_id)


@pytest.mark.db
def test_ms5_a_model_the_organisation_cant_use_is_403_and_nothing_is_stored(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    two_models(api, member.customer_id)
    not_allowed = add_model(db, name="Not allowed here")  # in the catalogue, enabled, not this org's

    for model in (not_allowed, "google/never-listed"):
        reply = upload(member, read_model=model)
        assert (reply.status_code, reply.json()["error"]["code"]) == (403, "model_not_allowed")

    assert documents_of(db, member.customer_id) == 0
    assert api.storage.count() == 0
    assert api.queue.jobs == []


@pytest.mark.db
def test_ms5_keyless_mode_refuses_a_model_and_stores_nothing(db: Database) -> None:
    api = Api(db, settings=KEYLESS)
    member = api.new_signed("member")
    default, _ = two_models(api, member.customer_id)
    reply = upload(member, read_model=default)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "model_not_allowed")
    assert documents_of(db, member.customer_id) == 0


@pytest.mark.db
def test_ms5_a_new_version_stores_the_model_or_is_403_before_anything_is_stored(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    _, other = two_models(api, member.customer_id)
    first = upload(member).json()["document"]["id"]
    url = f"/api/documents/{first}/versions"

    refused = member.client.post(
        url,
        data={"read_model": add_model(db)},
        files={"file": ("bill v2.pdf", V2_BYTES, "application/pdf")},
        headers=CSRF,
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "model_not_allowed")
    assert documents_of(db, member.customer_id) == 1

    reply = member.client.post(
        url,
        data={"read_model": other},
        files={"file": ("bill v2.pdf", V2_BYTES, "application/pdf")},
        headers=CSRF,
    )
    assert reply.status_code == 201, reply.text
    assert reading(db, member.customer_id, reply.json()["document"]["id"]) == (other, member.user_id)


@pytest.mark.db
def test_read_again_with_a_model_stores_it_and_the_asker_and_audits_it(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    _, other = two_models(api, c)
    document = api.add_document(c, status="failed", failure_code="corrupt_file")

    reply = reviewer.client.post(
        f"/api/documents/{document}/reprocess", json={"read_model": other}, headers=CSRF
    )

    assert reply.status_code == 200, reply.text
    assert reading(db, c, document) == (other, reviewer.user_id)
    assert api.audit_rows(c)[-1] == (
        "document.reprocess",
        "document",
        document,
        {"status": "failed", "read_model": other},
    )


@pytest.mark.db
def test_read_again_without_a_model_keeps_the_documents_and_bills_the_asker(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    _, other = two_models(api, c)
    uploader = api.add_user(c, "member")[0]
    document = api.add_document(c, uploaded_by=uploader)
    with db.customer(c) as conn:
        conn.execute(text("UPDATE documents SET read_model = :m WHERE id = :d"), {"m": other, "d": document})

    assert reviewer.client.post(f"/api/documents/{document}/reprocess", headers=CSRF).status_code == 200

    assert reading(db, c, document) == (other, reviewer.user_id)
    assert api.audit_rows(c)[-1][3] == {"status": "ready"}


@pytest.mark.db
def test_read_again_with_a_model_the_organisation_cant_use_is_403_and_nothing_changes(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    two_models(api, c)
    uploader = api.add_user(c, "member")[0]
    document = api.add_document(c, status="failed", failure_code="corrupt_file", uploaded_by=uploader)
    with db.customer(c) as conn:
        conn.execute(
            text("UPDATE documents SET read_requested_by = :u WHERE id = :d"), {"u": uploader, "d": document}
        )

    reply = reviewer.client.post(
        f"/api/documents/{document}/reprocess", json={"read_model": add_model(db)}, headers=CSRF
    )

    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "model_not_allowed")
    assert reading(db, c, document) == (None, uploader)
    assert api.queue.jobs == []
    assert "document.reprocess" not in api.audit_actions(c)


@pytest.mark.db
def test_a_pages_read_again_takes_no_model_and_bills_the_asker(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    reviewer = api.new_signed("reviewer")
    c = reviewer.customer_id
    _, other = two_models(api, c)
    document = api.add_document(c)
    api.add_page(c, document)
    with db.customer(c) as conn:
        conn.execute(text("UPDATE pages SET route = 'ocr' WHERE document_id = :d"), {"d": document})
        conn.execute(text("UPDATE documents SET read_model = :m WHERE id = :d"), {"m": other, "d": document})

    reply = reviewer.client.post(
        f"/api/documents/{document}/pages/1/read-again", json={"read_model": "google/x"}, headers=CSRF
    )

    assert reply.status_code == 200, reply.text  # a body is not read: the page keeps the document's model
    assert reading(db, c, document) == (other, reviewer.user_id)


@pytest.mark.db
def test_a_page_says_the_catalogue_name_of_the_model_that_read_it(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    c = member.customer_id
    model = add_model(db, name="Gemini, as the catalogue names it")
    document = api.add_document(c, page_count=3)
    for page_no, read_by in ((1, model), (2, "rapidocr"), (3, "text_layer")):
        api.add_page(c, document, page_no)
        with db.customer(c) as conn:
            conn.execute(
                text("UPDATE pages SET read_by = :r WHERE document_id = :d AND page_no = :n"),
                {"r": read_by, "d": document, "n": page_no},
            )

    pages = member.client.get(f"/api/documents/{document}").json()["pages"]

    assert [(page["read_by"], page["read_by_name"]) for page in pages] == [
        (model, "Gemini, as the catalogue names it"),
        ("rapidocr", None),
        ("text_layer", None),
    ]


@pytest.mark.db
def test_the_detail_says_the_model_the_document_asked_for(db: Database) -> None:
    # Read again starts from it, so a document never moves to the default unasked (R1).
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    c = member.customer_id
    _, other = two_models(api, c)
    chosen, plain = api.add_document(c), api.add_document(c)
    with db.customer(c) as conn:
        conn.execute(text("UPDATE documents SET read_model = :m WHERE id = :d"), {"m": other, "d": chosen})

    assert member.client.get(f"/api/documents/{chosen}").json()["read_model"] == other
    assert member.client.get(f"/api/documents/{plain}").json()["read_model"] is None
