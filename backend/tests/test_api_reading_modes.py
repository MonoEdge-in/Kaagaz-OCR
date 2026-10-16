"""Dynamic and Ensemble at upload (features 26 and 27, council 15 Oct 2026, C10, S8; Tushar:
the mode is called Dynamic): what ``/upload-limits`` offers, how an upload, a version or a
Read again asks for a reading (``read_mode``, ``read_model``, ``read_agents``), every 400
and 403 before anything is stored, the default (Dynamic when the organisation may use it),
the audit row's details, and a value's vote as members see it (no model names).

Without a database: ``kaagaz.api.readers`` with ``db.models`` swapped. Against a real
database (marker ``db``): an upload stores the reading, and a refused one stores nothing."""

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
        ("dynamic", "Dynamic", True),
        (DEFAULT, "Standard", False),
        (DEARER, "Careful", False),
    ]
    assert offered[0]["hint"] == "Picks the right reading for each page."


def test_no_dynamic_without_the_switch_but_dynamic_first_without_a_careful_tier() -> None:
    """Tushar, 16 Oct 2026: Dynamic works with one allowed model (Careful is the default model
    thinking harder), so it is offered first, the default, whenever the switch is on."""
    assert [reader["title"] for reader in readers.readers(ROWS, GEMINI)] == ["Standard", "Careful"]
    no_careful = [_row(DEFAULT, default=True), _row(OTHER)]
    offered = readers.readers(no_careful, GEMINI, dynamic=True)
    assert [(reader["id"], reader["default"]) for reader in offered] == [("dynamic", True), (DEFAULT, False)]
    assert readers.readers([], GEMINI, dynamic=True) == ocr_choices(GEMINI)


def test_ensemble_names_the_usable_models_only_with_the_switch_and_two_of_them() -> None:
    assert readers.ensemble(ROWS, True) == {
        "readers": [{"id": row["id"], "name": row["name"]} for row in ROWS],
        "max": 3,
        "main": DEFAULT,
    }
    assert readers.ensemble(ROWS, False) is None
    assert readers.ensemble(ROWS[:1], True) is None


def test_upload_limits_carry_no_price_or_cost_words(allowed: dict[str, Any]) -> None:
    offered, ensemble = readers.offer(_conn(), str(uuid.uuid4()), GEMINI)
    words = str([offered, ensemble]).lower()
    for banned in ("usd", "price", "cost", "budget", "spend", "$", "₹"):
        assert banned not in words


def test_keyless_mode_offers_auto_alone_and_asks_nothing() -> None:
    assert readers.offer(_conn(), str(uuid.uuid4()), KEYLESS) == (ocr_choices(KEYLESS), None)


# 400 before any query (S8).


@pytest.mark.parametrize(
    ("mode", "model", "agents"),
    [
        ("automatic", None, ()),  # not a mode
        ("single", None, (DEFAULT,)),  # readers without Ensemble
        (None, None, (DEFAULT, DEARER)),
        ("ensemble", None, (DEFAULT,)),  # one reader
        ("ensemble", None, (DEFAULT, DEARER, OTHER, "google/fourth")),  # four
        ("ensemble", None, (DEFAULT, DEFAULT)),  # twice the same
        ("ensemble", None, (DEFAULT, "Not a model")),
        ("ensemble", None, (DEFAULT, "google/" + "x" * 300)),  # too long
        ("ensemble", DEFAULT, (DEARER, OTHER)),  # Ensemble's main reader is not named apart
    ],
)
def test_s8_a_malformed_choice_is_400_before_any_query(
    mode: str | None, model: str | None, agents: tuple[str, ...]
) -> None:
    def check() -> None:
        readers.check_reading(_conn(), str(uuid.uuid4()), GEMINI, mode=mode, model=model, agents=agents)

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


def test_ensemble_without_the_switch_or_with_a_model_it_cant_use_is_403(allowed: dict[str, Any]) -> None:
    customer = str(uuid.uuid4())
    unusable = (DEFAULT, "google/not-allowed")
    assert _refused(
        lambda: readers.check_reading(_conn(), customer, GEMINI, mode="ensemble", agents=unusable)
    ) == (
        403,
        "model_not_allowed",
    )
    allowed["ensemble"] = False
    usable = (DEFAULT, DEARER)
    assert _refused(
        lambda: readers.check_reading(_conn(), customer, GEMINI, mode="ensemble", agents=usable)
    ) == (
        403,
        "ensemble_not_allowed",
    )


def test_p10_ensembles_main_reader_is_the_default_when_ticked_else_the_first(allowed: dict[str, Any]) -> None:
    customer = str(uuid.uuid4())
    ticked = readers.check_reading(
        _conn(), customer, GEMINI, mode="ensemble", agents=(OTHER, DEFAULT, DEARER)
    )
    assert ticked == readers.ReadingChoice("ensemble", DEFAULT, (OTHER, DEARER))
    first = readers.check_reading(_conn(), customer, GEMINI, mode="ensemble", agents=(OTHER, DEARER))
    assert first == readers.ReadingChoice("ensemble", OTHER, (DEARER,))


def test_c9_the_audit_row_names_the_reading_but_not_todays_default() -> None:
    assert readers.ReadingChoice("single").details() == {}
    assert readers.ReadingChoice(None).details() == {}
    assert readers.ReadingChoice("dynamic").details() == {"read_mode": "dynamic"}
    assert readers.ReadingChoice("ensemble", DEFAULT, (DEARER, OTHER)).details() == {
        "read_mode": "ensemble",
        "read_model": DEFAULT,
        "read_also": f"{DEARER},{OTHER}",
    }


def test_the_upload_forms_reading_fields() -> None:
    form = FormData([("read_mode", "ensemble"), ("read_agents", DEFAULT), ("read_agents", DEARER)])
    assert _form_reading(form) == _Asked("ensemble", None, (DEFAULT, DEARER))
    assert _form_reading(FormData([("read_mode", "")])) == _Asked()
    with pytest.raises(ApiError) as refused:
        _form_reading(FormData([("read_agents", DEFAULT)]))
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
def test_an_ensemble_upload_stores_its_main_reader_and_the_others(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    default, other = _ensemble_org(api, member.customer_id)

    offered = member.client.get("/api/upload-limits").json()["ensemble"]
    reply = _upload(member, {"read_mode": "ensemble", "read_agents": [other, default]})

    assert offered["main"] == default
    assert reply.status_code == 201, reply.text
    document = reply.json()["document"]["id"]
    with db.customer(member.customer_id) as conn:
        row = conn.execute(
            text("SELECT read_mode, read_model, read_also FROM documents WHERE id = :d"), {"d": document}
        ).one()
    assert tuple(row) == ("ensemble", default, [other])


@pytest.mark.db
def test_a_refused_reading_is_403_and_nothing_is_stored(db: Database) -> None:
    api = Api(db, settings=GEMINI)
    member = api.new_signed("member")
    default = add_model(api.db, name="Gemini 3.8 Flash")
    other = add_model(api.db, name="Sonnet 5.5", tokens=(500, 100))
    allow(api.db, member.customer_id, [default, other], default=default)  # no Ensemble switch

    refused = _upload(member, {"read_mode": "ensemble", "read_agents": [other, default]})
    dynamic = _upload(member, {"read_mode": "dynamic"})

    assert (refused.status_code, refused.json()["code"]) == (403, "ensemble_not_allowed")
    assert (dynamic.status_code, dynamic.json()["code"]) == (403, "dynamic_not_allowed")
    assert documents_of(db, member.customer_id) == 0
    assert api.queue.jobs == []
