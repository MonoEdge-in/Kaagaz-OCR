"""How an upload may ask for its scans to be read (model choice, council 11 Oct 2026, MS4,
MS5; features 25-27, council 15 Oct 2026, C10).

The operator sets each organisation's models; the ones usable now (allowed, eligible,
enabled, verified for the reader's prompt, inside both ceilings) come from
``db.models.usable_models``, which knows the reader's prompt version itself: the api never
imports a reader module (the reader plug socket). ``GET /api/upload-limits`` offers:

* ``readers``: "Dynamic" first, the default, when the organisation may use it (its switch
  is on unless the organisation or the platform turned it off; Tushar, 16 Oct 2026): it
  picks the right reading for each page, with the Careful tier or, with none, the default
  model thinking harder (``db.models.resolve_scan``). Then "Standard" (the default
  without Dynamic) and, when a usable model costs measurably more a page, "Careful", the
  tiers of ``db.models.pick_tiers``. Never a model's name, a price or cost words (Tushar,
  15 Oct 2026: org admins and members never see cost). Auto alone in keyless mode and for
  an organisation with no usable model, so nothing can be chosen.
* ``ensemble``: with the organisation's Ensemble switch on and two usable models, the
  models by name for "More than one reader…" (at most MAX_AGENTS, the main reader
  first: the default); else null.

``check_reading`` checks what an upload, a version or a Read again asks for (``read_mode``,
``read_model``, ``read_agents``) before anything is stored: 400 ``bad_request`` for a
malformed choice, 403 ``model_not_allowed``, ``dynamic_not_allowed`` or
``ensemble_not_allowed`` for one the organisation may not make. With no mode, an upload is
read with Dynamic when it may be, else as before. The browser's choice is never trusted:
the worker checks it again (``db.models.resolve_scan``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import Connection, RowMapping

from kaagaz.api.errors import ApiError
from kaagaz.db import models as models_sql
from kaagaz.reading.ocr import GEMINI, ocr_choices
from kaagaz.settings import MODEL_ID, Settings

NOT_ALLOWED = "That reading choice is no longer available. Choose again from the list."
STANDARD = "Standard"
STANDARD_HINT = "Right for most documents."
CAREFUL = "Careful"
CAREFUL_HINT = "Takes more care with faint, handwritten or crowded pages."
# Feature 26 (Tushar, 15 Oct 2026): the mode is called Dynamic everywhere.
DYNAMIC = "dynamic"
DYNAMIC_TITLE = "Dynamic"
DYNAMIC_HINT = "Picks the right reading for each page."
SINGLE, ENSEMBLE = "single", "ensemble"
# S8: the longest model id an upload may name (an OpenRouter id is at most 257 characters).
MAX_MODEL_ID = 257
_REFUSED = {
    "model_not_allowed": NOT_ALLOWED,
    "dynamic_not_allowed": "Dynamic reading is no longer available. Choose again from the list.",
    "ensemble_not_allowed": "More than one reader is no longer available. Choose again from the list.",
}


@dataclass(frozen=True, slots=True)
class ReadingChoice:
    """What to store on the document (``documents.read_mode``, ``read_model``,
    ``read_also``): ``mode`` None keeps the document's reading (a Read again that names
    nothing); ``model`` None the organisation's default."""

    mode: str | None
    model: str | None = None
    also: tuple[str, ...] = ()

    def details(self) -> dict[str, str]:
        """The audit row's details (C9): the reading asked for, when one was; the model and
        the other readers when named (``read_also`` comma separated). Nothing for single
        with the default model, the reading as before features 25-27."""
        if self.mode is None or (self.mode == SINGLE and self.model is None):
            return {}
        details = {"read_mode": self.mode, "read_model": self.model, "read_also": ",".join(self.also)}
        return {key: value for key, value in details.items() if value}


def usable(conn: Connection, customer_id: str, settings: Settings) -> list[RowMapping]:
    """The organisation's usable models, its default first; none in keyless mode (no
    query). On a ``Database.customer()`` connection of ``customer_id``."""
    if settings.ocr_engine != GEMINI:
        return []
    rows = models_sql.usable_models(conn, customer_id, settings=settings)
    return sorted(rows, key=lambda row: not row["is_default_scan"])  # stable: the rest keep their order


def readers(rows: Sequence[RowMapping], settings: Settings, *, dynamic: bool = False) -> list[dict[str, Any]]:
    """``UploadLimits.readers`` (``ReaderOption``, with ``reason`` null as ``UploadReader``
    has it): Auto alone when there are no usable models; else "Dynamic" (``dynamic``: the
    organisation may use it), marked the default; "Standard",
    the organisation's default (the cheapest a page when its default is not usable now,
    then ``default`` is false, R14; never marked under Dynamic); and "Careful", only when
    it costs measurably more than Standard (``db.models.pick_tiers``). O(n)."""
    if not rows:
        return list(ocr_choices(settings))
    standard, careful = models_sql.pick_tiers(rows)
    offered = []
    if dynamic:
        offered.append(
            {
                "id": DYNAMIC,
                "title": DYNAMIC_TITLE,
                "hint": DYNAMIC_HINT,
                "available": True,
                "reason": None,
                "default": True,
            }
        )
    offered.append(
        _tier(standard, STANDARD, STANDARD_HINT, default=not offered and standard["is_default_scan"])
    )
    if careful is not None:
        offered.append(_tier(careful, CAREFUL, CAREFUL_HINT, default=False))
    return offered


