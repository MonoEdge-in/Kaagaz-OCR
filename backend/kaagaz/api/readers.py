"""How an upload may ask for its scans to be read (model choice, council 11 Oct 2026, MS4,
MS5; features 25-27, council 15 Oct 2026, C10).

The operator sets each organisation's models; the ones usable now (allowed, eligible,
enabled, verified for the reader's prompt, inside both ceilings) come from
``db.models.usable_models``, which knows the reader's prompt version itself: the api never
imports a reader module (the reader plug socket). ``GET /api/upload-limits`` offers:

* ``readers``: "Automatic" (Dynamic, id ``dynamic``; "Cost saving" in the super admin
  console, never here: Tushar, 16 Oct 2026) first, the default, when the organisation may use
  it (its switch, else the platform's, is on; off on a new database, #405): it reads
  clear pages quickly and takes extra care with hard-to-read words, with the Careful tier
  or, with none, the default model thinking harder (``db.models.resolve_scan``). Then "Standard" (the default
  without Dynamic) and, when a usable model costs measurably more a page, "Careful", the
  tiers of ``db.models.pick_tiers``. Never a model's name, a price or cost words (Tushar,
  15 Oct 2026: org admins and members never see cost). Auto alone in keyless mode and for
  an organisation with no usable model, so nothing can be chosen.

Ensemble is offered only after the scan (Tushar, 16 Oct 2026; ``kaagaz/api/routers/ensemble.py``).

``check_reading`` checks what an upload, a version or a Read again asks for (``read_mode``,
``read_model``) before anything is stored: 400 ``bad_request`` for a mode that is not one,
403 ``model_not_allowed`` or ``dynamic_not_allowed`` for one the organisation may not make.
With no mode, an upload is read with Dynamic when it may be, else as before. A
``read_mode`` of ensemble and any ``read_agents`` (older browsers) are accepted and
ignored: read as with no mode, the audit row noting ``ensemble_after_scan`` (Tushar's
choice, never a 400). The browser's choice is never trusted: the worker checks it again
(``db.models.resolve_scan``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import Connection, RowMapping

from kaagaz.api.errors import ApiError
from kaagaz.db import models as models_sql
from kaagaz.reading.ocr import GEMINI, ocr_choices
from kaagaz.settings import Settings

NOT_ALLOWED = "That reading choice is no longer available. Choose again from the list."
STANDARD = "Standard"
STANDARD_HINT = "Right for most documents."
CAREFUL = "Careful"
CAREFUL_HINT = "Takes more care with faint, handwritten or crowded pages."
# Feature 26 (Tushar, 15 Oct 2026), its id "dynamic". Cost saving (Tushar, 16 Oct 2026): the
# super admin console calls it "Cost saving"; organisations never see cost words, so here it
# is "Automatic".
DYNAMIC = "dynamic"
DYNAMIC_TITLE = "Automatic"
DYNAMIC_HINT = "Reads clear pages quickly and takes extra care with hard-to-read words."
SINGLE, ENSEMBLE = "single", "ensemble"
# Ensemble after the scan (Tushar, 16 Oct 2026): the audit note of an upload that still asked for it.
ENSEMBLE_AFTER_SCAN = "ensemble_after_scan"
_REFUSED = {
    "model_not_allowed": NOT_ALLOWED,
    "dynamic_not_allowed": "Automatic reading is no longer available. Choose again from the list.",
}


@dataclass(frozen=True, slots=True)
class ReadingChoice:
    """What to store on the document (``documents.read_mode``, ``read_model``,
    ``read_also``): ``mode`` None keeps the document's reading (a Read again that names
    nothing); ``model`` None the organisation's default; ``also`` is always empty now
    (written so a Read again clears an old Ensemble document's); ``note``
    ENSEMBLE_AFTER_SCAN when Ensemble was asked for and ignored."""

    mode: str | None
    model: str | None = None
    also: tuple[str, ...] = ()
    note: str = ""

    def details(self) -> dict[str, str]:
        """The audit row's details (C9): the reading asked for, when one was; the model and
        the other readers when named (``read_also`` comma separated); ``read_note`` when
        Ensemble was asked for. Nothing for single with the default model, the reading as
        before features 25-27."""
        details = {"read_note": self.note}
        if self.mode is not None and (self.mode != SINGLE or self.model is not None):
            details |= {
                "read_mode": self.mode,
                "read_model": self.model or "",
                "read_also": ",".join(self.also),
            }
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
    has it): Auto alone when there are no usable models; else "Automatic" (``dynamic``: the
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


def offer(conn: Connection, customer_id: str, settings: Settings) -> list[dict[str, Any]]:
    """``readers`` of ``/upload-limits``; no query in keyless mode, one more for the
    settings only when a model is usable."""
    rows = usable(conn, customer_id, settings)
    if not rows:
        return readers(rows, settings)
    allowed = models_sql.effective_settings(conn, customer_id)
    return readers(rows, settings, dynamic=bool(allowed["dynamic"]))


def _tier(row: RowMapping, title: str, hint: str, *, default: bool) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": title,
        "hint": hint,
        "available": True,
        "reason": None,
        "default": bool(default),
    }


def check_shape(mode: str | None) -> None:
    """400 ``bad_request`` for a mode that is not single, dynamic or ensemble (S8), before
    any query."""
    if mode is not None and mode not in models_sql.MODES:
        raise ApiError(400, "bad_request")


def check_reading(
    conn: Connection,
    customer_id: str,
    settings: Settings,
    *,
    mode: str | None = None,
    model: str | None = None,
    keep: bool = False,
) -> ReadingChoice:
    """The reading to store for what was asked (``check_shape`` first), or ``ApiError``
    before anything is stored. A ``read_model`` alone is single (as before Dynamic). No
    mode and no model: the document's own reading when ``keep`` (a Read again; no query),
    else Dynamic when the organisation may use it, else single. Ensemble is read as with
    no mode, never kept (``note``: the module notes). Dynamic asked for without that is
    403 dynamic_not_allowed; a model it can't use 403 model_not_allowed. At most two
    queries; none in keyless mode."""
    check_shape(mode)
    note = ""
    if mode == ENSEMBLE:
        mode, keep, note = None, False, ENSEMBLE_AFTER_SCAN
    if mode is None and model is None and keep:
        return ReadingChoice(None)
    if mode is None and model is not None:
        mode = SINGLE
    rows = usable(conn, customer_id, settings)
    usable_ids = {row["id"] for row in rows}
    if model is not None and model not in usable_ids:
        _refuse("model_not_allowed")
    if mode == SINGLE:
        return ReadingChoice(SINGLE, model, note=note)
    dynamic = _allowed(conn, customer_id, rows, "dynamic")
    if mode == DYNAMIC and not dynamic:
        _refuse("dynamic_not_allowed")
    return ReadingChoice(DYNAMIC if dynamic else SINGLE, model, note=note)


def _allowed(conn: Connection, customer_id: str, rows: Sequence[RowMapping], switch: str) -> bool:
    """The organisation's effective ``switch`` (dynamic); False with no usable model,
    without asking."""
    return bool(rows) and bool(models_sql.effective_settings(conn, customer_id)[switch])


def _refuse(code: str) -> NoReturn:
    raise ApiError(403, code, _REFUSED[code])
