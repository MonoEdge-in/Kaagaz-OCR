"""The compact reply of Ultra low cost (#488, ``kaagaz.reading.compact``): the page as delimited
text in one JSON field, encoded from and decoded to the page cache's PAGE_SCHEMA JSON.

A round trip keeps every field the reader uses (tables, unsure words, blocks, continues, the
page's header, exact boxes); the decoder is lenient and never drops text: a row with a bad box
keeps its text and is left unpinned, a row it can't parse at all becomes text only, unpinned."""

import random
from typing import Any

import pytest

from kaagaz.reading.compact import (
    COMPACT_SCHEMA,
    INSTRUCTION_COMPACT,
    KIND_LETTERS,
    decode_compact,
    encode_compact,
)
from kaagaz.reading.gemini_ocr import LINE_KINDS, as_result, checked
from kaagaz.reading.page_quality import MODEL_ISSUES, MODEL_SCRIPTS
from tests.test_gemini_ocr import line, value


def result(*lines: dict[str, Any], **page: Any) -> dict[str, Any]:
    """A reply as the page cache keeps it: checked, then ``as_result`` (orders renumbered)."""
    return as_result(checked(value(*lines, **page)))


def cell(
    text: str, box: tuple[float, float, float, float], row: int, col: int, **more: Any
) -> dict[str, Any]:
    table = {"id": more.pop("table_id", "t1"), "row": row, "col": col, "header": more.pop("header", False)}
    table["col_span"] = more.pop("span", 1)
    return line(text, box, kind="table_cell", table=table, **more)


PAGE = result(
    line("७/१२ उतारा", (40, 100, 70, 900), order=0, kind="title", block=0),
    line("Village Shirur", (80, 100, 100, 600), order=1, kind="heading", block=1),
    line("Owner: Ramesh Patil; heir", (110, 100, 130, 800), order=2, block=1, unsure=("Patil;", "heir")),
    cell("Survey", (150, 100, 170, 300), 0, 0, header=True, order=3, block=2),
    cell("Area (ha)", (150, 300, 170, 700), 0, 1, header=True, span=2, order=4, block=2),
    cell("42/1", (175, 100, 195, 300), 1, 0, order=5, block=2, unsure=("42/1",)),
    cell("2.5 [?]", (175, 300, 195, 500), 1, 1, order=6, block=2),
    line("Sd/-", (900, 700, 930, 900), order=7, kind="signature", block=3),
    line("Seal", (900, 100, 940, 300), order=8, kind="stamp", block=4),
    line("page 1 of 2", (970, 400, 990, 600), order=9, kind="footer", block=5),
    line("and the rest goes on", (950, 100, 965, 900), order=10, kind="note", block=6, continues=True),
    quality="fair",
    handwritten=True,
    scripts=("devanagari", "latin"),
    issues=("faint", "stamp_over_text"),
)


def test_488_kind_letters_cover_the_line_kinds_one_letter_each() -> None:
    assert set(KIND_LETTERS) | {"table_cell"} == set(LINE_KINDS)
    assert len(set(KIND_LETTERS.values())) == len(KIND_LETTERS)
    assert set(KIND_LETTERS.values()) == set("thplHFsgno")
    for kind, letter in KIND_LETTERS.items():
        assert f"{letter} = {kind}" in INSTRUCTION_COMPACT  # the instruction names every letter


def test_488_the_schema_is_one_closed_string_field() -> None:
    assert COMPACT_SCHEMA == {
        "type": "object",
        "properties": {"r": {"type": "string"}},
        "required": ["r"],
        "additionalProperties": False,
    }


def test_488_round_trip_keeps_tables_unsure_words_blocks_and_continues() -> None:
    r = encode_compact(PAGE)
    decoded = decode_compact(r)
    assert decoded.result == PAGE
    assert decoded.unpinned == frozenset()
    rows = r.split("\n")
    assert rows[0] == "f 1 d,l faint,stamp_over_text"
    assert rows[1] == "t 40 100 70 900 ७/१२ उतारा"
    assert "c1.0.1h*2 150 300 170 700 Area (ha)" not in rows  # the table id is in the token
    assert "ct1.0.1h*2 150 300 170 700 Area (ha)" in rows
    assert "ct1.1.0 175 100 195 300 42/1" in rows
    assert "? Patil\\;;heir" in rows  # a ";" inside an unsure word is escaped
    assert rows[-1] == ">"
    assert rows.count("") == 6  # a blank line before each new block


