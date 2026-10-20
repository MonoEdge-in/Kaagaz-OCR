"""Dynamic at upload (feature 26, council 15 Oct 2026, C10, S8; Tushar: the mode is called
Dynamic): what ``/upload-limits`` offers, how an upload, a version or a Read again asks for
a reading (``read_mode``, ``read_model``), every 400 and 403 before anything is stored, the
default (Dynamic when the organisation may use it), the audit row's details, and a value's
vote as members see it (no model names). Ensemble is offered only after the scan (Tushar,
16 Oct 2026): an upload that still asks for it (``read_mode`` ensemble, ``read_agents``)
is read as by default, never refused.

Without a database: ``kaagaz.api.readers`` with ``db.models`` swapped. Against a real
database (marker ``db``): an Ensemble upload is read normally, and a refused one stores nothing."""

import uuid
from collections.abc import Sequence
from typing import Any, cast

import httpx2 as httpx
import pytest
from sqlalchemy import Connection, RowMapping, text
from starlette.datastructures import FormData

from kaagaz.api import readers
from kaagaz.api.errors import ApiError
from kaagaz.api.routers.documents import _Asked, _form_reading
from kaagaz.api.views import value_votes
from kaagaz.db import models as models_sql
from kaagaz.db.engine import Database
from kaagaz.reading.ocr import ocr_choices
from tests.settings_factory import make_settings
from tests.test_api_model_choice import _row, add_model, allow, documents_of
from tests.test_api_support import CSRF, PDF_BYTES, Api, Signed

GEMINI = make_settings(ocr_engine="gemini")
KEYLESS = make_settings(ocr_engine="rapidocr")
DEFAULT, DEARER, OTHER = "google/default", "google/dearer", "anthropic/claude-sonnet-5.5"
ROWS = [
    _row(DEFAULT, default=True),  # 44,000 a page on the server's own scale
    _row(DEARER, tokens=(20_000, 4_000)),  # 88,000: Careful
    _row(OTHER),
]


class _NoQuery:
    """A connection that fails any query."""

    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("no query expected")


def _conn() -> Connection:
    return cast(Connection, _NoQuery())


