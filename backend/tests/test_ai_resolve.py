"""Features 25-27 (council 15 Oct 2026, C3, S1, S2): who reads a document and who drafts an
answer, decided in ``kaagaz.db.models`` only. No database: ``FakeConn`` answers the
resolver's four statements (the scan list, the chat list, the settings, the document); the
SQL itself runs in test_isolation_ai_settings and test_migration_0026_db.

The vendor allow-list (OPENROUTER_MODEL_VENDORS) is a ceiling of its own; a mode the
organisation is no longer allowed falls back to single at job time with a note; a junk
``read_also`` id is never read; a thinking level the lead does not accept falls back to the
environment's; an answer choice the organisation may not make is refused with the API's code.
"""

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from kaagaz.db import models
from kaagaz.db.models import Agent, AiNotAllowed
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from kaagaz.settings import DEFAULT_MODEL_VENDORS, Settings, SettingsError
from tests.settings_factory import make_settings
from tests.test_settings import shipped

CUSTOMER = "00000000-0000-0000-0000-00000000000a"
DOCUMENT = "00000000-0000-0000-0000-00000000000c"
SETTINGS = make_settings()
FLASH, PRO, SONNET = "google/gemini-3.8-flash", "google/gemini-3.1-pro-preview", "anthropic/claude-sonnet-5.5"
# Changed on purpose (#463, 31 Oct 2026): the effective settings carry the scan cost settings too.
INHERIT = {
    "scan_thinking": None,
    "chat_levels": ["low"],
    "chat_default_level": "low",
    "dynamic": False,
    "scan_resolution": None,
    "careful_thinking": None,
    "careful_resolution": None,
    "page_max_output_tokens": None,
    "scan_reader": None,  # Changed on purpose (#473): who reads the pages, inherited
    "mistral_model": None,
    "ulc_ceiling_paise": None,  # Changed on purpose (#488): Ultra low cost's ceiling
}


