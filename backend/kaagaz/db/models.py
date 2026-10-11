"""Which model reads a document's scans (model choice, council 11 Oct 2026, MS1, MS5).

The operator sets each organisation's allowed models and its default scan model
(``customer_models``, 0023); an upload may ask for one of the allowed models
(``documents.read_model``). ``resolve`` is the worker's own check of that choice: the
browser's choice is never trusted, so a model the organisation no longer allows, or that
the platform no longer offers, falls back to the organisation's default, and an
organisation with no models set is read as before model choice.

Time: one statement over the organisation's allowed models (a few rows).
"""

from sqlalchemy import Connection, text

from kaagaz.settings import DEFAULT_PROVIDER, Settings

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
            usable = [slug for slug in row["providers"] if slug in settings.openrouter_providers]
            if row[wanted] and usable:
                return row["id"], usable[0]
    return settings.ocr_model, DEFAULT_PROVIDER
