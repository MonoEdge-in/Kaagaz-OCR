"""The scan model resolver's Python half, without a database (model choice, council 11 Oct
2026, MS4, MS5, MC3): which provider reads a model, the fallbacks when a row is not
usable, what ``usable_models`` asks for, and that its prompt version is the reader's. The
SQL itself: test_db_models_resolve_db."""

from collections.abc import Mapping
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from kaagaz.db import models
from kaagaz.reading import gemini_ocr
from kaagaz.reading.ocr import READER_PROMPT_VERSION
from tests.settings_factory import make_settings

CUSTOMER = "00000000-0000-0000-0000-00000000000a"
DOCUMENT = "00000000-0000-0000-0000-00000000000c"
SETTINGS = make_settings()


class _Rows:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self.rows = rows
        self.sent: list[Any] = []

    def execute(self, statement: object, params: Any) -> "_Rows":
        self.sent.append(params)
        return self

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self.rows


def _model(
    model: str, providers: list[str], *, default: bool = False, chosen: bool | None = None
) -> dict[str, Any]:
    return {
        "id": model,
        "name": model.split("/")[1],
        "providers": providers,
        "price_in_usd_per_m": 2,
        "price_out_usd_per_m": 12,
        "tokens_per_page_in": 1000,
        "tokens_per_page_out": 500,
        "is_default_scan": default,
        "chosen": chosen,
    }


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        # The chosen model has no provider inside the ceiling, and the default none either.
        (
            [
                _model("a/chosen", ["azure"], chosen=True),
                _model("a/default", ["amazon-bedrock"], default=True),
            ],
            (SETTINGS.ocr_model, "google-vertex"),
        ),
        # The default is also the chosen one.
        ([_model("a/both", ["google-vertex"], default=True, chosen=True)], ("a/both", "google-vertex")),
        # The first provider inside the ceiling, in the catalogue's order.
        (
            [_model("a/chosen", ["amazon-bedrock", "azure", "google-vertex"], chosen=True)],
            ("a/chosen", "google-vertex"),
        ),
    ],
)
def test_the_resolver_falls_back_row_by_row(rows: list[Mapping[str, Any]], expected: tuple[str, str]) -> None:
    conn = _Rows(rows)
    assert (
        models.resolve(cast(Connection, conn), CUSTOMER, DOCUMENT, settings=SETTINGS, prompt_version="g2")
        == expected
    )
    assert conn.sent == [{"customer": CUSTOMER, "document": DOCUMENT, "prompt_version": "g2"}]


def test_usable_models_ask_for_the_readers_prompt_and_the_approved_providers() -> None:
    rows: list[Mapping[str, Any]] = [_model("a/default", ["google-vertex"], default=True)]
    conn = _Rows(rows)
    wider = make_settings(openrouter_providers=frozenset({"google-vertex", "azure"}))
    assert list(models.usable_models(cast(Connection, conn), CUSTOMER, settings=wider)) == rows
    assert conn.sent == [
        {
            "customer": CUSTOMER,
            "prompt_version": READER_PROMPT_VERSION,
            "providers": ["azure", "google-vertex"],
            "days": models.LEDGER_DAYS,
        }
    ]


def test_the_prompt_version_is_the_readers_own_not_a_copy() -> None:
    """R6: usable_models asks for kaagaz.reading.ocr's READER_PROMPT_VERSION itself, so no
    copy can drift from the reader's."""
    assert not hasattr(models, "PROMPT_VERSION")
    assert READER_PROMPT_VERSION is gemini_ocr.GEMINI_OCR_PROMPT_VERSION