class _Result:
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self._rows

    def one(self) -> Mapping[str, Any]:
        [row] = self._rows
        return row

    def first(self) -> Mapping[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeConn:
    """Answers ``kaagaz.db.models``' statements: ``scan`` and ``chat`` the area's rows as
    the SQL leaves them (the default first), ``settings`` the effective settings, and
    ``document`` the document's choice (None: no such document)."""

    def __init__(
        self,
        scan: Sequence[Mapping[str, Any]] = (),
        chat: Sequence[Mapping[str, Any]] = (),
        settings: Mapping[str, Any] | None = None,
        document: Mapping[str, Any] | None = None,
    ) -> None:
        self.answers: dict[object, Sequence[Mapping[str, Any]]] = {
            models._SCAN: scan,
            models._CHAT: chat,
            models._SETTINGS: [{**INHERIT, "ensemble": False, **(settings or {})}],
            models._DOCUMENT: [] if document is None else [document],
        }
        self.sent: list[tuple[object, Any]] = []

    def execute(self, statement: object, params: Any) -> _Result:
        self.sent.append((statement, params))
        return _Result(self.answers[statement])

    def conn(self) -> Connection:
        return cast(Connection, self)


def row(
    model: str,
    *,
    default: bool = False,
    providers: Sequence[str] = ("google-vertex",),
    efforts: Sequence[str] = ("low", "medium", "high"),
    tokens: tuple[int, int] | None = (1000, 500),
    price: tuple[float, float] = (2, 12),
    area: str = "scan",
) -> dict[str, Any]:
    """One usable-list row as ``_SCAN`` or ``_CHAT`` returns it."""
    return {
        "id": model,
        "name": model.split("/")[1].title(),
        "price_in_usd_per_m": price[0],
        "price_out_usd_per_m": price[1],
        "tokens_per_page_in": None if tokens is None else tokens[0],
        "tokens_per_page_out": None if tokens is None else tokens[1],
        f"is_default_{area}": default,
        "providers": list(providers),
        "efforts": list(efforts),
        "ledger_tokens_in": None,
        "ledger_tokens_out": None,
    }


def document(read_model: str | None = None, mode: str = "single", also: Sequence[str] = ()) -> dict[str, Any]:
    return {"read_model": read_model, "read_mode": mode, "read_also": list(also)}


def scan(conn: FakeConn, settings: Settings = SETTINGS) -> models.Reading:
    return models.resolve_scan(conn.conn(), CUSTOMER, DOCUMENT, settings=settings)


def agent(model: str) -> Agent:
    return Agent(model, "google-vertex", model.split("/")[1].title())


PLATFORM = Agent(SETTINGS.ocr_model, "google-vertex", SETTINGS.ocr_model)


# --- the vendor allow-list (S1) -----------------------------------------------------------


def test_the_vendor_allow_list_is_google_anthropic_qwen_and_meta_llama_unless_set() -> None:
    """Tushar, 15 Oct 2026: Sonnet 5.5 is allowed by default, so anthropic is in it.
    Changed on purpose (Tushar 18 Oct: models without thinking levels): qwen and meta-llama too."""
    four = {"google", "anthropic", "qwen", "meta-llama"}
    assert four == DEFAULT_MODEL_VENDORS
    assert Settings.from_env(shipped()).openrouter_model_vendors == four
    assert Settings.from_env(shipped(OPENROUTER_MODEL_VENDORS="")).openrouter_model_vendors == four
    assert Settings.from_env(shipped(OPENROUTER_MODEL_VENDORS="google")).openrouter_model_vendors == {
        "google"
    }


@pytest.mark.parametrize("value", ["Google", "google vendor", "x/y", "a" * 129, ","])
def test_a_vendor_that_is_not_a_model_id_part_stops_start_up_by_name(value: str) -> None:
    with pytest.raises(SettingsError, match="OPENROUTER_MODEL_VENDORS"):
        Settings.from_env(shipped(OPENROUTER_MODEL_VENDORS=value))


def test_a_model_of_a_vendor_outside_the_allow_list_is_never_used() -> None:
    """Enabled, verified and on an approved provider, but its vendor is not approved."""
    rows = [row("openai/gpt-6-luna", default=True), row(FLASH)]
    conn = FakeConn(scan=rows, document=document("openai/gpt-6-luna"))
    assert [found["id"] for found in models.usable_models(conn.conn(), CUSTOMER, settings=SETTINGS)] == [
        FLASH
    ]
    assert scan(conn).lead == PLATFORM  # its default is not usable: OCR_MODEL, as before
    google_only = make_settings(openrouter_model_vendors=frozenset({"google"}))
    conn = FakeConn(scan=[row(SONNET, default=True), row(FLASH)], document=document(SONNET))
    assert scan(conn).lead == Agent(SONNET, "google-vertex", "Claude-Sonnet-5.5")
    assert scan(conn, google_only).lead == PLATFORM


def test_the_scan_list_asks_for_the_readers_prompt_and_the_ledger_days() -> None:
    conn = FakeConn()
    models.usable_models(conn.conn(), CUSTOMER, settings=SETTINGS)
    assert conn.sent == [
        (
            models._SCAN,
            {"customer": CUSTOMER, "prompt": READER_PROMPT_VERSION, "days": models.LEDGER_DAYS},
        )
    ]


# --- resolve_scan: the lead, the mode and the thinking level (C3, S2) -----------------------


def test_the_lead_is_the_choice_else_the_default_else_ocr_model() -> None:
    rows = [row(PRO, default=True), row(FLASH)]
    assert scan(FakeConn(scan=rows, document=document(FLASH))) == models.Reading(
        "single", agent(FLASH), thinking="low"
    )
    assert scan(FakeConn(scan=rows, document=document("google/not-allowed"))).lead == agent(PRO)
    assert scan(FakeConn(scan=[row(FLASH)], document=document())).lead == PLATFORM  # no default
    assert scan(FakeConn(document=document())).lead == PLATFORM  # nothing set anywhere
    unseen = FakeConn(scan=rows, settings={"ensemble": True})  # another customer's document, or none
    assert scan(unseen) == models.Reading("single", PLATFORM, thinking="low")
    assert [statement for statement, _ in unseen.sent] == [models._DOCUMENT, models._SETTINGS]


def test_a_mode_no_longer_allowed_reads_single_with_a_note() -> None:
    """S2: the worker checks the upload's mode again; exactly one model reads each page."""
    rows = [row(PRO, default=True, tokens=(4000, 2000)), row(FLASH)]
    ensemble = document(PRO, "ensemble", [FLASH])
    # Changed on purpose (Ensemble after the scan, 16 Oct 2026): an old Ensemble document is
    # read single, with or without the switch, noted ensemble_after_scan.
    assert scan(FakeConn(scan=rows, document=ensemble)) == models.Reading(
        "single", agent(PRO), thinking="low", notes=("ensemble_after_scan",)
    )
    dynamic = FakeConn(
        scan=[row(FLASH, default=True), row(PRO, tokens=(4000, 2000))], document=document(None, "dynamic")
    )
    assert scan(dynamic) == models.Reading("single", agent(FLASH), thinking="low", notes=("mode_revoked",))


def test_an_old_ensemble_document_is_read_by_its_lead_alone() -> None:
    """Changed on purpose (Ensemble after the scan, 16 Oct 2026): with the switch on, an
    Ensemble document of before is read single by its lead; its read_also is never read."""
    rows = [row(PRO, default=True), row(FLASH), row(SONNET)]
    allowed = {"ensemble": True}
    reading = scan(
        FakeConn(scan=rows, settings=allowed, document=document(PRO, "ensemble", [FLASH, "google/junk"]))
    )
    assert reading == models.Reading("single", agent(PRO), thinking="low", notes=("ensemble_after_scan",))
    assert reading.config() == {
        "mode": "single",
        "reader": "ai",  # Changed on purpose (#398): the reader chosen, in the run's config
        "lead": {"model": PRO, "provider": "google-vertex"},
        "others": [],
        "careful": None,
        "thinking": "low",
        "notes": ["ensemble_after_scan"],
        "scan_reader": "openrouter",  # Changed on purpose (#473): who read the pages, in the run's config
        "mistral_model": "mistral-ocr-4-1",
    }


def test_dynamic_reads_low_first_with_the_careful_tier_or_else_the_lead() -> None:
    """Tushar, 16 Oct 2026: the first read thinks low (not the organisation's level); with no
    Careful tier other than the lead, the lead itself is Careful (gemini_ocr reads it at
    OCR_RETRY_THINKING_LEVEL), so Dynamic works with one allowed model."""
    allowed = {"dynamic": True, "scan_thinking": "high"}
    cheap, dear = row(FLASH, default=True), row(PRO, tokens=(4000, 2000))
    reading = scan(FakeConn(scan=[cheap, dear], settings=allowed, document=document(None, "dynamic")))
    assert (reading.mode, reading.lead, reading.careful, reading.thinking) == (
        "dynamic",
        agent(FLASH),
        agent(PRO),
        "low",
    )
    near = row(PRO, tokens=(1050, 520))  # under 1.1 times Standard a page: no Careful tier
    reading = scan(FakeConn(scan=[cheap, near], settings=allowed, document=document(None, "dynamic")))
    assert (reading.mode, reading.careful, reading.thinking, reading.notes) == (
        "dynamic",
        agent(FLASH),
        "low",
        (),
    )
    reading = scan(FakeConn(scan=[cheap, dear], settings=allowed, document=document(PRO, "dynamic")))
    assert (reading.mode, reading.careful, reading.notes) == ("dynamic", agent(PRO), ())  # the lead itself
    alone = scan(FakeConn(scan=[cheap], settings=allowed, document=document(None, "dynamic")))
    assert (alone.mode, alone.careful, alone.thinking) == ("dynamic", agent(FLASH), "low")


def test_dynamic_keeps_the_leads_level_when_it_does_not_think_low() -> None:
    allowed = {"dynamic": True, "scan_thinking": "high"}
    lead = row(PRO, default=True, efforts=["medium", "high"])
    reading = scan(FakeConn(scan=[lead], settings=allowed, document=document(None, "dynamic")))
    assert (reading.mode, reading.thinking, reading.notes) == ("dynamic", "high", ("thinking_fallback",))


def test_dynamic_is_the_organisations_else_the_platforms() -> None:
    """The effective switch: the organisation's, else the platform row's, with no default of
    its own (#405: the "else on" of 16 Oct 2026 was never reached; test_ai_settings_db)."""
    sql = str(models._SETTINGS)
    # Changed on purpose (0038): the platform's Cost saving switch can turn it off too.
    assert "COALESCE(s.dynamic, p.dynamic) AND COALESCE(w.cost_saving, true) AS dynamic" in sql
    off = scan(
        FakeConn(
            scan=[row(FLASH, default=True)], settings={"dynamic": False}, document=document(None, "dynamic")
        )
    )
    assert (off.mode, off.notes) == ("single", ("mode_revoked",))


def test_a_thinking_level_the_lead_does_not_accept_falls_back_to_the_environments() -> None:
    allowed = {"scan_thinking": "medium"}
    accepts = scan(FakeConn(scan=[row(PRO, default=True)], settings=allowed, document=document()))
    assert (accepts.thinking, accepts.notes) == ("medium", ())
    refuses = scan(
        FakeConn(scan=[row(PRO, default=True, efforts=["low"])], settings=allowed, document=document())
    )
    assert (refuses.thinking, refuses.notes) == ("low", ("thinking_fallback",))
    platform = scan(FakeConn(settings=allowed, document=document()))  # OCR_MODEL: its levels unknown
    assert (platform.thinking, platform.notes) == ("low", ("thinking_fallback",))


def test_the_old_resolve_is_the_leads_model_and_provider() -> None:
    conn = FakeConn(scan=[row(PRO, default=True, providers=["azure", "google-vertex"])], document=document())
    assert models.resolve(
        conn.conn(), CUSTOMER, DOCUMENT, settings=SETTINGS, prompt_version=READER_PROMPT_VERSION
    ) == (PRO, "google-vertex")
    with pytest.raises(ValueError, match="prompt version"):
        models.resolve(conn.conn(), CUSTOMER, DOCUMENT, settings=SETTINGS, prompt_version="g0")


# --- pick_tiers: Standard and Careful ---------------------------------------------------------


def test_careful_is_the_dearest_measured_model_at_least_dearer_than_standard() -> None:
    standard, dear, dearest, unmeasured = (
        row(FLASH, default=True),
        row(PRO, tokens=(2000, 1000)),
        row(SONNET, tokens=(4000, 2000)),
        row("google/unmeasured", tokens=None),
    )
    assert models.pick_tiers([standard, dear, dearest, unmeasured]) == (standard, dearest)
    assert models.pick_tiers([row(FLASH), row(PRO, tokens=(2000, 1000))])[0]["id"] == FLASH  # the cheapest
    assert models.pick_tiers([unmeasured, dearest]) == (dearest, None)


# --- resolve_chat: the answer's models and level (C3, C7) --------------------------------------


def chat(
    conn: FakeConn, agents: Sequence[str] | None = None, thinking: str | None = None
) -> models.ChatChoice:
    return models.resolve_chat(conn.conn(), CUSTOMER, agents, thinking, settings=SETTINGS)


def test_with_no_chat_models_the_answer_is_drafted_with_model_chat_as_before() -> None:
    fallback = Agent(SETTINGS.model_chat, "google-vertex", SETTINGS.model_chat)
    assert chat(FakeConn()) == models.ChatChoice([fallback], "low")
    assert chat(FakeConn(), [SETTINGS.model_chat]) == models.ChatChoice([fallback], "low")
    with pytest.raises(AiNotAllowed) as refused:
        chat(FakeConn(), [FLASH])
    assert refused.value.code == "model_not_allowed"


def test_the_default_chat_model_and_level_unless_chosen() -> None:
    rows = [row(PRO, default=True, area="chat"), row(FLASH, area="chat")]
    levels = {"chat_levels": ["low", "medium"], "chat_default_level": "medium"}
    assert chat(FakeConn(chat=rows, settings=levels)) == models.ChatChoice([agent(PRO)], "medium")
    assert chat(FakeConn(chat=rows, settings=levels), [FLASH], "low") == models.ChatChoice(
        [agent(FLASH)], "low"
    )


def test_the_backups_are_the_other_usable_models_that_take_the_level() -> None:
    """Never "busy" (24 Oct 2026): the models tried in turn when the chosen one is down."""
    rows = [
        row(PRO, default=True, area="chat"),
        row(FLASH, area="chat"),
        row(SONNET, area="chat", efforts=["low"]),  # not at medium: never a medium backup
        row("openai/gpt-6-luna", area="chat"),  # its vendor is not approved: never a backup
    ]
    levels = {"chat_levels": ["low", "medium"], "chat_default_level": "medium"}
    choice = chat(FakeConn(chat=rows, settings=levels))
    assert choice.agents == [agent(PRO)]
    assert choice.backups == (agent(FLASH),)
    assert chat(FakeConn(chat=rows, settings=levels), [FLASH], "low").backups == (agent(PRO), agent(SONNET))
    assert chat(FakeConn()).backups == ()  # MODEL_CHAT only: nothing to fall back to


@pytest.mark.parametrize(
    ("agents", "thinking", "allowed", "code"),
    [
        (["google/other"], None, {}, "model_not_allowed"),
        (["openai/gpt-6-luna"], None, {}, "model_not_allowed"),  # listed, but its vendor is not approved
        ([FLASH, PRO], None, {}, "ensemble_not_allowed"),
        ([FLASH], "medium", {}, "thinking_not_allowed"),  # not one of the organisation's levels
        ([SONNET], "medium", {"chat_levels": ["low", "medium"]}, "thinking_not_allowed"),  # Sonnet: low only
        ([FLASH, PRO], "high", {"ensemble": True, "chat_levels": ["low", "high"]}, "thinking_not_allowed"),
    ],
)
def test_a_choice_the_organisation_may_not_make_is_refused_with_its_code(
    agents: list[str], thinking: str | None, allowed: dict[str, Any], code: str
) -> None:
    rows = [
        row(PRO, default=True, area="chat"),
        row(FLASH, area="chat"),
        row(SONNET, area="chat", efforts=["low"]),
        row("openai/gpt-6-luna", area="chat"),
    ]
    with pytest.raises(AiNotAllowed) as refused:
        chat(FakeConn(chat=rows, settings=allowed), agents, thinking)
    assert refused.value.code == code


def test_with_ensemble_the_lead_is_the_default_when_chosen_else_the_first_named() -> None:
    rows = [row(PRO, default=True, area="chat"), row(FLASH, area="chat"), row(SONNET, area="chat")]
    allowed = {"ensemble": True, "chat_levels": ["low", "medium"], "chat_default_level": "low"}
    with_default = chat(FakeConn(chat=rows, settings=allowed), [FLASH, PRO], "medium")
    assert [one.model for one in with_default.agents] == [PRO, FLASH]
    assert with_default.thinking == "medium"
    without = chat(FakeConn(chat=rows, settings=allowed), [SONNET, FLASH])
    assert [one.model for one in without.agents] == [SONNET, FLASH]


@pytest.mark.parametrize("agents", [[], [FLASH, FLASH], [FLASH, PRO, SONNET, "google/four"]])
def test_agents_the_api_should_have_refused_are_a_programming_error(agents: list[str]) -> None:
    with pytest.raises(ValueError, match="agents"):
        chat(FakeConn(chat=[row(FLASH, area="chat")]), agents)
