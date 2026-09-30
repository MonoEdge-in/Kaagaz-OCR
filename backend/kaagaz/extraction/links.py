"""The identifiers a document is linked by (PRD 2 D5).

Two documents of one customer are linked when both hold the same identifier of
one kind: an invoice, PO, challan, survey, khata, account or reference number
(``pipeline.LINK_KINDS``). GSTIN and PAN never link: every invoice from one
supplier would link to every other. Identifiers are compared with every space
removed and case ignored, the same as the store's SQL
(``lower(regexp_replace(value_text, '\\s', '', 'g'))``).

Only a document's own values count: the card's (record ``card``) and the
document record's (``document``); another match further down
(``page:p:line:n``) is not the document's own number. The card never numbers
an identifier's field, so a document's second PO number links too. The store
saves the links (``db.pipeline_store``); this module only lists them.

Security amendment A7 caps: a normalised identifier longer than 64 characters
is left out, and at most 30 are kept, the first in sorted order.

Time O(v + i log i) for v values and i identifiers; memory O(i).
"""

from collections.abc import Sequence

from kaagaz.extraction.labelled import DOCUMENT_RECORD
from kaagaz.extraction.labels import CARD_RECORD
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import LINK_KINDS, LinkIdentifier

MAX_IDENTIFIER_CHARS = 64  # A7: a normalised identifier
MAX_LINK_IDENTIFIERS = 30  # A7: identifiers per document

_OWN_RECORDS = frozenset({CARD_RECORD, DOCUMENT_RECORD})


def normalise_identifier(value: str) -> str:
    """Every space removed, lower case: "INV 17" and "inv17" are the same number."""
    return "".join(value.split()).lower()


def link_identifiers(values: Sequence[ExtractedValue]) -> tuple[LinkIdentifier, ...]:
    """This document's link identifiers, from its final values: each (kind, value) once,
    sorted, within the caps above."""
    found: set[tuple[str, str]] = set()
    for value in values:
        if value.field in LINK_KINDS and value.record_key in _OWN_RECORDS:
            normal = normalise_identifier(value.value_text)
            if 0 < len(normal) <= MAX_IDENTIFIER_CHARS:
                found.add((value.field, normal))
    return tuple(LinkIdentifier(kind, normal) for kind, normal in sorted(found)[:MAX_LINK_IDENTIFIERS])
