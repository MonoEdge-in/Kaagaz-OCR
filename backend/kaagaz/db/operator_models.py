"""What a platform operator does to the model catalogue, to each organisation's AI settings
and to the platform defaults (model choice, council 11 Oct 2026, MC2, SP3; features 25-27,
council 15 Oct 2026, C10; 0023, 0026), on ``OperatorDatabase.operator()`` connections only:
each call is one ``kaagaz_op_*`` function, which checks the operator and writes its own
audit rows.

* ``list_models``: the catalogue (``kaagaz_op_models``), with the answer columns and the
  real cost of the last 30 days (0033): per model, every organisation's ledger together,
  pages read (non-cached calls of scan, upgrade, careful, ensemble_scan: one call a page)
  and questions answered (ledger rows of chat, ensemble_chat: one row a question), the
  cost OpenRouter reported for them, and the calls with no reported cost (a NULL or an
  estimated ``cost_usd``), counted apart and never guessed.
* ``set_model_enabled``: enable or disable one model on the platform, with the step of a
  fresh code (step-up). Outcomes: ``done``, ``unchanged``, ``missing``, ``not_eligible``,
  ``not_verified`` (verified neither for the reader's prompt nor for the answer prompt),
  ``in_use`` (an organisation or the platform defaults still name it), ``step_up`` or
  ``signed_out``.
* ``org_ai`` and ``set_org_ai``: one organisation's own AI settings (``AiChoice``: an
  empty list or a None field inherits the platform's), its Ensemble switch and its monthly
  budget. Outcomes: ``done``, ``missing``, ``deleting``, ``unavailable``,
  ``not_verified``, ``chat_not_verified``, ``level_not_supported``,
  ``default_not_allowed``, ``step_up`` or ``signed_out``. Any number of scanning models
  (0037): Ensemble with fewer than two in effect is saved off, still ``done``.
* ``ai_defaults`` and ``set_ai_defaults``: the platform defaults and how many
  organisations inherit each area; the same outcomes but missing and deleting.
* ``usage``: tokens and dollars per organisation and model. No user, no document.
* ``reading_today`` (#478, 0066): one organisation's pages read by AI and dollars on one India
  day (the reading pause's counter). Numbers only.
* ``mistral_status`` (#473, 0065): whether Mistral stopped reading pages, from the runs that
  waited on it (no worker memory). Codes and counts only.
* ``ulc_cost`` (#488, 0070): what Ultra low cost really cost for one model: its pages, their
  reported cost, the pages read locally to stay within the ceiling and the highest cost a page of
  one document. Numbers only.
* ``scan_cost`` (#463, 0064): the measured cost of a page at one first read's and one
  Careful re-read's (model, thinking, picture detail), the scope's share read again, and its
  pages read at the first read's and at max settings, and the clean ones.
* ``start_accuracy_check`` and ``accuracy_check`` (#463, 0064): an organisation's accuracy
  check against max settings, started with a fresh code (``done``, ``missing``, ``deleting``,
  ``check_running``, ``rate_limited``, ``no_pages``, ``step_up`` or ``signed_out``), and its
  latest (or its latest done at given settings). Numbers only.
* ``feature_switches`` and ``set_feature_switch`` (0038): the platform feature switches;
  ``org_feature_switches`` and ``set_org_feature_switch`` (0039): one organisation's.
* ``session_timeout`` and ``set_session_timeout`` (0040): the sign-in idle timeout, the
  platform's and one organisation's.
* ``speed`` and ``set_speed`` (0053): the platform's speed controls.
* ``current_test_model``, ``set_test_model`` and ``clear_test_model`` (0038): the platform
  test model, one model for every organisation's scanning and answering, and its undo.

``record_verified`` and ``record_chat_verified`` are the exception: ``verify-models
--record`` writes a model's pass on the OWNER's connection (``cli._owner``), since no app or
operator role may write ``verified_*`` or ``chat_verified_*``.

Time: each is one statement; ``usage`` is O(ledger rows in the period).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db.operator_orgs import Outcome

_RECORD = text(
    "UPDATE model_catalog SET verified_prompt_version = :version, verified_at = now(),"
    " tokens_per_page_in = :tokens_in, tokens_per_page_out = :tokens_out WHERE id = :model"
)
_RECORD_CHAT = text(
    "UPDATE model_catalog SET chat_verified_prompt = :prompt, chat_verified_at = now() WHERE id = :model"
)
# An AiChoice's parameters (sm to dyn) are in the functions' order (0064: sr, ct, cr, pm after st;
# #473, 0065: rd and mm after the step; #478, 0066: dpl; #488, 0070: ulc last).
_SET_ORG_AI = text(
    "SELECT outcome, result_id FROM kaagaz_op_set_org_ai(:o, :c, CAST(:sm AS text[]), CAST(:sd AS text),"
    " CAST(:cm AS text[]), CAST(:cd AS text), CAST(:st AS text), CAST(:sr AS text), CAST(:ct AS text),"
    " CAST(:cr AS text), CAST(:pm AS integer), CAST(:cl AS text[]), CAST(:cdl AS text),"
    " CAST(:dyn AS boolean), :ensemble,"
    " CAST(:budget AS numeric), :scan, :chat, :step, CAST(:rd AS text), CAST(:mm AS text),"
    " CAST(:dpl AS integer), CAST(:ulc AS integer))"
)
_SET_AI_DEFAULTS = text(
    "SELECT kaagaz_op_set_ai_defaults(:o, CAST(:sm AS text[]), CAST(:sd AS text), CAST(:cm AS text[]),"
    " CAST(:cd AS text), CAST(:st AS text), CAST(:sr AS text), CAST(:ct AS text), CAST(:cr AS text),"
    " CAST(:pm AS integer), CAST(:cl AS text[]), CAST(:cdl AS text), CAST(:dyn AS boolean), :scan, :chat,"
    " :step, CAST(:rd AS text), CAST(:mm AS text), CAST(:dpl AS integer), CAST(:ulc AS integer))"
)
_SCAN_COST = text(
    "SELECT * FROM kaagaz_op_scan_cost(:o, CAST(:c AS uuid), :m, :t, :r, CAST(:cm AS text),"
    " CAST(:ct AS text), CAST(:cr AS text), :mt, :mr)"
)
_START_CHECK = text(
    "SELECT outcome, result_id FROM kaagaz_op_start_accuracy_check(:o, :c, :m, :st, :sr, :ct, :cr,"
    " CAST(:pm AS integer), CAST(:dyn AS boolean), :n, :step)"
)
_CHECK = text(
    "SELECT * FROM kaagaz_op_accuracy_check(:o, :c, CAST(:m AS text), CAST(:st AS text), CAST(:sr AS text),"
    " CAST(:ct AS text), CAST(:cr AS text), CAST(:pm AS integer), CAST(:dyn AS boolean))"
)
_ULC_COST = text("SELECT * FROM kaagaz_op_ulc_cost(:o, CAST(:c AS uuid), :m)")
_MODEL_RATES = text("SELECT * FROM kaagaz_op_model_rates(:o, :c)")
_SET_MODEL_RATE = text("SELECT kaagaz_op_set_model_rate(:o, :c, :u, :rpm, :step)")


@dataclass(frozen=True, slots=True)
class AiChoice:
    """One AI settings save, an organisation's or the platform's (C10): the scan and answer
    models with their defaults (an empty list: the platform's; for the platform, the
    environment's OCR_MODEL and MODEL_CHAT), the scan thinking level, the answer levels and
    default level, and Dynamic. For an organisation, None inherits that field. #463 (0064): the
    scan picture detail, Careful's pair (pages read again) and a page read's output limit, last
    so the order above holds; for the platform, None is the server's (OCR_MEDIA_RESOLUTION,
    OCR_RETRY_*, PAGE_MAX_OUTPUT_TOKENS). #473 (0065): who reads scanned pages (``scan_reader``,
    openrouter or mistral) and the Mistral model; for the platform, None is openrouter and
    DEFAULT_MISTRAL_MODEL. Whether Mistral is set up is the API's check, not the database's.
    #478 (0066): the daily page limit (pages AI may read in one India day); for the platform,
    None is no limit. #488 (0070): Ultra low cost's ceiling, the most one read of a page may cost,
    in paise (20 to 40; ``scan_reader`` ultra_low_cost reads with it); for the platform, None is
    ULC_DEFAULT_PAISE."""

    scan_models: Sequence[str] = ()
    scan_default: str | None = None
    chat_models: Sequence[str] = ()
    chat_default: str | None = None
    scan_thinking: str | None = None
    chat_levels: Sequence[str] | None = None
    chat_default_level: str | None = None
    dynamic: bool | None = None
    scan_resolution: str | None = None
    careful_thinking: str | None = None
    careful_resolution: str | None = None
    page_max_output_tokens: int | None = None
    scan_reader: str | None = None
    mistral_model: str | None = None
    daily_page_limit: int | None = None
    ulc_ceiling_paise: int | None = None

    def params(self) -> dict[str, object]:
        return {
            "sm": list(self.scan_models),
            "sd": self.scan_default,
            "cm": list(self.chat_models),
            "cd": self.chat_default,
            "st": self.scan_thinking,
            "sr": self.scan_resolution,
            "ct": self.careful_thinking,
            "cr": self.careful_resolution,
            "pm": self.page_max_output_tokens,
            "cl": None if self.chat_levels is None else list(self.chat_levels),
            "cdl": self.chat_default_level,
            "dyn": self.dynamic,
            "rd": self.scan_reader,
            "mm": self.mistral_model,
            "dpl": self.daily_page_limit,
            "ulc": self.ulc_ceiling_paise,
        }


def list_models(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """Every catalogue row, by name. Empty for an operator who is not enabled."""
    return conn.execute(text("SELECT * FROM kaagaz_op_models(:o)"), {"o": operator_id}).mappings().all()


def set_model_enabled(
    conn: Connection,
    operator_id: str,
    model: str,
    *,
    enabled: bool,
    scan_prompt: str,
    chat_prompt: str,
    step: int,
) -> str:
    """Enable or disable ``model`` on the platform (``step`` of a fresh code required);
    enabling needs a pass for ``scan_prompt`` (READER_PROMPT_VERSION) or for
    ``chat_prompt`` (ANSWER_PROMPT_HASH). The outcome code."""
    params = {
        "o": operator_id,
        "m": model,
        "on": enabled,
        "scan": scan_prompt,
        "chat": chat_prompt,
        "step": step,
    }
    row = conn.execute(
        text("SELECT outcome FROM kaagaz_op_set_model_enabled(:o, :m, :on, :scan, :chat, :step)"), params
    ).one()
    return str(row[0])


def org_ai(conn: Connection, operator_id: str, customer_id: str) -> RowMapping | None:
    """One organisation's own settings (scan_models, scan_default, chat_models,
    chat_default, scan_thinking, chat_levels, chat_default_level, dynamic, scan_resolution,
    careful_thinking, careful_resolution, page_max_output_tokens, scan_reader, mistral_model,
    daily_page_limit, ulc_ceiling_paise, ensemble) and ai_budget_usd; an empty list or a
    NULL field inherits. None when there is no such organisation (or the operator is not enabled)."""
    return (
        conn.execute(text("SELECT * FROM kaagaz_op_org_ai(:o, :c)"), {"o": operator_id, "c": customer_id})
        .mappings()
        .first()
    )


def set_org_ai(
    conn: Connection,
    operator_id: str,
    customer_id: str,
    choice: AiChoice,
    *,
    ensemble: bool,
    budget: Decimal | None,
    scan_prompt: str,
    chat_prompt: str,
    step: int,
) -> Outcome:
    """Replace one organisation's AI settings, Ensemble switch and budget (``step`` of a
    fresh code required; audited for the operator, and with no budget in the
    organisation's own log)."""
    row = conn.execute(
        _SET_ORG_AI,
        {
            "o": operator_id,
            "c": customer_id,
            **choice.params(),
            "ensemble": ensemble,
            "budget": budget,
            "scan": scan_prompt,
            "chat": chat_prompt,
            "step": step,
        },
    ).one()
    return Outcome(str(row[0]), None if row[1] is None else str(row[1]))


