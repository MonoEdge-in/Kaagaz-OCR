"""Which models an upload may read its scans with (model choice, council 11 Oct 2026, MS4,
MS5, MC3).

The operator sets each organisation's models; the ones usable now (allowed, eligible,
enabled, verified for the reader's prompt, on a provider inside OPENROUTER_PROVIDERS) come
from ``db.models.usable_models``. ``GET /api/upload-limits`` lists them, the default first,
each with its catalogue name and an estimated price a page, and an upload or a Read again
naming a ``read_model`` outside them is 403 ``model_not_allowed`` before anything is stored.
The browser's choice is never trusted: the worker checks it again (``db.models.resolve``).

Keyless mode (OCR_ENGINE other than gemini) and an organisation the operator set no models
for keep the one reader, Auto (``ocr_choices``, PRD 5 RT3), so nothing can be chosen.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, RowMapping

from kaagaz.api.errors import ApiError
from kaagaz.db import models as models_sql
from kaagaz.reading.gemini_ocr import GEMINI_OCR_PROMPT_VERSION
from kaagaz.reading.ocr import GEMINI, ocr_choices
from kaagaz.settings import Settings

NOT_ALLOWED = "Scans here can't be read with that model. Choose one from the list, or leave the default."


def usable(conn: Connection, customer_id: str, settings: Settings) -> list[RowMapping]:
    """The organisation's usable models, its default first; none in keyless mode (no
    query). On a ``Database.customer()`` connection of ``customer_id``."""
    if settings.ocr_engine != GEMINI:
        return []
    rows = models_sql.usable_models(
        conn, customer_id, settings=settings, prompt_version=GEMINI_OCR_PROMPT_VERSION
    )
    return sorted(rows, key=lambda row: not row["is_default_scan"])  # stable: the rest keep their order


def readers(rows: Sequence[RowMapping], settings: Settings) -> list[dict[str, Any]]:
    """``UploadLimits.readers``: the usable models (``ReaderOption``, with ``reason`` null as
    ``UploadReader`` has it), or Auto alone when there are none."""
    if not rows:
        return list(ocr_choices(settings))
    return [
        {"id": row["id"], "title": row["name"], "hint": page_hint(row), "available": True, "reason": None}
        for row in rows
    ]


def page_usd(row: RowMapping) -> float | None:
    """US dollars one page is estimated to cost: the tokens verify-models measured a page
    times the live price; None until it was measured. O(1)."""
    tokens_in, tokens_out = row["tokens_per_page_in"], row["tokens_per_page_out"]
    if tokens_in is None or tokens_out is None:
        return None
    price_in, price_out = float(row["price_in_usd_per_m"]), float(row["price_out_usd_per_m"])
    return float(tokens_in * price_in + tokens_out * price_out) / 1_000_000


def page_provider(row: RowMapping, settings: Settings) -> str | None:
    """The provider the model's pages go to: its first one inside OPENROUTER_PROVIDERS, in
    the catalogue's order, as ``db.models.resolve`` picks it; None when none is."""
    return next((slug for slug in row["providers"] if slug in settings.openrouter_providers), None)


def page_hint(row: RowMapping) -> str:
    """The reader's hint (MC3): "about $0.05 a page", "under $0.01 a page", or "Cost not
    measured yet". Never a made-up figure."""
    usd = page_usd(row)
    if usd is None:
        return "Cost not measured yet"
    return "under $0.01 a page" if usd < 0.01 else f"about ${usd:.2f} a page"


def check_read_model(conn: Connection, customer_id: str, settings: Settings, read_model: str | None) -> None:
    """403 ``model_not_allowed`` unless ``read_model`` is None (the default) or one of the
    organisation's usable models (MS5). No query for None."""
    if read_model is not None and read_model not in {
        row["id"] for row in usable(conn, customer_id, settings)
    }:
        raise ApiError(403, "model_not_allowed", NOT_ALLOWED)
