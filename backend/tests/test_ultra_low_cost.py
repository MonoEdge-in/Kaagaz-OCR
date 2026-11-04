"""Ultra low cost, settings and resolving without a database (#488, Tushar, 4 Nov 2026): the third
scan reader reads every page once, single, at minimal thinking, with the default Gemini model
that has a price (else OCR_MODEL), under a ceiling a page (20 to 40 paise, NULL: 40), whatever the
document asked for (mode, "Read with", Save, High, Max); the local reader only stays the local
reader only; Ensemble is refused; the reader choice offers Standard alone. No organisation that
did not choose it reads differently: ``resolve_scan`` and ``scope_reading`` never read the ceiling
then. The SQL and the API: test_ultra_low_cost_db."""

import itertools
from typing import Any, cast

import pytest

from kaagaz.api import readers
from kaagaz.db import ai_settings, models
from kaagaz.db.models import Agent, AiNotAllowed
from kaagaz.jobs import accuracy
from kaagaz.reading.ocr import UlcCeiling
from kaagaz.settings import DEFAULT_PROVIDER, NO_THINKING
from tests.settings_factory import make_settings
from tests.test_ai_resolve import CUSTOMER, DOCUMENT, INHERIT, FakeConn, row

GEMINI = make_settings(ocr_engine="gemini", page_routing=True)
LEAD, CAREFUL = "google/gemini-3.5-flash-lite", "google/gemini-3.1-pro-preview"
ROWS = [
    row(LEAD, default=True, efforts=("minimal", "low", "medium", "high"), price=(0.3, 2.5)),
    row(CAREFUL, efforts=("low", "high"), price=(2, 12)),
]
ULC: dict[str, Any] = {"scan_reader": "ultra_low_cost", "ulc_ceiling_paise": 30}
MODES = ("single", "dynamic", "ensemble")
LEVELS = (None, "none", "save", "low", "high", "max")
READ_WITH = (None, LEAD, CAREFUL)


def _document(mode: str, read_model: str | None, level: str | None) -> dict[str, Any]:
    """A document's choice, with the organisation's locked scan level (None: none saved)."""
    return {
        "read_model": read_model,
        "read_mode": mode,
        "scan_settings": None if level is None else [[True, level, True]],
    }


def _scan(settings: dict[str, Any], mode: str, read_model: str | None, level: str | None) -> models.Reading:
    conn = FakeConn(scan=ROWS, settings=settings, document=_document(mode, read_model, level))
    return models.resolve_scan(conn.conn(), CUSTOMER, DOCUMENT, settings=GEMINI)