def ensemble(rows: Sequence[RowMapping], allowed: bool) -> dict[str, Any] | None:
    """``UploadLimits.ensemble`` (C10): the usable models by name, the main reader (the
    default, else the first) and the most an upload may tick; None unless the
    organisation's Ensemble switch is on (``allowed``) and two models are usable."""
    if not allowed or len(rows) < 2:
        return None
    named = [{"id": row["id"], "name": row["name"] or row["id"]} for row in rows]
    return {"readers": named, "max": models_sql.MAX_AGENTS, "main": rows[0]["id"]}


def offer(
    conn: Connection, customer_id: str, settings: Settings
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """(``readers``, ``ensemble``) of ``/upload-limits``; no query in keyless mode, one more
    for the settings only when a model is usable."""
    rows = usable(conn, customer_id, settings)
    if not rows:
        return readers(rows, settings), None
    allowed = models_sql.effective_settings(conn, customer_id)
    return readers(rows, settings, dynamic=bool(allowed["dynamic"])), ensemble(
        rows, bool(allowed["ensemble"])
    )


def _tier(row: RowMapping, title: str, hint: str, *, default: bool) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": title,
        "hint": hint,
        "available": True,
        "reason": None,
        "default": bool(default),
    }


def check_shape(mode: str | None, model: str | None, agents: Sequence[str]) -> None:
    """400 ``bad_request`` for a choice that can't be one (S8), before any query: a mode
    that is not single, dynamic or ensemble; readers named without Ensemble, or Ensemble
    with a ``read_model`` or without 2 to MAX_AGENTS different model ids (each an OpenRouter
    id of at most MAX_MODEL_ID characters)."""
    if mode is not None and mode not in models_sql.MODES:
        raise ApiError(400, "bad_request")
    if agents and mode != ENSEMBLE:
        raise ApiError(400, "bad_request")
    if mode == ENSEMBLE and (
        model is not None
        or not 2 <= len(agents) <= models_sql.MAX_AGENTS
        or len(set(agents)) != len(agents)
        or not all(len(agent) <= MAX_MODEL_ID and MODEL_ID.fullmatch(agent) for agent in agents)
    ):
        raise ApiError(400, "bad_request", f"Choose 2 to {models_sql.MAX_AGENTS} different readers.")


def check_reading(
    conn: Connection,
    customer_id: str,
    settings: Settings,
    *,
    mode: str | None = None,
    model: str | None = None,
    agents: Sequence[str] = (),
    keep: bool = False,
) -> ReadingChoice:
    """The reading to store for what was asked (``check_shape`` first), or ``ApiError``
    before anything is stored. A ``read_model`` alone is single (as before Dynamic). No
    mode and no model: the document's own reading when ``keep`` (a Read again; no query),
    else Dynamic when the organisation may use it, else single.
    Dynamic asked for without that is 403 dynamic_not_allowed; Ensemble without the
    organisation's switch 403 ensemble_not_allowed; a model it can't use 403
    model_not_allowed. Ensemble's main reader is the default when it is ticked, else the
    first named; the rest are ``also``. At most two queries; none in keyless mode."""
    agents = list(agents)
    check_shape(mode, model, agents)
    if mode is None and model is None and keep:
        return ReadingChoice(None)
    if mode is None and model is not None:
        mode = SINGLE
    rows = usable(conn, customer_id, settings)
    usable_ids = {row["id"] for row in rows}
    if mode == ENSEMBLE:
        if not _allowed(conn, customer_id, rows, "ensemble"):
            _refuse("ensemble_not_allowed")
        if any(agent not in usable_ids for agent in agents):
            _refuse("model_not_allowed")
        lead = next((row["id"] for row in rows if row["is_default_scan"] and row["id"] in agents), agents[0])
        return ReadingChoice(ENSEMBLE, lead, tuple(agent for agent in agents if agent != lead))
    if model is not None and model not in usable_ids:
        _refuse("model_not_allowed")
    if mode == SINGLE:
        return ReadingChoice(SINGLE, model)
    dynamic = _allowed(conn, customer_id, rows, "dynamic")
    if mode == DYNAMIC and not dynamic:
        _refuse("dynamic_not_allowed")
    return ReadingChoice(DYNAMIC if dynamic else SINGLE, model)


def _allowed(conn: Connection, customer_id: str, rows: Sequence[RowMapping], switch: str) -> bool:
    """The organisation's effective ``switch`` (dynamic or ensemble); False with no usable
    model, without asking."""
    return bool(rows) and bool(models_sql.effective_settings(conn, customer_id)[switch])


def _refuse(code: str) -> NoReturn:
    raise ApiError(403, code, _REFUSED[code])
