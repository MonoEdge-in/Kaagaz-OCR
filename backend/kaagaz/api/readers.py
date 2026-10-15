"""Which models an upload may read its scans with (model choice, council 11 Oct 2026, MS4,
MS5, MC3).

The operator sets each organisation's models; the ones usable now (allowed, eligible,
enabled, verified for the reader's prompt, on a provider inside OPENROUTER_PROVIDERS) come
from ``db.models.usable_models``, which knows the reader's prompt version itself: the api
never imports a reader module (the reader plug socket). ``GET /api/upload-limits`` offers
at most two of them as plain tiers (UI quality council, 15 Oct 2026): "Standard", the
default, and "Careful", the dearest a page; never a model's name, a price or any cost
words (Tushar, 15 Oct 2026: org admins and members never see cost). An
upload or a Read again naming a ``read_model`` outside them is 403 ``model_not_allowed``
before anything is stored.
The browser's choice is never trusted: the worker checks it again (``db.models.resolve``).

Keyless mode (OCR_ENGINE other than gemini) and an organisation the operator set no models
for keep the one reader, Auto (``ocr_choices``, PRD 5 RT3), so nothing can be chosen.
"""

import math
from collections.abc import Sequence
from typing import Any

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
# Careful is offered only when it costs at least this much more a page than Standard.
DEARER = 1.1


def usable(conn: Connection, customer_id: str, settings: Settings) -> list[RowMapping]:
    """The organisation's usable models, its default first; none in keyless mode (no
    query). On a ``Database.customer()`` connection of ``customer_id``."""
    if settings.ocr_engine != GEMINI:
        return []
    rows = models_sql.usable_models(conn, customer_id, settings=settings)
    return sorted(rows, key=lambda row: not row["is_default_scan"])  # stable: the rest keep their order


def readers(rows: Sequence[RowMapping], settings: Settings) -> list[dict[str, Any]]:
    """``UploadLimits.readers`` (``ReaderOption``, with ``reason`` null as ``UploadReader``
    has it): Auto alone when there are no usable models; else "Standard", the
    organisation's default (the cheapest a page when its default is not usable now, then
    ``default`` is false, R14), and "Careful", the dearest a page of the rest,
    only when it costs measurably more (``DEARER``) than Standard. A model whose price is not
    measured yet is never Careful. O(n)."""
    if not rows:
        return list(ocr_choices(settings))
    standard = next((row for row in rows if row["is_default_scan"]), min(rows, key=_cost))
    offered = [_tier(standard, STANDARD, STANDARD_HINT)]
    floor = _cost(standard) * DEARER  # inf when Standard's price is not measured: no Careful
    dearer = [row for row in rows if floor < _cost(row) < math.inf]
    if dearer:
        offered.append(_tier(max(dearer, key=_cost), CAREFUL, CAREFUL_HINT))
    return offered


def _cost(row: RowMapping) -> float:
    """``page_usd``, with a price not measured yet as infinity: never the cheapest."""
    usd = page_usd(row)
    return math.inf if usd is None else usd


def _tier(row: RowMapping, title: str, hint: str) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": title,
        "hint": hint,
        "available": True,
        "reason": None,
        "default": bool(row["is_default_scan"]),
    }


def page_estimate(row: RowMapping) -> tuple[float, bool] | None:
    """(US dollars one page is estimated to cost, whether it is rough), MC3: the average
    tokens a page took in the organisation's ledger of the last 30 days
    (``ledger_tokens_*``, ``db.models.usable_models``) times the live price; else the
    tokens verify-models measured on its small test page, a rough estimate; None with
    neither. O(1)."""
    rough = row["ledger_tokens_in"] is None or row["ledger_tokens_out"] is None
    tokens_in, tokens_out = (
        (row["tokens_per_page_in"], row["tokens_per_page_out"])
        if rough
        else (row["ledger_tokens_in"], row["ledger_tokens_out"])
    )
    if tokens_in is None or tokens_out is None:
        return None
    price_in, price_out = float(row["price_in_usd_per_m"]), float(row["price_out_usd_per_m"])
    return (float(tokens_in) * price_in + float(tokens_out) * price_out) / 1_000_000, rough


def page_usd(row: RowMapping) -> float | None:
    """US dollars one page is estimated to cost (``page_estimate``); None until measured."""
    estimate = page_estimate(row)
    return None if estimate is None else estimate[0]


def page_provider(row: RowMapping, settings: Settings) -> str | None:
    """The provider the model's pages go to: its first one inside OPENROUTER_PROVIDERS, in
    the catalogue's order, as ``db.models.resolve`` picks it; None when none is."""
    return next((slug for slug in row["providers"] if slug in settings.openrouter_providers), None)


def page_hint(row: RowMapping) -> str:
    """The reader's hint (MC3): "about $0.05 a page" or "under $0.01 a page" from the
    ledger, the same with "(rough estimate)" from verify-models' test page, or "Cost not
    measured yet". Never a made-up figure."""
    estimate = page_estimate(row)
    if estimate is None:
        return "Cost not measured yet"
    usd, rough = estimate
    hint = "under $0.01 a page" if usd < 0.01 else f"about ${usd:.2f} a page"
    return f"{hint} (rough estimate)" if rough else hint


def check_read_model(conn: Connection, customer_id: str, settings: Settings, read_model: str | None) -> None:
    """403 ``model_not_allowed`` unless ``read_model`` is None (the default) or one of the
    organisation's usable models (MS5). No query for None."""
    if read_model is not None and read_model not in {
        row["id"] for row in usable(conn, customer_id, settings)
    }:
        raise ApiError(403, "model_not_allowed", NOT_ALLOWED)