def test_488_the_round_trip_is_lossless_on_random_pages() -> None:
    rng = random.Random(488)  # noqa: S311 - a fixed seed draws test pages, not a secret
    letters = ["paragraph", *KIND_LETTERS]
    for _ in range(300):
        lines = []
        block = 0
        for order in range(rng.randint(0, 25)):
            if order and rng.random() < 0.3:
                block += 1
            y0 = rng.choice([rng.randint(0, 990), round(rng.uniform(0, 990), 3)])
            x0 = rng.randint(0, 990)
            box = (y0, x0, rng.uniform(y0 + 0.5, 1000), rng.randint(x0 + 1, 1000))
            words = [
                rng.choice(["Rs", "५००", "a;b", "x\\y", "[?]", "Total", "ab  cd", "ß"]) for _ in range(3)
            ]
            text = " ".join(words)
            sure = [word for word in words if word != "[?]"]
            unsure = tuple(rng.sample(sure, k=rng.randint(0, min(1, len(sure)))))
            if rng.random() < 0.2:
                lines.append(
                    cell(
                        text,
                        box,
                        rng.randint(0, 9),
                        rng.randint(0, 5),
                        order=order,
                        block=block,
                        unsure=unsure,
                        header=rng.random() < 0.5,
                        span=rng.randint(1, 3),
                    )
                )
            else:
                lines.append(
                    line(text, box, order=order, kind=rng.choice(letters), block=block, unsure=unsure)
                )
        if lines and rng.random() < 0.3:
            lines[-1]["continues_on_next_page"] = True
        page = result(
            *lines,
            quality=rng.choice(["good", "fair", "poor"]),
            handwritten=rng.random() < 0.5,
            scripts=tuple(rng.sample(MODEL_SCRIPTS, k=rng.randint(0, 3))),
            issues=tuple(rng.sample(MODEL_ISSUES, k=rng.randint(0, 3))),
        )
        assert decode_compact(encode_compact(page)).result == page


def test_488_a_blank_page_is_its_header_alone() -> None:
    page = result(scripts=(), quality="poor")
    assert encode_compact(page) == "p 0 - -"
    assert decode_compact("p 0 - -").result == page


def test_488_the_decoded_reply_passes_the_readers_checks() -> None:
    decoded = decode_compact(encode_compact(PAGE))
    assert len(checked(decoded.result).lines) == len(decoded.result["lines"])


# Lenient: never drop text.


def texts(r: str) -> list[str]:
    return [item["text"] for item in decode_compact(r).result["lines"]]


def test_488_a_row_with_a_bad_box_keeps_its_text_and_is_left_unpinned() -> None:
    r = (
        "g 0 l -\np 100 100 120 900 first\np 300 100 290 900 upside down"
        "\np 400 1200 420 900 off the page\np 500 100 520 900 last"
    )
    decoded = decode_compact(r)
    assert texts(r) == ["first", "upside down", "off the page", "last"]
    assert decoded.unpinned == frozenset({1, 2})
    boxes = [item["box_2d"] for item in decoded.result["lines"]]
    assert boxes[1][0] >= 120 and boxes[1][2] <= 500  # between its neighbours
    assert [item["kind"] for item in decoded.result["lines"]] == ["paragraph"] * 4
    checked(decoded.result)  # every made-up box is one the reader takes


def test_488_a_row_it_cannot_parse_becomes_text_only_and_unpinned() -> None:
    r = (
        "g 0 l -\nh 10 10 30 990 Heading\nthe model forgot the kind and box"
        "\np 12 x 30 990 a box with a word in it\n"
    )
    decoded = decode_compact(r)
    assert texts(r) == [
        "Heading",
        "the model forgot the kind and box",
        "p 12 x 30 990 a box with a word in it",
    ]
    assert decoded.unpinned == frozenset({1, 2})
    assert [item["kind"] for item in decoded.result["lines"]] == ["heading", "other", "other"]


def test_488_an_unknown_kind_or_a_bad_table_token_is_never_taken_for_a_kind() -> None:
    # The first word may be the line's own text (the model left the kind out): the whole row is kept.
    r = "g 0 l -\nz 10 10 30 990 Text\nct1.5000.0 10 10 30 990 Cell\nc!!.0.0 40 10 60 990 Other cell"
    decoded = decode_compact(r)
    assert texts(r) == [
        "z 10 10 30 990 Text",
        "ct1.5000.0 10 10 30 990 Cell",
        "c!!.0.0 40 10 60 990 Other cell",
    ]
    assert [(item["kind"], item["table"]) for item in decoded.result["lines"]] == [("other", None)] * 3
    assert decoded.unpinned == frozenset({0, 1, 2})