@pytest.fixture
def allowed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``db.models``: the usable models are ROWS and the effective switches are the dict
    returned (change it in a test); each settings lookup is counted in ``asked``."""
    switches: dict[str, Any] = {"dynamic": True, "ensemble": True, "asked": 0}

    def usable_models(conn: Connection, customer_id: str, **kwargs: Any) -> Sequence[RowMapping]:
        return ROWS

    def effective_settings(conn: Connection, customer_id: str) -> dict[str, Any]:
        switches["asked"] += 1
        return switches

    monkeypatch.setattr(models_sql, "usable_models", usable_models)
    monkeypatch.setattr(models_sql, "effective_settings", effective_settings)
    return switches


def _refused(call: Any) -> tuple[int, str]:
    with pytest.raises(ApiError) as refused:
        call()
    return refused.value.status, refused.value.code


# What /upload-limits offers.


def test_dynamic_comes_first_and_is_the_default_when_allowed_with_a_careful_tier() -> None:
    offered = readers.readers(ROWS, GEMINI, dynamic=True)
    assert [(reader["id"], reader["title"], reader["default"]) for reader in offered] == [
        ("dynamic", "Automatic", True),
        (DEFAULT, "Standard", False),
        (DEARER, "Careful", False),
    ]
    # Changed on purpose (Cost saving, 16 Oct 2026): "Cost saving" is the super admin's name for
    # it; an organisation sees "Automatic", with no cost words.
    assert offered[0]["hint"] == "Reads clear pages quickly and takes extra care with hard-to-read words."


def test_no_dynamic_without_the_switch_but_dynamic_first_without_a_careful_tier() -> None:
    """Tushar, 16 Oct 2026: Dynamic works with one allowed model (Careful is the default model
    thinking harder), so it is offered first, the default, whenever the switch is on."""
    assert [reader["title"] for reader in readers.readers(ROWS, GEMINI)] == ["Standard", "Careful"]
    no_careful = [_row(DEFAULT, default=True), _row(OTHER)]
    offered = readers.readers(no_careful, GEMINI, dynamic=True)
    assert [(reader["id"], reader["default"]) for reader in offered] == [("dynamic", True), (DEFAULT, False)]
    assert readers.readers([], GEMINI, dynamic=True) == ocr_choices(GEMINI)


def test_upload_limits_carry_no_price_or_cost_words(allowed: dict[str, Any]) -> None:
    # Changed on purpose (Ensemble after the scan, 16 Oct 2026): offer gives the readers alone.
    # Cost saving (16 Oct 2026): the refusals an upload may get are checked too, and "saving".
    words = str(readers.offer(_conn(), str(uuid.uuid4()), GEMINI)).lower()
    words += " ".join(readers._REFUSED.values()).lower() + readers.NOT_ALLOWED.lower()
    for banned in ("usd", "price", "cost", "budget", "spend", "saving", "$", "₹"):
        assert banned not in words


def test_keyless_mode_offers_auto_alone_and_asks_nothing() -> None:
    assert readers.offer(_conn(), str(uuid.uuid4()), KEYLESS) == ocr_choices(KEYLESS)


# 400 before any query (S8).


def test_s8_a_mode_that_is_not_one_is_400_before_any_query() -> None:
    # Changed on purpose (Ensemble after the scan, 16 Oct 2026): readers named are ignored,
    # so the only malformed choice left is the mode.
    def check() -> None:
        readers.check_reading(_conn(), str(uuid.uuid4()), GEMINI, mode="automatic")

    assert _refused(check) == (400, "bad_request")


# 403 and the reading stored.


def test_with_nothing_asked_an_upload_is_read_with_dynamic_when_allowed(allowed: dict[str, Any]) -> None:
    customer = str(uuid.uuid4())
    assert readers.check_reading(_conn(), customer, GEMINI) == readers.ReadingChoice("dynamic")
    allowed["dynamic"] = False
    assert readers.check_reading(_conn(), customer, GEMINI) == readers.ReadingChoice("single")
    assert readers.check_reading(_conn(), customer, KEYLESS) == readers.ReadingChoice("single")


def test_a_read_again_that_names_nothing_keeps_the_documents_reading_and_asks_nothing() -> None:
    assert readers.check_reading(_conn(), str(uuid.uuid4()), GEMINI, keep=True) == readers.ReadingChoice(None)


def test_a_model_alone_is_single_and_dynamic_may_name_its_model(allowed: dict[str, Any]) -> None:
    customer = str(uuid.uuid4())
    assert readers.check_reading(_conn(), customer, GEMINI, model=DEARER) == readers.ReadingChoice(
        "single", DEARER
    )
    chosen = readers.check_reading(_conn(), customer, GEMINI, mode="dynamic", model=OTHER)
    assert chosen == readers.ReadingChoice("dynamic", OTHER)


def test_dynamic_not_allowed_is_403(allowed: dict[str, Any]) -> None:
    allowed["dynamic"] = False
    customer = str(uuid.uuid4())
    assert _refused(lambda: readers.check_reading(_conn(), customer, GEMINI, mode="dynamic")) == (
        403,
        "dynamic_not_allowed",
    )
    assert _refused(lambda: readers.check_reading(_conn(), customer, KEYLESS, mode="dynamic")) == (
        403,
        "dynamic_not_allowed",
    )


def test_ensemble_asked_for_is_read_as_by_default_and_noted(allowed: dict[str, Any]) -> None:
    """Changed on purpose (Ensemble after the scan, 16 Oct 2026): Ensemble at upload or Read
    again is never refused, with or without the switch; it is read as with no mode (a Read
    again too, never kept) and the audit row says ensemble_after_scan."""
    customer, noted = str(uuid.uuid4()), readers.ENSEMBLE_AFTER_SCAN
    asked = readers.check_reading(_conn(), customer, GEMINI, mode="ensemble")
    assert asked == readers.ReadingChoice("dynamic", note=noted)
    again = readers.check_reading(_conn(), customer, GEMINI, mode="ensemble", keep=True)
    assert again == readers.ReadingChoice("dynamic", note=noted)
    allowed["ensemble"], allowed["dynamic"] = False, False
    with_model = readers.check_reading(_conn(), customer, GEMINI, mode="ensemble", model=DEARER)
    assert with_model == readers.ReadingChoice("single", DEARER, note=noted)
    assert readers.check_reading(_conn(), customer, KEYLESS, mode="ensemble") == readers.ReadingChoice(
        "single", note=noted
    )
    assert readers.ReadingChoice("single", note=noted).details() == {"read_note": noted}


def test_c9_the_audit_row_names_the_reading_but_not_todays_default() -> None:
    assert readers.ReadingChoice("single").details() == {}
    assert readers.ReadingChoice(None).details() == {}
    assert readers.ReadingChoice("dynamic").details() == {"read_mode": "dynamic"}
    assert readers.ReadingChoice("single", DEFAULT).details() == {
        "read_mode": "single",
        "read_model": DEFAULT,
    }


def test_the_upload_forms_reading_fields() -> None:
    # Changed on purpose (Ensemble after the scan, 16 Oct 2026): read_agents is not read.
    form = FormData([("read_mode", "ensemble"), ("read_agents", DEFAULT), ("read_agents", DEARER)])
    assert _form_reading(form) == _Asked("ensemble", None)
    assert _form_reading(FormData([("read_mode", "")])) == _Asked()
    assert _form_reading(FormData([("read_agents", DEFAULT)])) == _Asked()
    with pytest.raises(ApiError) as refused:
        _form_reading(FormData([("read_mode", "automatic")]))
    assert refused.value.status == 400


# A value's vote, as members see it.


def test_a_values_vote_reaches_members_without_model_names() -> None:
    votes = {
        "value": "27AAKFS8812L1Z9",
        "agreed": 2,
        "readers": 3,
        "reads": [
            {"model": DEFAULT, "text": "27AAKFS88I2L1Z9"},
            {"model": DEARER, "text": "27AAKFS8812L1Z9"},
            {"model": OTHER, "text": None},
        ],
    }
    shown = value_votes(votes)
    assert shown == {
        "value": "27AAKFS8812L1Z9",
        "agreed": 2,
        "readers": 3,
        "reads": [
            {"main": True, "text": "27AAKFS88I2L1Z9"},
            {"main": False, "text": "27AAKFS8812L1Z9"},
            {"main": False, "text": None},
        ],
    }
    assert "google" not in str(shown) and "anthropic" not in str(shown)
    assert value_votes(None) is None


# Against a real database.


def _upload(signed: Signed, form: dict[str, str | list[str]]) -> httpx.Response:
    reply: httpx.Response = signed.client.post(
        "/api/documents", data=form, files={"file": ("bill.pdf", PDF_BYTES, "application/pdf")}, headers=CSRF
    )
    return reply


def _ensemble_org(api: Api, customer_id: str) -> tuple[str, str]:
    default = add_model(api.db, name="Gemini 3.8 Flash")
    other = add_model(api.db, name="Sonnet 5.5", tokens=(500, 100))
    allow(api.db, customer_id, [default, other], default=default)
    with api.db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customer_ai_settings (customer_id, ensemble) VALUES (:c, true)"),
            {"c": customer_id},
        )
    return default, other


@pytest.mark.db
def test_an_ensemble_upload_is_read_normally(db: Database) -> None:
    """Changed on purpose (Ensemble after the scan, 16 Oct 2026): /upload-limits offers no
    Ensemble, and an upload that still asks for it is read with the default reading."""
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    default, other = _ensemble_org(api, member.customer_id)

    limits = member.client.get("/api/upload-limits").json()
    reply = _upload(member, {"read_mode": "ensemble", "read_agents": [other, default]})

    assert "ensemble" not in limits
    assert reply.status_code == 201, reply.text
    document = reply.json()["document"]["id"]
    with db.customer(member.customer_id) as conn:
        row = conn.execute(
            text("SELECT read_mode, read_model, read_also FROM documents WHERE id = :d"), {"d": document}
        ).one()
    assert tuple(row) == ("dynamic", None, [])


@pytest.mark.db
def test_a_refused_reading_is_403_and_nothing_is_stored(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    default = add_model(api.db, name="Gemini 3.8 Flash")
    other = add_model(api.db, name="Sonnet 5.5", tokens=(500, 100))
    allow(api.db, member.customer_id, [default, other], default=default)
    with api.db.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customer_ai_settings (customer_id, dynamic) VALUES (:c, false)"),
            {"c": member.customer_id},
        )

    # Changed on purpose (Ensemble after the scan, 16 Oct 2026): Ensemble is no longer
    # refused; Dynamic turned off still is, and a model the organisation can't use.
    dynamic = _upload(member, {"read_mode": "dynamic"})
    unusable = _upload(member, {"read_model": "google/not-allowed"})

    assert (dynamic.status_code, dynamic.json()["code"]) == (403, "dynamic_not_allowed")
    assert (unusable.status_code, unusable.json()["code"]) == (403, "model_not_allowed")
    assert documents_of(db, member.customer_id) == 0
    assert api.queue.jobs == []
