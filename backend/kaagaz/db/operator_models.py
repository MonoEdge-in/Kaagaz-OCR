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
* ``feature_switches`` and ``set_feature_switch`` (0038): the platform feature switches;
  ``org_feature_switches`` and ``set_org_feature_switch`` (0039): one organisation's.
* ``current_test_model``, ``set_test_model`` and ``clear_test_model`` (0038): the platform
  test model, one model for every organisation's scanning and answering, and its undo.

``record_verified`` and ``record_chat_verified`` are the exception: ``verify-models
--record`` writes a model's pass on the OWNER's connection (``cli._owner``), since no app or
operator role may write ``verified_*`` or ``chat_verified_*``.

Time: each is one statement; ``usage`` is O(ledger rows in the period).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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
# An AiChoice's parameters (sm to dyn) are in the functions' order.
_SET_ORG_AI = text(
    "SELECT outcome, result_id FROM kaagaz_op_set_org_ai(:o, :c, CAST(:sm AS text[]), CAST(:sd AS text),"
    " CAST(:cm AS text[]), CAST(:cd AS text), CAST(:st AS text), CAST(:cl AS text[]), CAST(:cdl AS text),"
    " CAST(:dyn AS boolean), :ensemble, CAST(:budget AS numeric), :scan, :chat, :step)"
)
_SET_AI_DEFAULTS = text(
    "SELECT kaagaz_op_set_ai_defaults(:o, CAST(:sm AS text[]), CAST(:sd AS text), CAST(:cm AS text[]),"
    " CAST(:cd AS text), CAST(:st AS text), CAST(:cl AS text[]), CAST(:cdl AS text), CAST(:dyn AS boolean),"
    " :scan, :chat, :step)"
)


@dataclass(frozen=True, slots=True)
class AiChoice:
    """One AI settings save, an organisation's or the platform's (C10): the scan and answer
    models with their defaults (an empty list: the platform's; for the platform, the
    environment's OCR_MODEL and MODEL_CHAT), the scan thinking level, the answer levels and
    default level, and Dynamic. For an organisation, None inherits that field."""

    scan_models: Sequence[str] = ()
    scan_default: str | None = None
    chat_models: Sequence[str] = ()
    chat_default: str | None = None
    scan_thinking: str | None = None
    chat_levels: Sequence[str] | None = None
    chat_default_level: str | None = None
    dynamic: bool | None = None

    def params(self) -> dict[str, object]:
        return {
            "sm": list(self.scan_models),
            "sd": self.scan_default,
            "cm": list(self.chat_models),
            "cd": self.chat_default,
            "st": self.scan_thinking,
            "cl": None if self.chat_levels is None else list(self.chat_levels),
            "cdl": self.chat_default_level,
            "dyn": self.dynamic,
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
    chat_default, scan_thinking, chat_levels, chat_default_level, dynamic, ensemble) and
    ai_budget_usd; an empty list or a NULL field inherits. None when there is no such
    organisation (or the operator is not enabled)."""
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
    orgs_inheriting_{scan_models, chat_models, scan_thinking, chat_levels, dynamic}: the
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
