"""Which models read a document's scans and draft an answer (model choice, council 11 Oct
2026, MS1, MS4, MS5; features 25-27, council 15 Oct 2026, C3).

The only place that decides it. Every area (scan and chat) and every field inherits on its
own: the organisation's value, else the platform defaults (``platform_models``,
``platform_ai_settings``, 0026), else the environment (OCR_MODEL, MODEL_CHAT,
OCR_THINKING_LEVEL). An organisation inherits an area's models when it has no
``customer_models`` row for that area, and a setting when its ``customer_ai_settings``
field is NULL (no row: every field). Ensemble is the organisation's own switch, never
inherited (default off). Dynamic is on when neither the organisation nor the platform sets
it (Tushar, 16 Oct 2026: Dynamic is the default reading); either may turn it off.

A model is usable while the area's list names it, it is eligible for that use, the
operator enabled it, it is verified (scan: for the reader's prompt version; chat: for the
answer prompt's hash), one of its providers is in OPENROUTER_PROVIDERS (the first such, in
the catalogue's order, is called) and its vendor, the part of its id before "/", is in
OPENROUTER_MODEL_VENDORS (S1). The browser's choice is never trusted: ``resolve_scan`` is
the worker's own check at job time (S2), so a model or a mode the organisation is no
longer allowed falls back, with a note for ``processing_runs.config.reading``.

* ``usable_models``: the scan models an upload may name, the default first.
* ``resolve_scan``: the ``Reading`` of one document (mode, lead, others, careful, thinking).
* ``resolve_ensemble``: who reads a document again for Ensemble after the scan.
* ``usable_chat_models`` and ``resolve_chat``: the ``ChatChoice`` of one question; a
  choice the organisation may not make raises ``AiNotAllowed`` (the API's 403 codes).
* ``pick_tiers``: Standard and Careful among the usable scan models (the API's "Read
  with" and Dynamic's Careful tier use the same rule).

Dynamic (Tushar, 16 Oct 2026): the first read of every page thinks at DYNAMIC_FIRST_THINKING
(low; the lead's own level when it does not take low), and Careful reads the pages that need
it at OCR_RETRY_THINKING_LEVEL (``gemini_ocr._careful``), with the Careful tier, or with the
lead itself when there is no other (so Dynamic works with one allowed model).

The ledger columns (``ledger_tokens_in``/``_out``) are the average tokens a scanned page
took in the organisation's own ledger of the last LEDGER_DAYS days (MC3); prices never
leave the server (Tushar, 15 Oct 2026: org admins and members never see cost).

Time: one statement for the area's list (a few rows, each with one range read of its
ledger rows, the model_usage_period index) and one for the settings.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, RowMapping, text

from kaagaz.answering.draft import ANSWER_PROMPT_HASH
from kaagaz.reading.ocr import GEMINI, READER_PROMPT_VERSION
from kaagaz.settings import DEFAULT_PROVIDER, Settings

# MC3: the days of ledger a page's estimate averages over.
LEDGER_DAYS = 30
MODES = ("single", "dynamic", "ensemble")  # documents.read_mode (0026)
LEVELS = ("low", "medium", "high")  # the thinking levels an operator may set
MAX_AGENTS = 3  # the lead and at most two others (documents.read_also holds two)
# Careful is offered only when it costs at least this much more a page than Standard.
DEARER = 1.1
DYNAMIC_FIRST_THINKING = "low"  # Dynamic's quick first read (Tushar, 16 Oct 2026)

# The organisation's own models of an area, else the platform's (only when it has none of
# its own), each with the catalogue row and the average tokens of its scan calls (not
# cached) in the organisation's ledger of the last LEDGER_DAYS days; NULL with none. The
# area's rules (eligible, enabled, verified) are in SQL; the provider and the vendor in
# Python (``_provider``). The default first, then by name.
_AREA = """
    WITH own AS (
        SELECT m.model, m.is_default_{area} AS is_default FROM customer_models m
        WHERE m.customer_id = :customer AND m.{area}
    ), area AS (
        SELECT own.model, own.is_default FROM own
        UNION ALL
        SELECT p.model, p.is_default_{area} FROM platform_models p
        WHERE p.{area} AND NOT EXISTS (SELECT 1 FROM own)
    )
    SELECT c.id, c.name, c.price_in_usd_per_m, c.price_out_usd_per_m, c.tokens_per_page_in,
           c.tokens_per_page_out, a.is_default AS is_default_{area}, c.providers, c.efforts,
           s.ledger_tokens_in, s.ledger_tokens_out
    FROM area a
    JOIN model_catalog c ON c.id = a.model
    LEFT JOIN LATERAL (
        SELECT sum(u.input_tokens) / sum(u.calls) AS ledger_tokens_in,
               sum(u.output_tokens) / sum(u.calls) AS ledger_tokens_out
        FROM model_usage u
        WHERE u.customer_id = :customer AND u.model = c.id AND u.kind = 'scan' AND NOT u.cached
          AND u.created_at >= now() - make_interval(days => :days)
    ) s ON true
    WHERE c.enabled_at IS NOT NULL AND {usable}
    ORDER BY a.is_default DESC, c.name, c.id
