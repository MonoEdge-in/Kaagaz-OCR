"""The identifiers a document is linked by (PRD 2 D5).

Two documents of one customer are linked when both hold the same identifier of
one kind: an invoice, PO, challan, survey, khata, account or reference number
(``pipeline.LINK_KINDS``). GSTIN and PAN never link: every invoice from one
supplier would link to every other. Identifiers are compared with every space
removed, case ignored and Devanagari digits as ASCII digits (council fix 10), the
same as the store's SQL (``translate(lower(regexp_replace(value_text, '\\s', '',
'g')), '०१२३४५६७८९', '0123456789')``).

``FORM_NAMES`` (PROVISIONAL, council fix 10) are the names of forms, not the
number of one document: as a survey or khata number, "7/12" (the land record
extract) or "8-A" would link every land record to every other, so they never link.

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

from kaagaz.chunking.script import fold_digits
from kaagaz.extraction.labelled import DOCUMENT_RECORD
from kaagaz.extraction.values import CARD_RECORD, ExtractedValue
from kaagaz.pipeline import LINK_KINDS, LinkIdentifier

MAX_IDENTIFIER_CHARS = 64  # A7: a normalised identifier
MAX_LINK_IDENTIFIERS = 30  # A7: identifiers per document
FORM_NAMES = frozenset({"7/12", "8a", "8-a", "8\N{DEVANAGARI LETTER A}"})  # normalised; the module docstring

_OWN_RECORDS = frozenset({CARD_RECORD, DOCUMENT_RECORD})


def normalise_identifier(value: str) -> str:
    """Every space removed, lower case, Devanagari digits as ASCII: "INV 17" and "inv17"
    are the same number, and so are ४५/२ and 45/2."""
    return fold_digits("".join(value.split()).lower())


def link_identifiers(values: Sequence[ExtractedValue]) -> tuple[LinkIdentifier, ...]:
    """This document's link identifiers, from its final values: each (kind, value) once,
    sorted, within the caps above."""
    found: set[tuple[str, str]] = set()
    for value in values:
        if value.field in LINK_KINDS and value.record_key in _OWN_RECORDS:
            normal = normalise_identifier(value.value_text)
            if 0 < len(normal) <= MAX_IDENTIFIER_CHARS and normal not in FORM_NAMES:
                found.add((value.field, normal))
    return tuple(LinkIdentifier(kind, normal) for kind, normal in sorted(found)[:MAX_LINK_IDENTIFIERS])