def ai_defaults(conn: Connection, operator_id: str) -> RowMapping | None:
    """The platform defaults (as ``org_ai``, without ensemble and budget) and
    orgs_inheriting_{scan_models, chat_models, scan_thinking, chat_levels, dynamic,
    scan_resolution, careful_thinking, careful_resolution, page_max_output_tokens, scan_reader,
    mistral_model, daily_page_limit, ulc_ceiling_paise}: the
    organisations (not being deleted) a change of that area reaches. None for an operator
    who is not enabled."""
    return (
        conn.execute(text("SELECT * FROM kaagaz_op_ai_defaults(:o)"), {"o": operator_id}).mappings().first()
    )


def set_ai_defaults(
    conn: Connection, operator_id: str, choice: AiChoice, *, scan_prompt: str, chat_prompt: str, step: int
) -> str:
    """Replace the platform defaults (``step`` of a fresh code required; audited with the
    values before and after and the organisations changed per area). The outcome code."""
    params = {"o": operator_id, **choice.params(), "scan": scan_prompt, "chat": chat_prompt, "step": step}
    row = conn.execute(_SET_AI_DEFAULTS, params).one()
    return str(row[0])


def reading_today(conn: Connection, operator_id: str, customer_id: str, day: date) -> RowMapping | None:
    """#478 (0066): pages and cost_usd, the organisation's counter on ``day`` (0 and 0 with
    none). None for an operator who is not enabled."""
    return (
        conn.execute(
            text("SELECT * FROM kaagaz_op_reading_today(:o, :c, :day)"),
            {"o": operator_id, "c": customer_id, "day": day},
        )
        .mappings()
        .first()
    )