"""
_SCAN = text(_AREA.format(area="scan", usable="c.eligible AND c.verified_prompt_version = :prompt"))
_CHAT = text(_AREA.format(area="chat", usable="c.chat_eligible AND c.chat_verified_prompt = :prompt"))

# The effective settings (C2): the organisation's field, else the platform's. Ensemble is
# the organisation's alone. One row (0026 seeds the platform row; no row, the env only).
_SETTINGS = text(
    "SELECT COALESCE(s.scan_thinking, p.scan_thinking) AS scan_thinking,"
    " COALESCE(s.chat_levels, p.chat_levels) AS chat_levels,"
    " COALESCE(s.chat_default_level, p.chat_default_level) AS chat_default_level,"
    " COALESCE(s.dynamic, p.dynamic, true) AS dynamic, COALESCE(s.ensemble, false) AS ensemble"
    " FROM (SELECT 1) one"
    " LEFT JOIN platform_ai_settings p ON true"
    " LEFT JOIN customer_ai_settings s ON s.customer_id = :customer"
)
_DOCUMENT = text(
    "SELECT read_model, read_mode FROM documents WHERE customer_id = :customer AND id = :document"
)


@dataclass(frozen=True, slots=True)
class Agent:
    """One model that reads or answers: its OpenRouter id, the provider it is called
    through and its catalogue name (the id when it has none)."""

    model: str
    provider: str
    name: str


@dataclass(frozen=True, slots=True)
class Reading:
    """How one document's scans are read (C3), checked at job time: ``mode`` single,
    dynamic or ensemble (after the permission check); ``lead`` reads every page;
    ``others`` (ensemble only, at most two) read them again for the vote; ``careful``
    (dynamic only) re-reads the unclear pages; ``thinking`` the level sent; ``notes``
    why something fell back (``mode_revoked``, ``ensemble_after_scan``,
    ``thinking_fallback``)."""

    mode: str
    lead: Agent
    others: tuple[Agent, ...] = ()
    careful: Agent | None = None
    thinking: str = ""
    notes: tuple[str, ...] = ()

    def config(self) -> dict[str, Any]:
        """``processing_runs.config.reading`` (S5a): ids, providers, mode and thinking."""

        def agent(one: Agent) -> dict[str, str]:
            return {"model": one.model, "provider": one.provider}

        return {
            "mode": self.mode,
            "lead": agent(self.lead),
            "others": [agent(one) for one in self.others],
            "careful": None if self.careful is None else agent(self.careful),
            "thinking": self.thinking,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ChatChoice:
    """Who drafts one answer (C3): ``agents`` the lead first, then the others in the
    asker's order; ``thinking`` the level every one of them is called at."""

    agents: list[Agent]
    thinking: str


