"""Which model reads a document's scans (model choice, council 11 Oct 2026, MS1, MS4, MS5).

The operator sets each organisation's allowed models and its default scan model
(``customer_models``, 0023); an upload may ask for one of the allowed models
(``documents.read_model``). ``resolve`` is the worker's own check of that choice: the
browser's choice is never trusted, so a model the organisation no longer allows, or that
the platform no longer offers, falls back to the organisation's default, and an
organisation with no models set is read as before model choice. ``usable_models`` is the
same rule for the API: the models an upload or a read again may name, the default first.

A model is usable while the organisation allows it, it is eligible, the operator enabled
it, it is verified for the reader's prompt version, and one of its providers is in
OPENROUTER_PROVIDERS (the first such, in the catalogue's order, reads it).

Time: one statement over the organisation's allowed models (a few rows).
"""

from collections.abc import Sequence

from sqlalchemy import Connection, RowMapping, text

from kaagaz.settings import DEFAULT_PROVIDER, Settings

# The reader's prompt version a model must be verified for (``usable_models``). The same
# value as kaagaz.reading.gemini_ocr.GEMINI_OCR_PROMPT_VERSION, which a test checks; kept
# here so the database layer does not import the reader.
# ponytail: a copy, until the reading layer exposes the version outside gemini_ocr.
PROMPT_VERSION = "g1"

# The document's chosen model and the organisation's default, each only while it is allowed
# for the organisation, eligible, enabled by the operator and verified for this prompt.
_USABLE = text(
    "SELECT c.id, c.providers, m.is_default_scan, c.id = d.read_model AS chosen"
    " FROM documents d"
    " JOIN customer_models m ON m.customer_id = d.customer_id"
    " JOIN model_catalog c ON c.id = m.model"
    " WHERE d.customer_id = :customer AND d.id = :document"
    " AND (c.id = d.read_model OR m.is_default_scan)"
    " AND c.eligible AND c.enabled_at IS NOT NULL AND c.verified_prompt_version = :prompt_version"
)

# Every model the organisation allows that is eligible, enabled, verified for this prompt
# and served by an approved provider: the default first, then by name.
_ALLOWED = text(
    "SELECT c.id, c.name, c.price_in_usd_per_m, c.price_out_usd_per_m, c.tokens_per_page_in,"
    " c.tokens_per_page_out, m.is_default_scan, c.providers"
    " FROM customer_models m"
    " JOIN model_catalog c ON c.id = m.model"
    " WHERE m.customer_id = :customer"
    " AND c.eligible AND c.enabled_at IS NOT NULL AND c.verified_prompt_version = :prompt_version"
    " AND c.providers && CAST(:providers AS text[])"
    " ORDER BY m.is_default_scan DESC, c.name, c.id"
)


def _provider(providers: Sequence[str], settings: Settings) -> str | None:
    """The first of ``providers`` (the catalogue's order) inside OPENROUTER_PROVIDERS, or None."""
    return next((slug for slug in providers if slug in settings.openrouter_providers), None)


def resolve(
    conn: Connection,
    customer_id: str,
    document_id: str,
    *,
    settings: Settings,
    prompt_version: str,
) -> tuple[str, str]:
    """(model, provider) that reads ``document_id``'s scans: its ``read_model`` when the
    organisation allows it, else the organisation's default, each only while it is
    eligible, enabled and verified for ``prompt_version`` (the reader's prompt version) and
    one of its providers is in OPENROUTER_PROVIDERS (the first such, in the catalogue's
    order); else OCR_MODEL on the default provider, as before model choice. On a
    ``Database.customer()`` connection of ``customer_id``."""
    rows = (
        conn.execute(
            _USABLE, {"customer": customer_id, "document": document_id, "prompt_version": prompt_version}
        )
        .mappings()
        .all()
    )
    for wanted in ("chosen", "is_default_scan"):
        for row in rows:
            slug = _provider(row["providers"], settings)
            if row[wanted] and slug is not None:
                return row["id"], slug
    return settings.ocr_model, DEFAULT_PROVIDER


def usable_models(conn: Connection, customer_id: str, *, settings: Settings) -> Sequence[RowMapping]:
    """The models ``customer_id`` may read scans with now (MS4, MS5), the ones ``resolve``
    accepts for PROMPT_VERSION, the default first, then by name: what the API offers as
    "Read with" and accepts as ``read_model``. Keys: id, name, price_in_usd_per_m,
    price_out_usd_per_m, tokens_per_page_in, tokens_per_page_out, is_default_scan,
    providers. Empty: the organisation has none set (every scan is read with OCR_MODEL, as
    before), or none is usable now. On a ``Database.customer()`` connection of
    ``customer_id``."""
    params = {
        "customer": customer_id,
        "prompt_version": PROMPT_VERSION,
        "providers": sorted(settings.openrouter_providers),
    }
    return conn.execute(_ALLOWED, params).mappings().all()
