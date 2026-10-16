"""The scan model resolver's Python half, without a database (model choice, council 11 Oct
2026, MS4, MS5, MC3): which provider reads a model, the fallbacks when a row is not
usable, what ``usable_models`` asks for, and that its prompt version is the reader's. The
SQL itself: test_db_models_resolve_db.

Changed on purpose (features 25-27, 15 Oct 2026): the resolver now asks for the area's list
(the organisation's, else the platform's) and the document's choice apart, and filters the
providers and the vendors in Python, so the fake answers each statement
(tests.test_ai_resolve.FakeConn) and the models are google ones."""

from collections.abc import Mapping
from typing import Any

import pytest

from kaagaz.db import models
from kaagaz.reading import gemini_ocr
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from tests.settings_factory import make_settings
from tests.test_ai_resolve import CUSTOMER, DOCUMENT, FakeConn, document, row

SETTINGS = make_settings()


@pytest.mark.parametrize(
    ("rows", "chosen", "expected"),
    [
        # The chosen model has no provider inside the ceiling, and the default none either.
        (
            [
                row("google/default", default=True, providers=["amazon-bedrock"]),
                row("google/chosen", providers=["azure"]),
            ],
            "google/chosen",
            (SETTINGS.ocr_model, "google-vertex"),
        ),
        # The default is also the chosen one.
        ([row("google/both", default=True)], "google/both", ("google/both", "google-vertex")),
        # The first provider inside the ceiling, in the catalogue's order.
        (
            [row("google/chosen", providers=["amazon-bedrock", "azure", "google-vertex"])],
            "google/chosen",
            ("google/chosen", "google-vertex"),
        ),
    ],
)
def test_the_resolver_falls_back_row_by_row(
    rows: list[Mapping[str, Any]], chosen: str, expected: tuple[str, str]
) -> None:
    conn = FakeConn(scan=rows, document=document(chosen))
    assert (
        models.resolve(
            conn.conn(), CUSTOMER, DOCUMENT, settings=SETTINGS, prompt_version=READER_PROMPT_VERSION
        )
        == expected
    )
    assert conn.sent[1] == (  # after the document's choice
        models._SCAN,
        {"customer": CUSTOMER, "prompt": READER_PROMPT_VERSION, "days": models.LEDGER_DAYS},
    )


def test_usable_models_ask_for_the_readers_prompt_and_keep_the_approved_providers() -> None:
    rows: list[Mapping[str, Any]] = [
        row("google/default", default=True),
        row("google/azure-only", providers=["azure"]),
    ]
    conn = FakeConn(scan=rows)
    wider = make_settings(openrouter_providers=frozenset({"google-vertex", "azure"}))
    assert models.usable_models(conn.conn(), CUSTOMER, settings=wider) == rows
    assert models.usable_models(conn.conn(), CUSTOMER, settings=SETTINGS) == rows[:1]
    assert (
        conn.sent
        == [
            (
                models._SCAN,
                {"customer": CUSTOMER, "prompt": READER_PROMPT_VERSION, "days": models.LEDGER_DAYS},
            )
        ]
        * 2
    )


def test_the_prompt_version_is_the_readers_own_not_a_copy() -> None:
    """R6: usable_models asks for kaagaz.reading.ocr's READER_PROMPT_VERSION itself, so no
    copy can drift from the reader's."""
    assert not hasattr(models, "PROMPT_VERSION")
    assert READER_PROMPT_VERSION is gemini_ocr.GEMINI_OCR_PROMPT_VERSION