class AiNotAllowed(Exception):
    """A choice the organisation may not make; ``code`` is the API's 403 code:
    model_not_allowed, ensemble_not_allowed, dynamic_not_allowed or thinking_not_allowed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def vendor(model: str) -> str:
    """The part of an OpenRouter model id before "/" (``google`` of google/gemini-x)."""
    return model.split("/", 1)[0]


def _provider(row: RowMapping, settings: Settings) -> str | None:
    """The first of the model's providers (the catalogue's order) inside
    OPENROUTER_PROVIDERS, or None; None too when its vendor is outside
    OPENROUTER_MODEL_VENDORS (S1: an approved provider does not approve every vendor)."""
    if vendor(row["id"]) not in settings.openrouter_model_vendors:
        return None
    return next((slug for slug in row["providers"] if slug in settings.openrouter_providers), None)


def _agent(row: RowMapping, settings: Settings) -> Agent:
    return Agent(row["id"], str(_provider(row, settings)), row["name"] or row["id"])


def _usable(rows: Sequence[RowMapping], settings: Settings) -> list[RowMapping]:
    return [row for row in rows if _provider(row, settings) is not None]


def usable_models(
    conn: Connection, customer_id: str, *, settings: Settings, prompt_version: str = READER_PROMPT_VERSION
) -> list[RowMapping]:
    """The scan models ``customer_id`` may read with now: its own, else the platform's,
    each eligible, enabled, verified for ``prompt_version`` (the reader's) and inside both
    ceilings; the default first, then by name. Keys: id, name, price_in_usd_per_m,
    price_out_usd_per_m, tokens_per_page_in, tokens_per_page_out, is_default_scan,
    providers, efforts, ledger_tokens_in, ledger_tokens_out. Empty: none set anywhere
    (every scan is read with OCR_MODEL, as before), or none usable now. On a
    ``Database.customer()`` connection of ``customer_id``."""
    params = {"customer": customer_id, "prompt": prompt_version, "days": LEDGER_DAYS}
    return _usable(conn.execute(_SCAN, params).mappings().all(), settings)


def usable_chat_models(conn: Connection, customer_id: str, *, settings: Settings) -> list[RowMapping]:
    """The chat models ``customer_id`` may answer with now (as ``usable_models``, verified
    for ANSWER_PROMPT_HASH; ``is_default_chat`` in place of ``is_default_scan``). Empty:
    every answer is drafted with MODEL_CHAT, as before."""
    params = {"customer": customer_id, "prompt": ANSWER_PROMPT_HASH, "days": LEDGER_DAYS}
    return _usable(conn.execute(_CHAT, params).mappings().all(), settings)


def effective_settings(conn: Connection, customer_id: str) -> RowMapping:
    """The organisation's AI settings after inheritance: scan_thinking (None: the env's),
    chat_levels, chat_default_level (None: low only), dynamic, ensemble."""
    return conn.execute(_SETTINGS, {"customer": customer_id}).mappings().one()


def page_cost(row: Mapping[Any, Any]) -> float:
    """What one page costs on the server's own scale (never shown): the ledger's average
    tokens a page, else verify-models' test page, times the live price; infinity when
    neither is measured, so an unmeasured model is never the cheapest nor Careful."""
    rough = row["ledger_tokens_in"] is None or row["ledger_tokens_out"] is None
    tokens_in, tokens_out = (
        (row["tokens_per_page_in"], row["tokens_per_page_out"])
        if rough
        else (row["ledger_tokens_in"], row["ledger_tokens_out"])
    )
    if tokens_in is None or tokens_out is None:
        return math.inf
    return float(tokens_in) * float(row["price_in_usd_per_m"]) + float(tokens_out) * float(
        row["price_out_usd_per_m"]
    )


def pick_tiers[R: Mapping[Any, Any]](rows: Sequence[R]) -> tuple[R, R | None]:
    """(Standard, Careful) among usable scan ``rows`` (not empty): Standard the default,
    else the cheapest a page; Careful the dearest a page of the rest, only when it costs
    at least DEARER times Standard (None otherwise, or when Standard is not measured). O(n)."""
    standard = next((row for row in rows if row["is_default_scan"]), min(rows, key=page_cost))
    floor = page_cost(standard) * DEARER
    dearer = [row for row in rows if floor < page_cost(row) < math.inf]
    return standard, max(dearer, key=page_cost) if dearer else None


def _thinking(level: str | None, lead: RowMapping | None, settings: Settings) -> tuple[str, tuple[str, ...]]:
    """The scan level: the effective one when the lead accepts it, else the env's."""
    env = settings.ocr_thinking_level
    if level is None or level == env:
        return env, ()
    if lead is not None and level in lead["efforts"]:
        return level, ()
    return env, ("thinking_fallback",)


