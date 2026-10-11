"""What a platform operator does to the model catalogue and to each organisation's models
and budget (model choice, council 11 Oct 2026, MC2, MS1, MS2, SP3, SP4; 0023), on
``OperatorDatabase.operator()`` connections only: each call is one ``kaagaz_op_*``
function, which checks the operator and writes its own audit rows.

* ``list_models``: the catalogue (``kaagaz_op_models``).
* ``set_model_enabled``: enable or disable one model on the platform, with the step of a
  fresh code (step-up, the 0016 pattern). Outcomes: ``done``, ``unchanged``, ``missing``,
  ``not_eligible``, ``not_verified`` (no verify-models pass for ``prompt_version``),
  ``in_use`` (an organisation still has it), ``step_up`` or ``signed_out``.
* ``org_models`` and ``set_org_models``: one organisation's allowed models, its default
  scan model and its monthly budget. Outcomes: ``done``, ``missing``, ``deleting``,
  ``unavailable``, ``not_verified``, ``default_not_allowed`` or ``signed_out``.
* ``usage``: tokens and dollars per organisation and model. No user, no document.

``record_verified`` is the exception: ``verify-models --record`` writes a model's pass on
the OWNER's connection (``cli._owner``), since no app or operator role may write
``verified_*``.

Time: each is one statement; ``usage`` is O(ledger rows in the period).
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Connection, RowMapping, text

from kaagaz.db.operator_orgs import Outcome

_RECORD = text(
    "UPDATE model_catalog SET verified_prompt_version = :version, verified_at = now(),"
    " tokens_per_page_in = :tokens_in, tokens_per_page_out = :tokens_out WHERE id = :model"
)


def list_models(conn: Connection, operator_id: str) -> Sequence[RowMapping]:
    """Every catalogue row, by name. Empty for an operator who is not enabled."""
    return conn.execute(text("SELECT * FROM kaagaz_op_models(:o)"), {"o": operator_id}).mappings().all()


def set_model_enabled(
    conn: Connection, operator_id: str, model: str, *, enabled: bool, prompt_version: str, step: int
) -> str:
    """Enable or disable ``model`` on the platform (``step`` of a fresh code required);
    the outcome code."""
    row = conn.execute(
        text("SELECT outcome FROM kaagaz_op_set_model_enabled(:o, :m, :enabled, :version, :step)"),
        {"o": operator_id, "m": model, "enabled": enabled, "version": prompt_version, "step": step},
    ).one()
    return str(row[0])


def org_models(conn: Connection, operator_id: str, customer_id: str) -> RowMapping | None:
    """(models, default_scan, ai_budget_usd) of one organisation; None when there is no
    such organisation (or the operator is not enabled)."""
    return (
        conn.execute(text("SELECT * FROM kaagaz_op_org_models(:o, :c)"), {"o": operator_id, "c": customer_id})
        .mappings()
        .first()
    )


def set_org_models(
    conn: Connection,
    operator_id: str,
    customer_id: str,
    *,
    models: Sequence[str],
    default_scan: str | None,
    budget: Decimal | None,
    prompt_version: str,
) -> Outcome:
    """Replace one organisation's models, default and budget (audited for the operator and
    in the organisation's own log)."""
    row = conn.execute(
        text(
            "SELECT outcome, result_id FROM kaagaz_op_set_org_models("
            ":o, :c, CAST(:models AS text[]), :default, :budget, :version)"
        ),
        {
            "o": operator_id,
            "c": customer_id,
            "models": list(models),
            "default": default_scan,
            "budget": budget,
            "version": prompt_version,
        },
    ).one()
    return Outcome(str(row[0]), None if row[1] is None else str(row[1]))


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