def mistral_status(conn: Connection, operator_id: str) -> RowMapping | None:
    """#473 (0065): code (the newest wait's run error code, ``mistral_key_unavailable`` or
    ``mistral_unavailable``; None when no document waits), since (when the first of the waiting
    documents' Mistral waits ended) and documents_waiting, over the last 24 hours, every
    organisation's together. None for an operator who is not enabled."""
    return (
        conn.execute(text("SELECT * FROM kaagaz_op_mistral_status(:o)"), {"o": operator_id})
        .mappings()
        .first()
    )


def ulc_cost(conn: Connection, operator_id: str, customer_id: str | None, model: str) -> RowMapping | None:
    """#488 (0070): pages, cost_usd (None with none), limit_hits and top_usd_per_page (the highest
    cost a page of one document; None with none) of ``model``'s Ultra low cost reads over the last
    30 days, ``customer_id``'s (None: every organisation's), reported costs only. None for an
    operator who is not enabled."""
    return conn.execute(_ULC_COST, {"o": operator_id, "c": customer_id, "m": model}).mappings().first()


def feature_switches(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """Every product feature (0038): feature, enabled, organisations_using, changed_at.
    Empty for an operator who is not enabled."""
    return (
        conn.execute(text("SELECT * FROM kaagaz_op_feature_switches(:o)"), {"o": operator_id})
        .mappings()
        .all()
    )


def set_feature_switch(conn: Connection, operator_id: str, feature: str, *, enabled: bool, step: int) -> str:
    """Turn ``feature`` on or off for every organisation (``step`` of a fresh code
    required; audited). ``done``, ``unchanged``, ``step_up`` or ``signed_out``."""
    params = {"o": operator_id, "f": feature, "on": enabled, "step": step}
    return str(
        conn.execute(text("SELECT kaagaz_op_set_feature_switch(:o, :f, :on, :step)"), params).scalar_one()
    )


def org_feature_switches(conn: Connection, operator_id: str, customer_id: str) -> Sequence[RowMapping]:
    """One organisation's switches (0039): feature, enabled, platform_enabled. Empty for an
    unknown organisation or an operator who is not enabled."""
    params = {"o": operator_id, "c": customer_id}
    return conn.execute(text("SELECT * FROM kaagaz_op_org_feature_switches(:o, :c)"), params).mappings().all()


def set_org_feature_switch(
    conn: Connection, operator_id: str, customer_id: str, feature: str, *, enabled: bool, step: int
) -> str:
    """Turn ``feature`` on or off for one organisation (``step`` of a fresh code required;
    audited). ``done``, ``unchanged``, ``missing``, ``deleting``, ``step_up`` or ``signed_out``."""
    params = {"o": operator_id, "c": customer_id, "f": feature, "on": enabled, "step": step}
    statement = text("SELECT kaagaz_op_set_org_feature_switch(:o, :c, :f, :on, :step)")
    return str(conn.execute(statement, params).scalar_one())


def session_timeout(conn: Connection, operator_id: str, customer_id: str | None) -> RowMapping | None:
    """The sign-in idle timeout (0040): org_hours (None without a customer) and platform_hours.
    None for an unknown organisation or an operator who is not enabled."""
    params = {"o": operator_id, "c": customer_id}
    return conn.execute(text("SELECT * FROM kaagaz_op_session_timeout(:o, :c)"), params).mappings().first()


def set_session_timeout(
    conn: Connection, operator_id: str, customer_id: str | None, hours: int | None, *, step: int
) -> str:
    """Set the platform's idle timeout (no customer) or one organisation's (None: follow the
    platform), ``step`` of a fresh code required; audited. ``done``, ``unchanged``,
    ``missing``, ``deleting``, ``step_up`` or ``signed_out``."""
    params = {"o": operator_id, "c": customer_id, "h": hours, "step": step}
    statement = text("SELECT kaagaz_op_set_session_timeout(:o, :c, :h, :step)")
    return str(conn.execute(statement, params).scalar_one())


def speed(conn: Connection, operator_id: str) -> RowMapping | None:
    """The platform's speed controls (package S, 0053): model_rpm, documents_at_once and
    pages_at_once, each None for the server's setting. None for an operator who is not enabled."""
    return conn.execute(text("SELECT * FROM kaagaz_op_speed(:o)"), {"o": operator_id}).mappings().first()


def set_speed(
    conn: Connection,
    operator_id: str,
    model_rpm: int | None,
    documents_at_once: int | None,
    pages_at_once: int | None,
    *,
    step: int,
) -> str:
    """Set all three (None: the server's setting), ``step`` of a fresh code required; audited.
    ``done``, ``unchanged``, ``step_up`` or ``signed_out``."""
    params = {"o": operator_id, "r": model_rpm, "d": documents_at_once, "p": pages_at_once, "step": step}
    statement = text("SELECT kaagaz_op_set_speed(:o, :r, :d, :p, :step)")
    return str(conn.execute(statement, params).scalar_one())


def current_test_model(conn: Connection, operator_id: str) -> RowMapping | None:
    """The platform test model (0038) with what ran on it since it started: scan_model,
    chat_model, note, started_at, pages, questions, cost_usd (reported only), seconds_per_page.
    None when none is set (or the operator is not enabled)."""
    return conn.execute(text("SELECT * FROM kaagaz_op_test_model(:o)"), {"o": operator_id}).mappings().first()


def set_test_model(
    conn: Connection,
    operator_id: str,
    *,
    scan_model: str | None,
    chat_model: str | None,
    note: str | None,
    scan_prompt: str,
    chat_prompt: str,
    step: int,
) -> str:
    """Start or replace the test model (``step`` of a fresh code required; audited).
    ``done``, ``unavailable``, ``not_verified``, ``chat_not_verified``, ``step_up`` or
    ``signed_out``."""
    params = {
        "o": operator_id,
        "s": scan_model,
        "c": chat_model,
        "n": note,
        "sp": scan_prompt,
        "cp": chat_prompt,
        "step": step,
    }
    statement = text(
        "SELECT kaagaz_op_set_test_model(:o, CAST(:s AS text), CAST(:c AS text), CAST(:n AS text),"
        " :sp, :cp, :step)"
    )
    return str(conn.execute(statement, params).scalar_one())


def clear_test_model(conn: Connection, operator_id: str, *, step: int) -> str:
    """Undo the test model (``step`` of a fresh code required; audited): ``done``,
    ``unchanged``, ``step_up`` or ``signed_out``."""
    params = {"o": operator_id, "step": step}
    return str(conn.execute(text("SELECT kaagaz_op_clear_test_model(:o, :step)"), params).scalar_one())


Sent = tuple[str, str, str]  # (model, thinking level, picture detail) as a page read sends them
# An accuracy check's settings: (model, scan_thinking, scan_resolution, careful_thinking,
# careful_resolution, page_max_output_tokens, dynamic), every one a value (the estimate's, after
# inheritance; #463 review: Dynamic after the Cost saving switches).
CheckSettings = tuple[str, str, str, str, str, int, bool]


def scan_cost(
    conn: Connection,
    operator_id: str,
    customer_id: str | None,
    first: Sent,
    careful: Sent | None,
    best: tuple[str, str],
) -> RowMapping | None:
    """#463: first_usd and first_pages (the first reads at ``first``), careful_usd and
    careful_pages (Careful's at ``careful``; none with None) over every organisation's ledger
    of the last 30 days, OpenRouter's reported cost only, a page counted once (#463 review);
    scan_calls and careful_calls of ``customer_id``'s (None: every organisation's); and its
    pages ``first``'s model first read in those days at ``first``'s settings (quality_pages,
    clean_pages; output_p99, the 99th percentile of their output tokens, None under 20) and
    read once at ``best``'s (thinking, picture detail: max settings; max_quality_pages,
    max_clean_pages); its first reads' tokens_in, tokens_out, token_pages and limit_hits (not
    cached). None for an operator who is not enabled."""
    model, thinking, resolution = first
    careful_model, careful_thinking, careful_resolution = careful or (None, None, None)
    params = {
        "o": operator_id,
        "c": customer_id,
        "m": model,
        "t": thinking,
        "r": resolution,
        "cm": careful_model,
        "ct": careful_thinking,
        "cr": careful_resolution,
        "mt": best[0],
        "mr": best[1],
    }
    return conn.execute(_SCAN_COST, params).mappings().first()


def _check_params(settings: CheckSettings | None) -> dict[str, object]:
    names = ("m", "st", "sr", "ct", "cr", "pm", "dyn")
    return dict(zip(names, settings or (None,) * len(names), strict=True))


def start_accuracy_check(
    conn: Connection, operator_id: str, customer_id: str, settings: CheckSettings, pages: int, *, step: int
) -> Outcome:
    """Start ``customer_id``'s accuracy check of up to ``pages`` pages at ``settings`` (a fresh
    code's ``step``; audited); the outcome, with the check's id when ``done``."""
    params = {"o": operator_id, "c": customer_id, **_check_params(settings), "n": pages, "step": step}
    row = conn.execute(_START_CHECK, params).one()
    return Outcome(str(row[0]), None if row[1] is None else str(row[1]))


def accuracy_check(
    conn: Connection, operator_id: str, customer_id: str, settings: CheckSettings | None = None
) -> RowMapping | None:
    """``customer_id``'s latest accuracy check (``settings`` None), else its latest done one at
    exactly ``settings``; None with none (or an operator who is not enabled)."""
    params = {"o": operator_id, "c": customer_id, **_check_params(settings)}
    return conn.execute(_CHECK, params).mappings().first()


def usage(conn: Connection, operator_id: str, start: datetime, end: datetime) -> Sequence[RowMapping]:
    """Spend per organisation and model from ``start`` up to (not including) ``end``."""
    return (
        conn.execute(
            text("SELECT * FROM kaagaz_op_usage(:o, :start, :end)"),
            {"o": operator_id, "start": start, "end": end},
        )
        .mappings()
        .all()
    )


def model_rates(conn: Connection, operator_id: str, customer_id: str) -> Sequence[RowMapping]:
    """One organisation's effective RPM setting and its users' optional overrides."""
    return conn.execute(_MODEL_RATES, {"o": operator_id, "c": customer_id}).mappings().all()


def set_model_rate(
    conn: Connection,
    operator_id: str,
    customer_id: str,
    user_id: str | None,
    rpm: int | None,
    step: int,
) -> str:
    """Set an organisation/user RPM override; None removes a user override."""
    return str(
        conn.execute(
            _SET_MODEL_RATE,
            {"o": operator_id, "c": customer_id, "u": user_id, "rpm": rpm, "step": step},
        ).scalar_one()
    )


def record_verified(
    conn: Connection, model: str, *, prompt_version: str, tokens_in: int, tokens_out: int
) -> bool:
    """Write ``model``'s verify-models pass for ``prompt_version`` and the tokens one page
    took. False when the catalogue has no such model (run refresh-models first). On the
    OWNER's connection only."""
    params = {"model": model, "version": prompt_version, "tokens_in": tokens_in, "tokens_out": tokens_out}
    return conn.execute(_RECORD, params).rowcount == 1


def record_chat_verified(conn: Connection, model: str, *, prompt_hash: str) -> bool:
    """Write ``model``'s verify-models answer pass for ``prompt_hash`` (ANSWER_PROMPT_HASH).
    False when the catalogue has no such model. On the OWNER's connection only."""
    return conn.execute(_RECORD_CHAT, {"model": model, "prompt": prompt_hash}).rowcount == 1
