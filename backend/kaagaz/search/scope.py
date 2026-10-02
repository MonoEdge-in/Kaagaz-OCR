"""The scope of a search: which of a customer's documents it may look in (PRD 3 Q1).

``ScopeBody`` is the one filter shape for search and chat; unknown keys, bad ids,
too many ids, an unknown type and dates out of order are refused. Store only
``model_dump()``; a saved scope is parsed and resolved again on reuse.

``resolve`` makes a ``Scope`` once per request, in the customer's transaction:
the folder, or with ``include_subfolders`` its subtree (edge case 2); tags that
are all this customer's, every one on the document (as in the document list);
document ids as given (RLS and ``customer_id = :c`` make another customer's
match nothing). ``doc_types`` is ``documents.doc_type``, never the file type.
Dates are ``coalesce(document_date, the upload day in TIMEZONE)``, both included.

**Matches nothing** is explicit: an unknown folder or tag (another customer's
too) or an empty list is an empty tuple, and search returns nothing before any
search SQL (edge case 1). Only a field left out means "no filter".

Time and space: O(s log F) for s subfolders, O(k log T) for k tags, O(n) for n ids.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection

from kaagaz.db import folders as folders_sql
from kaagaz.db import tags as tags_sql

# The documents.doc_type values (migration 0004's CHECK).
DocType = Literal[
    "invoice", "receipt", "bank_statement", "land_record", "contract", "register", "form", "letter", "other"
]
MAX_DOCUMENT_IDS = 50


class ScopeBody(BaseModel):
    """The Q1 shape. A field left out filters nothing."""

    model_config = ConfigDict(extra="forbid")

    folder_id: uuid.UUID | None = None
    include_subfolders: bool = False
    tag_ids: list[uuid.UUID] | None = Field(default=None, max_length=tags_sql.MAX_TAG_FILTERS)
    doc_types: list[DocType] | None = Field(default=None, max_length=9)  # each type once at most
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_DOCUMENT_IDS)
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _dates_in_order(self) -> Self:
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


@dataclass(frozen=True, slots=True)
class Scope:
    """A resolved scope: None filters nothing, an empty tuple matches nothing.
    ``detected_type`` is the document list's Type filter (PRD 1), which Q3 also
    applies to search inside documents; it is not in the Q1 body."""

    folder_ids: tuple[str, ...] | None = None
    tag_ids: tuple[str, ...] | None = None
    doc_types: tuple[str, ...] | None = None
    detected_type: str | None = None
    document_ids: tuple[str, ...] | None = None
    date_from: date | None = None
    date_to: date | None = None
    timezone: str = "UTC"  # the customer's, for the upload day

    @property
    def matches_nothing(self) -> bool:
        lists = (self.folder_ids, self.tag_ids, self.doc_types, self.document_ids)
        return any(chosen is not None and not chosen for chosen in lists)

    def params(self) -> dict[str, Any]:
        """The bound parameters of the scope predicates in kaagaz/search/hybrid.py."""
        return {
            "s_folders": _listed(self.folder_ids),
            "s_tags": _listed(self.tag_ids),
            "s_doc_types": _listed(self.doc_types),
            "s_detected_type": self.detected_type,
            "s_documents": _listed(self.document_ids),
            "s_from": self.date_from,
            "s_to": self.date_to,
            "s_tz": self.timezone,
        }


EVERYTHING = Scope()  # no scope: every ready, current document of the customer


def resolve(
    conn: Connection, customer_id: str, body: ScopeBody, *, timezone: str, detected_type: str | None = None
) -> Scope:
    """``body`` for this customer, now. ``conn`` comes from ``Database.customer(customer_id)``."""
    folder_ids: tuple[str, ...] | None = None
    if body.folder_id is not None:
        # The folder first, then its subfolders; empty when this customer has no such folder.
        subtree = folders_sql.descendant_ids(conn, customer_id, str(body.folder_id))
        folder_ids = tuple(subtree if body.include_subfolders else subtree[:1])
    tag_ids = _distinct(body.tag_ids)
    if tag_ids and tags_sql.count_existing(conn, customer_id, tag_ids) != len(tag_ids):
        tag_ids = ()  # a tag this customer does not have: no document holds every tag asked for
    return Scope(
        folder_ids=folder_ids,
        tag_ids=tag_ids,
        doc_types=_distinct(body.doc_types),
        detected_type=detected_type,
        document_ids=_distinct(body.document_ids),
        date_from=body.date_from,
        date_to=body.date_to,
        timezone=timezone,
    )


def _distinct(values: Sequence[object] | None) -> tuple[str, ...] | None:
    return None if values is None else tuple(dict.fromkeys(str(value) for value in values))


def _listed(values: tuple[str, ...] | None) -> list[str] | None:
    return None if values is None else list(values)