def resolve_scan(conn: Connection, customer_id: str, document_id: str, *, settings: Settings) -> Reading:
    """The ``Reading`` of ``document_id`` (C3), on a ``Database.customer()`` connection of
    ``customer_id``. Lead: its ``read_model`` when usable, else the area's default, else
    OCR_MODEL on DEFAULT_PROVIDER (as before model choice). Mode: ensemble (an old
    upload's) is single, ``ensemble_after_scan`` (Ensemble runs only after the scan,
    Tushar, 16 Oct 2026: ``resolve_ensemble``); dynamic needs the effective Dynamic
    switch, and reads first at DYNAMIC_FIRST_THINKING when the lead takes it (else at its
    level, ``thinking_fallback``), with the Careful tier, else the lead, as Careful; a mode
    no longer allowed is single (``mode_revoked``). A document the connection cannot see (another
    customer's, or none) reads single with OCR_MODEL, as before."""
    document = conn.execute(_DOCUMENT, {"customer": customer_id, "document": document_id}).mappings().first()
    rows = [] if document is None else usable_models(conn, customer_id, settings=settings)
    by_id = {row["id"]: row for row in rows}
    read_model, mode = (
        (document["read_model"], document["read_mode"]) if document is not None else (None, "single")
    )
    lead_row = by_id.get(read_model) or next((row for row in rows if row["is_default_scan"]), None)
    lead = (
        _agent(lead_row, settings)
        if lead_row is not None
        else Agent(settings.ocr_model, DEFAULT_PROVIDER, settings.ocr_model)
    )
    allowed = effective_settings(conn, customer_id)
    thinking, notes = _thinking(allowed["scan_thinking"], lead_row, settings)

    if mode == "ensemble":
        return Reading("single", lead, thinking=thinking, notes=(*notes, "ensemble_after_scan"))
    if mode == "dynamic":
        if not allowed["dynamic"]:
            return Reading("single", lead, thinking=thinking, notes=(*notes, "mode_revoked"))
        tier = pick_tiers(rows)[1] if rows else None
        careful = lead if tier is None or tier["id"] == lead.model else _agent(tier, settings)
        if lead_row is None or DYNAMIC_FIRST_THINKING in lead_row["efforts"]:
            thinking, notes = (
                DYNAMIC_FIRST_THINKING,
                tuple(note for note in notes if note != "thinking_fallback"),
            )
        elif "thinking_fallback" not in notes:
            notes += ("thinking_fallback",)
        return Reading("dynamic", lead, careful=careful, thinking=thinking, notes=notes)
    return Reading("single", lead, thinking=thinking, notes=notes)