def test_with_it_off_no_reading_changes_and_the_ceiling_is_never_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hard requirement: for every mode, level, "Read with", Dynamic and reader other than
    Ultra low cost, a saved ceiling changes nothing, and the settings without the field at all
    (as before 0070) read the same: the field is never looked at."""
    monkeypatch.delitem(INHERIT, "ulc_ceiling_paise")  # the effective settings as before 0070
    for mode, read_model, level, dynamic, reader in itertools.product(
        MODES, READ_WITH, LEVELS, (False, True), (None, "openrouter", "mistral")
    ):
        before = {"dynamic": dynamic, "scan_reader": reader}  # INHERIT has no ceiling: KeyError if read
        assert "ulc_ceiling_paise" not in {**INHERIT, **before}
        old = _scan(before, mode, read_model, level)
        assert _scan({**before, "ulc_ceiling_paise": 25}, mode, read_model, level) == old
        assert old.ulc is None and "ultra_low_cost" not in old.notes
        assert "ulc" not in old.config()
    for dynamic, reader in itertools.product((False, True), (None, "openrouter", "mistral")):
        allowed = {**INHERIT, "dynamic": dynamic, "scan_reader": reader}
        old = models.scope_reading(cast(Any, ROWS), allowed, settings=GEMINI)
        assert (
            models.scope_reading(cast(Any, ROWS), {**allowed, "ulc_ceiling_paise": 25}, settings=GEMINI)
            == old
        )
        assert old.ulc is None


def test_it_reads_every_page_once_at_minimal_with_the_default_gemini_whatever_was_asked() -> None:
    for mode, read_model, level, dynamic in itertools.product(MODES, READ_WITH, LEVELS, (False, True)):
        reading = _scan({**ULC, "dynamic": dynamic}, mode, read_model, level)
        if level == "none":  # the local reader only stays so: no model, no cost
            assert reading.mode == "local", (mode, read_model, level)
            continue
        assert (reading.mode, reading.lead.model, reading.thinking) == ("single", LEAD, "minimal"), (
            mode,
            read_model,
            level,
            dynamic,
        )
        assert (reading.careful, reading.others, reading.routes, reading.notes) == (
            None,
            (),
            False,
            ("ultra_low_cost",),
        )
        assert reading.ulc == UlcCeiling(usd=0.30 / models.INR_PER_USD, paise=30)
        assert reading.scan_reader == "ultra_low_cost"
    assert models.INR_PER_USD == 88  # frontend/components/operator/models.ts


def test_the_estimate_reads_it_the_same_way() -> None:
    allowed = {**INHERIT, **ULC, "dynamic": True}
    estimate = models.scope_reading(cast(Any, ROWS), allowed, settings=GEMINI)
    assert estimate == _scan({**ULC, "dynamic": True}, "dynamic", None, None)


def test_no_ceiling_saved_is_40_paise_and_the_reading_records_it() -> None:
    reading = _scan({"scan_reader": "ultra_low_cost", "ulc_ceiling_paise": None}, "single", None, None)
    assert reading.ulc == UlcCeiling(usd=0.40 / 88, paise=40)
    assert reading.config()["ulc"] == {"paise": 40, "usd": 0.40 / 88}


@pytest.mark.parametrize(
    "rows",
    [
        [],  # no usable model: OCR_MODEL, as before model choice
        [row("anthropic/claude-sonnet-5.5", default=True, efforts=("low",))],  # not a Gemini
        [row(LEAD, default=True, price=(0, 2.5))],  # a price not known
        # #488 review (R3): a price a request or an image not known, so no call is known to fit.
        [row(LEAD, default=True, price=(0.3, 2.5), per_call=(None, 0.0))],
        [row(LEAD, default=True, price=(0.3, 2.5), per_call=(0.0, None))],
        [row(LEAD, price=(0.3, 2.5))],  # no default
    ],
)
def test_without_a_priced_default_gemini_the_lead_is_ocr_model(rows: list[dict[str, Any]]) -> None:
    conn = FakeConn(scan=rows, settings=ULC, document=_document("single", None, None))
    reading = models.resolve_scan(conn.conn(), CUSTOMER, DOCUMENT, settings=GEMINI)
    assert reading.lead == Agent(GEMINI.ocr_model, DEFAULT_PROVIDER, GEMINI.ocr_model)
    assert (reading.thinking, reading.mode) == ("minimal", "single") and reading.thinking != NO_THINKING


def test_a_known_price_a_call_that_is_not_0_still_leads() -> None:
    """#488 review (R3): the reader charges a price a request or an image, so it never stops the
    default Gemini leading; only one not known does."""
    dear = [row(LEAD, default=True, efforts=("minimal",), price=(0.3, 2.5), per_call=(0.0001, 0.001238))]
    conn = FakeConn(scan=dear, settings=ULC, document=_document("single", None, None))
    assert models.resolve_scan(conn.conn(), CUSTOMER, DOCUMENT, settings=GEMINI).lead.model == LEAD


def test_ensemble_is_refused_like_mistral() -> None:
    conn = FakeConn(scan=ROWS, settings={**ULC, "ensemble": True}, document=_document("single", None, None))
    with pytest.raises(AiNotAllowed) as refused:
        models.resolve_ensemble(conn.conn(), CUSTOMER, DOCUMENT, settings=GEMINI)
    assert refused.value.code == "ensemble_not_allowed"
    plain = FakeConn(scan=ROWS, settings={"ensemble": True}, document=_document("single", None, None))
    assert models.resolve_ensemble(plain.conn(), CUSTOMER, DOCUMENT, settings=GEMINI).mode == "ensemble"


def test_the_scan_levels_are_clamped_to_none_and_low() -> None:
    for scan_thinking, dynamic in itertools.product((None, "low", "high"), (False, True)):
        ceiling = {"scan_thinking": scan_thinking, "dynamic": dynamic, **ULC}
        assert ai_settings.scan_ceiling(ceiling, GEMINI) == (("none", "low"), "low")
    for saved in ("save", "low", "high", "max"):
        task = ai_settings.resolve(
            "scan", {("scan", True): (saved, True)}, *ai_settings.scan_ceiling(ULC, GEMINI)
        )
        assert task.effective == "low", saved
    keyless = make_settings()  # no reading model: None only, as before
    assert ai_settings.scan_ceiling({"scan_thinking": "high", "dynamic": True, **ULC}, keyless) == (
        ("none",),
        "none",
    )
    off = {"scan_thinking": "high", "dynamic": True, "scan_reader": "mistral"}
    assert ai_settings.scan_ceiling(off, GEMINI) == (("none", "save", "low", "high", "max"), "low")


def test_the_reader_choice_offers_standard_alone() -> None:
    conn = FakeConn(scan=ROWS, settings={**ULC, "dynamic": True})
    [standard] = readers.offer(conn.conn(), CUSTOMER, GEMINI)
    assert (standard["id"], standard["title"], standard["default"]) == (LEAD, "Standard", True)
    off = FakeConn(scan=ROWS, settings={"dynamic": True})
    assert [one["title"] for one in readers.offer(off.conn(), CUSTOMER, GEMINI)] == [
        "Automatic",
        "Standard",
        "Careful",
    ]


def test_a_stale_pick_is_accepted_and_read_as_ultra_low_cost() -> None:
    for settings in ({**ULC, "dynamic": False}, {**ULC, "dynamic": True}):
        conn = FakeConn(scan=ROWS, settings=settings)
        for mode, model in (("dynamic", None), (None, CAREFUL), ("single", CAREFUL)):
            choice = readers.check_reading(conn.conn(), CUSTOMER, GEMINI, mode=mode, model=model)
            assert choice.mode in ("single", "dynamic") and choice.model == model
            reading = _scan(settings, choice.mode or "single", choice.model, None)
            assert (reading.lead.model, reading.mode, reading.ulc) == (
                LEAD,
                "single",
                UlcCeiling(0.30 / 88, 30),
            )
    off = FakeConn(scan=ROWS, settings={"dynamic": False})
    with pytest.raises(Exception) as refused:
        readers.check_reading(off.conn(), CUSTOMER, GEMINI, mode="dynamic")
    assert getattr(refused.value, "code", None) == "dynamic_not_allowed"  # unchanged without it


def test_the_accuracy_check_names_its_model_ulc_paise_model() -> None:
    assert accuracy.ulc_model(30, LEAD) == f"ulc/30/{LEAD}"
    assert accuracy.ulc_parts(f"ulc/30/{LEAD}") == (30, LEAD)
    assert accuracy.ulc_parts(LEAD) is None and accuracy.ulc_parts("mistral/mistral-ocr-4-1") is None
    assert accuracy.ulc_parts("ulc/x/google/a") is None
