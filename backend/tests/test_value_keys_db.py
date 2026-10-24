"""The company vocabulary kept fresh (``value_keys``, 0049, #245) against a real PostgreSQL:
a save keys the land people, places and properties (and nothing else), a read again replaces
the keys with the values, and a review resolve, a word change and its undo key the value
again, so a key always equals the key freshly computed from the value's text."""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz.chunking.piece import Piece
from kaagaz.db import review
from kaagaz.db.engine import Database
from kaagaz.db.pipeline_store import PgPipelineStore
from kaagaz.extraction.land import value_key
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import DocumentContent, PageRecord, ReaderOutput
from kaagaz.reading.spans import PageInfo, Span
from tests.db_support import insert_document, new_customer
from tests.test_word_review_db import LINE, MARK, VALUE_BOX, change, key, load, undo

pytestmark = pytest.mark.db

KEYS = (
    "SELECT v.field, v.value_text, k.kind, k.place_level, k.latin, k.sound, k.document_id::text"
    " FROM value_keys k JOIN extracted_values v ON v.customer_id = k.customer_id AND v.id = k.value_id"
    " ORDER BY v.field, v.value_text"
)


def _value(record_key: str, field: str, value_text: str, **more: Any) -> ExtractedValue:
    return ExtractedValue(record_key, field, value_text, value_text, VALUE_BOX, **more)


LAND = [
    _value("land:1", "land_survey_number", "४५ / २"),
    _value("land:1", "land_village", "कुसगाव"),
    _value("land:1", "land_area", "0.40 H.R."),
    _value("land:1:party:1", "land_owner_name", "Krishna Sohoni", label_reason="unsure_text"),
    _value("land:1:party:1", "land_share", "50%", value_number=Decimal(50)),
    _value("card", "owner_name", "Krishna Sohoni", label_reason="model_chosen"),  # a card value: no key
]


def _saved(db: Database, values: list[ExtractedValue]) -> tuple[str, str, str]:
    customer, user = new_customer(db)
    with db.customer(customer) as conn:
        document = insert_document(conn, customer, uploaded_by=user, detected_type="jpeg")
    PgPipelineStore(db).save(_content(customer, document, values))
    return customer, user, document


def _content(customer: str, document: str, values: list[ExtractedValue]) -> DocumentContent:
    """A one-page scan whose line names an owner, the word "Sohoni" read unsure, with ``values``."""
    line = "Owner Krishna Sohoni"
    spans = (
        Span(line, line, LINE, None, "paragraph", "ocr", quality="ok", box_source="found"),
        Span("Sohoni", "Sohoni", MARK, 0.62, origin="ocr", quality="unsure", box_source="found"),
    )
    page = PageRecord(PageInfo(1, 595.0, 842.0, "pt"), True, "ocr", read_quality="uncertain", unsure_words=1)
    piece = Piece(customer, document, line, (LINE,), "needs_review")
    outputs = [ReaderOutput("first", "google/gemini-3.1-pro-preview", "1", spans)]
    return DocumentContent(customer, document, [page], outputs, [piece], values, reader="gemini")


def _keys(db: Database, customer: str) -> list[tuple[Any, ...]]:
    with db.customer(customer) as conn:
        return [tuple(row) for row in conn.execute(text(KEYS))]


def _fresh(field: str, value_text: str, document: str) -> tuple[Any, ...]:
    computed = value_key(field, value_text)
    assert computed is not None
    return (field, value_text, *computed, document)


def test_a_save_keys_land_people_places_and_properties_and_a_read_again_replaces_them(db: Database) -> None:
    customer, _user, document = _saved(db, LAND)
    assert _keys(db, customer) == [
        ("land_owner_name", "Krishna Sohoni", "person", None, "krishna sohoni", "krisn sohoni", document),
        ("land_survey_number", "४५ / २", "property", None, "45/2", "", document),
        ("land_village", "कुसगाव", "place", "village", "kusagav", "kusgv", document),
    ]  # the area, the share and the card's owner have none

    again = [_value("land:1:party:1", "land_owner_name", "Nikhil Varde")]
    PgPipelineStore(db).save(_content(customer, document, again))
    assert _keys(db, customer) == [_fresh("land_owner_name", "Nikhil Varde", document)]


def test_a_review_resolve_keys_the_value_again_and_numbers_a_share_only_as_a_percentage(db: Database) -> None:
    customer, user, document = _saved(db, LAND)
    with db.customer(customer) as conn:
        rows = conn.execute(
            text("SELECT id, document_id, field, value_text, value_raw FROM extracted_values WHERE field IN"
                 " ('land_owner_name', 'land_share') ORDER BY field")
        ).mappings().all()  # fmt: skip
        owner, share = rows
        for current, new_text in ((owner, "कृष्ण सोहोनी"), (share, "1/3")):
            review.resolve_value(conn, customer, current, new_text=new_text, choice="typed", remember=False,
                                 user_id=user)  # fmt: skip
        number = conn.execute(
            text("SELECT value_number FROM extracted_values WHERE id = :v"), {"v": share["id"]}
        )
        assert number.scalar_one() is None  # "1/3" is kept as printed, never a number
    assert _fresh("land_owner_name", "कृष्ण सोहोनी", document) in _keys(db, customer)
    assert ("land_owner_name", "Krishna Sohoni") not in [row[:2] for row in _keys(db, customer)]


def test_a_word_change_and_its_undo_key_the_value_again(db: Database) -> None:
    customer, user, document = _saved(db, LAND)
    word = key(load(db, customer, document), "mark", "Sohoni")
    change_id = change(db, customer, document, word, "Sohoni", "Sohony", user)
    assert _fresh("land_owner_name", "Krishna Sohony", document) in _keys(db, customer)
    undo(db, customer, document, change_id, user)
    assert _fresh("land_owner_name", "Krishna Sohoni", document) in _keys(db, customer)
    assert ("land_owner_name", "Krishna Sohony") not in [row[:2] for row in _keys(db, customer)]