def resolve_ensemble(conn: Connection, customer_id: str, document_id: str, *, settings: Settings) -> Reading:
    """Who reads ``document_id`` again for Ensemble after the scan (scan review UX rules, 16
    Oct 2026): ``lead`` is ``resolve_scan``'s (the stored reading's own model), ``others`` the
    first two usable scan models that are not the lead (the default first, then by name).
    Raises ``AiNotAllowed("ensemble_not_allowed")`` when the organisation's Ensemble grant is
    off, scans are not read by a model (OCR_ENGINE is not gemini), or no other model is
    usable. The API checks it before a run and the job again when it starts (S2)."""
    if settings.ocr_engine != GEMINI or not effective_settings(conn, customer_id)["ensemble"]:
        raise AiNotAllowed("ensemble_not_allowed")
    scan = resolve_scan(conn, customer_id, document_id, settings=settings)
    rows = usable_models(conn, customer_id, settings=settings)
    others = tuple(_agent(row, settings) for row in rows if row["id"] != scan.lead.model)[: MAX_AGENTS - 1]
    if not others:
        raise AiNotAllowed("ensemble_not_allowed")
    return Reading("ensemble", scan.lead, others, thinking=scan.thinking, notes=scan.notes)


def resolve(
    conn: Connection, customer_id: str, document_id: str, *, settings: Settings, prompt_version: str
) -> tuple[str, str]:
    """(model, provider) of ``document_id``'s lead reader: ``resolve_scan``'s lead (kept
    for the pipeline until it reads the whole ``Reading``). ``prompt_version`` must be the
    reader's own."""
    if prompt_version != READER_PROMPT_VERSION:
        raise ValueError("resolve reads with the reader's own prompt version")
    lead = resolve_scan(conn, customer_id, document_id, settings=settings).lead
    return lead.model, lead.provider


def resolve_chat(
    conn: Connection,
    customer_id: str,
    agents: Sequence[str] | None,
    thinking: str | None,
    *,
    settings: Settings,
) -> ChatChoice:
    """Who drafts the answer (C3), on a ``Database.customer()`` connection of
    ``customer_id``. ``agents`` (None: the default) must be usable chat models, more than
    one only with the organisation's Ensemble switch (the API has already refused
    duplicates and more than MAX_AGENTS, 400). ``thinking`` (None: the effective default
    level) must be one of the effective levels and accepted by every agent; with more than
    one agent never high (v1). With no usable chat model, MODEL_CHAT on DEFAULT_PROVIDER,
    as before. The lead is the area's default when it is chosen, else the first named
    (P10). Raises ``AiNotAllowed``."""
    if agents is not None and (len(set(agents)) != len(agents) or not 0 < len(agents) <= MAX_AGENTS):
        raise ValueError("agents must be 1 to MAX_AGENTS distinct model ids")
    rows = usable_chat_models(conn, customer_id, settings=settings)
    by_id = {row["id"]: row for row in rows}
    default = next((row["id"] for row in rows if row["is_default_chat"]), None)
    fallback = Agent(settings.model_chat, DEFAULT_PROVIDER, settings.model_chat)
    if agents is None:
        chosen = [_agent(by_id[default], settings) if default is not None else fallback]
    elif not rows and list(agents) == [settings.model_chat]:
        chosen = [fallback]
    elif all(model in by_id for model in agents):
        chosen = [_agent(by_id[model], settings) for model in agents]
    else:
        raise AiNotAllowed("model_not_allowed")
    allowed = effective_settings(conn, customer_id)
    if len(chosen) > 1 and not allowed["ensemble"]:
        raise AiNotAllowed("ensemble_not_allowed")
    levels = allowed["chat_levels"] or ["low"]
    level = thinking or allowed["chat_default_level"] or "low"
    accepted = all(level in by_id[agent.model]["efforts"] for agent in chosen if agent.model in by_id)
    if level not in levels or not accepted or (len(chosen) > 1 and level == "high"):
        raise AiNotAllowed("thinking_not_allowed")
    chosen.sort(key=lambda agent: agent.model != default)  # stable: the rest keep their order
    return ChatChoice(chosen, level)
