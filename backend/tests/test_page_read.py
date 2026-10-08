"""The page result of the Gemini reader's socket (PRD 5 G1, G3, G5, G7, F1)."""

import dataclasses
from collections.abc import Callable
from typing import Any, cast

import pytest
from PIL import Image

from kaagaz.reading.ocr import OcrEngine, OcrLine, OcrTable, PageEngine, PageRead

BASE = PageRead(
    page_no=3,
    lines=(),
    quality="fair",
    handwritten=True,
    scripts=("devanagari", "latin"),
    issues=("handwriting_hard",),
    read_by="gemini-3.1-pro-preview",
)


def page(*lines: OcrLine, **changes: Any) -> PageRead:
    return dataclasses.replace(BASE, lines=lines, **changes)


TITLE = OcrLine("७/१२ उतारा", 100, 50, 900, 120, None, "title", box_source="found")
MARGIN = OcrLine(
    "पाटील [?] यांचे [?]", 40, 300, 380, 340, None, "paragraph", unsure=("पाटील", "यांचे"), box_source="model"
)
CELL = OcrLine("1,180.00", 600, 900, 780, 940, None, "table_cell", OcrTable("t1", 2, 3, False), (), "found")


def test_the_counts_f1_stores_come_from_the_lines() -> None:
    read = page(TITLE, MARGIN, CELL)
    assert (read.unsure_words, read.illegible_words, read.unpinned_lines) == (2, 2, 1)
    empty = page(issues=("no_text",))
    assert (empty.unsure_words, empty.illegible_words, empty.unpinned_lines) == (0, 0, 0)


def test_a_fresh_read_is_not_cached_and_the_billing_defaults_to_nothing() -> None:
    read = page(TITLE)
    assert (read.input_tokens, read.output_tokens, read.cached, read.continues_on_next_page) == (
        0,
        0,
        False,
        False,
    )
    billed = page(TITLE, input_tokens=1700, output_tokens=4000, continues_on_next_page=True)
    assert (billed.input_tokens, billed.output_tokens, billed.continues_on_next_page) == (1700, 4000, True)


def test_a_page_read_is_frozen_and_built_by_name_only() -> None:
    read = page(TITLE)
    field_name = "page_no"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(read, field_name, 4)
    by_position = cast(Callable[..., PageRead], PageRead)
    with pytest.raises(TypeError):
        by_position(3, (TITLE,), "good", False, (), (), "rapidocr")
    assert [field.name for field in dataclasses.fields(PageRead)] == [
        "page_no",
        "lines",
        "quality",
        "handwritten",
        "scripts",
        "issues",
        "read_by",
        "input_tokens",
        "output_tokens",
        "cached",
        "continues_on_next_page",
    ]


class FakePageEngine:
    name = "gemini"
    version = "gemini-3.1-pro-preview;prompt=1"

    def read(self, image: Image.Image) -> list[OcrLine]:
        return list(self.read_page(image, page_no=1).lines)

    def read_page(self, image: Image.Image, *, page_no: int) -> PageRead:
        return page(TITLE, page_no=page_no)


class LinesOnly:
    name = "rapidocr"
    version = "3.9.2"

    def read(self, image: Image.Image) -> list[OcrLine]:
        return [TITLE]


def test_a_page_engine_is_an_ocr_engine_that_also_reads_numbered_pages() -> None:
    engine: PageEngine = FakePageEngine()
    plain: OcrEngine = engine  # a page engine still plugs in wherever an OcrEngine does
    image = Image.new("RGB", (10, 10), "white")
    assert engine.read_page(image, page_no=7).page_no == 7
    assert plain.read(image) == [TITLE]
    assert isinstance(engine, PageEngine)
    assert not isinstance(LinesOnly(), PageEngine)