def test_488_a_row_with_no_text_has_nothing_to_keep() -> None:
    assert texts("g 0 l -\np 10 10 30 990\np 40 10 60 990 B") == ["B"]


def test_488_a_missing_header_is_a_fair_page_and_its_first_row_is_kept() -> None:
    decoded = decode_compact("p 10 10 30 990 Total 500")
    assert decoded.result["page"] == {"quality": "fair", "handwritten": False, "scripts": [], "issues": []}
    assert texts("p 10 10 30 990 Total 500") == ["Total 500"]


def test_488_unknown_scripts_and_issues_are_left_out_of_the_header() -> None:
    page = decode_compact("g 1 l,x,d faint,smudged\np 10 10 30 990 A").result["page"]
    assert page == {
        "quality": "good",
        "handwritten": True,
        "scripts": ["latin", "devanagari"],
        "issues": ["faint"],
    }


def test_488_unsure_words_not_in_their_line_are_left_out_and_never_more_than_its_words() -> None:
    decoded = decode_compact("g 0 l -\np 10 10 30 990 two words\n? two;nowhere;words;two;[?]")
    assert decoded.result["lines"][0]["unsure"] == ["two", "words"]


def test_488_a_question_line_with_no_row_before_it_is_text() -> None:
    assert texts("g 0 l -\n? what is this") == ["? what is this"]


def test_488_continues_only_from_a_final_mark_and_a_mark_inside_is_text() -> None:
    ends = decode_compact("g 0 l -\np 10 10 30 990 A\np 40 10 60 990 B\n>\n").result["lines"]
    assert [item["continues_on_next_page"] for item in ends] == [False, True]
    inside = decode_compact("g 0 l -\np 10 10 30 990 A\n>\np 40 10 60 990 B").result["lines"]
    assert [item["text"] for item in inside] == ["A", ">", "B"]
    assert not any(item["continues_on_next_page"] for item in inside)


def test_488_controls_are_dropped_and_a_row_left_empty_has_no_text_to_keep() -> None:
    decoded = decode_compact("g 0 l -\np 10 10 30 990 To\x07tal‮\np 40 10 60 990 \x01\np 70 10 90 990 B\r")
    assert texts("g 0 l -\np 10 10 30 990 To\x07tal‮\np 40 10 60 990 \x01\np 70 10 90 990 B\r") == [
        "Total",
        "B",
    ]
    assert len(checked(decoded.result).lines) == 2


def test_488_blank_lines_number_the_blocks_and_extra_ones_change_nothing() -> None:
    r = "g 0 l -\n\np 10 10 30 990 A\np 40 10 60 990 B\n\n\n  \np 70 10 90 990 C"
    assert [item["block"] for item in decode_compact(r).result["lines"]] == [0, 0, 1]


def test_488_extra_spaces_between_the_numbers_are_taken() -> None:
    decoded = decode_compact("g 0 l -\np  10 10  30 990  spaced text")
    assert decoded.result["lines"][0]["box_2d"] == [10.0, 10.0, 30.0, 990.0]
    assert decoded.result["lines"][0]["text"] == " spaced text"
    assert decoded.unpinned == frozenset()


def test_488_a_newline_inside_a_cached_line_is_encoded_as_a_space() -> None:
    page = result(line("two\nrows"), scripts=("latin",))
    assert texts(encode_compact(page)) == ["two rows"]


@pytest.mark.parametrize("r", ["", "\n\n", "garbage", "g", "g 0", ">", "?", "c", "\x00"])
def test_488_any_text_decodes_without_an_error(r: str) -> None:
    decoded = decode_compact(r)
    checked(decoded.result)
    assert all(0 <= index < len(decoded.result["lines"]) for index in decoded.unpinned)


def test_488_random_rows_never_lose_a_word() -> None:
    rng = random.Random(4880)  # noqa: S311 - a fixed seed draws test replies, not a secret
    # "?" is left out: a "? " row after a row is that row's unsure words, which are not text.
    alphabet = [
        "p",
        "h",
        "c",
        "t1",
        ".",
        "0",
        "1",
        "12",
        "999",
        "1000",
        "-3",
        " ",
        "  ",
        ";",
        ">",
        "\n",
        "राम",
        "Zed!",
        "₹5",
    ]
    for _ in range(2000):
        r = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        decoded = decode_compact(r)
        reply = checked(decoded.result)
        kept = " ".join(item.text for item in reply.lines)
        for word in ("राम", "Zed!", "₹5"):
            assert kept.count(word) == r.count(word), r
