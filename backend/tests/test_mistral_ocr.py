"""The Mistral OCR page reader (#473, Task 3): the client against a fake ``/v1/ocr`` on
127.0.0.1 (``tests/openrouter_fake.py``, path ``ocr``), and ``page_read`` on made-up replies.

Never the real API and never a real key: ``KEY`` is made up. A fake clock (``Clock``) drives
the switch-off, so no test sleeps for real except the slow reply's.
"""

import base64
import logging
import time
import unicodedata
import urllib.request
from http.client import IncompleteRead
from typing import Any, cast
from unittest import mock

import pytest

from kaagaz.reading import mistral_reader
from kaagaz.reading.mistral_ocr import MISTRAL_ENDPOINT, MISTRAL_OCR_MODELS, MISTRAL_TIMEOUT_SECONDS
from kaagaz.reading.mistral_reader import (
    KEY_OFF_SECONDS,
    OUTAGE_OFF_SECONDS,
    OUTAGES_IN_A_ROW,
    BadReply,
    MistralClient,
    MistralUnavailable,
    page_read,
    trimmed,
)
from kaagaz.reading.ocr import MAX_BLOCK_CHARS, MAX_BLOCKS_PER_PAGE, PageRead
from tests.openrouter_fake import SECRET, OpenRouterFake, Reply, running_fake, slow

KEY = "test-mistral-key"
PNG = b"\x89PNG made-up page"
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
OCR_4 = MISTRAL_OCR_MODELS["mistral-ocr-4-1"]
OCR_3 = MISTRAL_OCR_MODELS["mistral-ocr-2512"]
# Mistral's pixels (the reply's dimensions) and ours (the image given to page_read): half.
DIMS = {"dpi": 200, "width": 2000, "height": 1000}
WIDTH, HEIGHT = 1000, 500


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def block(kind: str, content: str, box: tuple[float, float, float, float] | None = None) -> dict[str, Any]:
    """A reply block; ``box`` is (x0, y0, x1, y1) in Mistral's pixels, or None for none."""
    value: dict[str, Any] = {"type": kind, "content": content}
    if box is not None:
        value.update(top_left_x=box[0], top_left_y=box[1], bottom_right_x=box[2], bottom_right_y=box[3])
    return value


def page(
    markdown: str,
    blocks: list[dict[str, Any]] | None = None,
    *,
    words: list[dict[str, Any]] | None = None,
    average: float | None = 0.95,
) -> dict[str, Any]:
    value: dict[str, Any] = {"index": 0, "markdown": markdown, "images": [], "dimensions": dict(DIMS)}
    if blocks is not None:
        value["blocks"] = blocks
    if average is not None or words is not None:
        value["confidence_scores"] = {
            "average_page_confidence_score": average,
            "word_confidence_scores": words or [],
        }
    return value


def word(text: str, confidence: float, start: int | None) -> dict[str, Any]:
    return {"text": text, "confidence": confidence, "start_index": start}


def ok(value: dict[str, Any], processed: int = 1) -> Reply:
    return Reply(
        200,
        {
            "pages": [value],
            "model": "mistral-ocr-4-1",
            "document_annotation": None,
            "usage_info": {"pages_processed": processed, "doc_size_bytes": len(PNG)},
        },
    )


def error(status: int) -> Reply:
    """An error reply like Mistral's: its message holds page text that must never be kept."""
    return Reply(
        status, {"object": "error", "message": SECRET, "type": "invalid_request_error", "code": "3310"}
    )


def client(fake: OpenRouterFake, clock: Clock | None = None) -> MistralClient:
    return MistralClient(KEY, url=fake.url + "/ocr", now=clock or Clock())


def read(value: dict[str, Any], found: list[tuple[float, float, float, float]] | None = None) -> PageRead:
    return page_read(value, page_no=3, model=OCR_4, found=found or [], width=WIDTH, height=HEIGHT)


def texts(result: PageRead) -> list[str]:
    return [line.text for line in result.lines]


def box(line: Any) -> tuple[float, float, float, float]:
    return line.x0, line.y0, line.x1, line.y1


class Body:
    """A 200 whose body can't be read whole: ``failure`` raised by each read, or (None) one
    that keeps trickling in past the deadline."""

    def __init__(self, failure: Exception | None) -> None:
        self.failure = failure

    def __enter__(self) -> "Body":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read1(self, size: int) -> bytes:
        if self.failure is not None:
            raise self.failure
        time.sleep(0.05)
        return b" "


class Answers:
    """An opener whose every ``open`` answers 200 with ``body``."""

    def __init__(self, body: Body) -> None:
        self.body = body

    def open(self, request: urllib.request.Request, timeout: float) -> Body:
        return self.body


# The client.


def test_request_exact() -> None:
    assert MISTRAL_ENDPOINT == "https://api.mistral.ai/v1/ocr"
    value = page("Total 500")
    with running_fake() as fake:
        fake.script("ocr", ok(value, processed=1))
        got, processed = client(fake).read(PNG, model="mistral-ocr-4-1", seconds=10)
    (request,) = fake.requests
    assert request.method == "POST"
    assert request.path == "/api/v1/ocr"
    assert request.body == {
        "model": "mistral-ocr-4-1",
        "document": {"type": "image_url", "image_url": DATA_URL},
        "include_image_base64": False,
        "confidence_scores_granularity": "word",
    }
    assert "table_format" not in request.body
    fake.assert_key_in_header_only(KEY)
    assert (got, processed) == (value, 1)


def test_a_garbage_200_is_returned_as_an_empty_page_then_bad_reply() -> None:
    """A 200 is billed by Mistral, so the client returns it (one page) and page_read refuses it."""
    with running_fake() as fake:
        fake.script("ocr", Reply(200, b"not json " + SECRET.encode()), Reply(200, {"pages": []}))
        mistral = client(fake)
        assert mistral.read(PNG, model="mistral-ocr-4-1", seconds=10) == ({}, 1)
        assert mistral.read(PNG, model="mistral-ocr-4-1", seconds=10) == ({}, 1)
    with pytest.raises(BadReply):
        read({})


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_401_402_switch_off_300s(status: int) -> None:
    clock = Clock()
    with running_fake() as fake:
        fake.script("ocr", error(status), ok(page("Total")))
        mistral = client(fake, clock)
        with pytest.raises(MistralUnavailable) as raised:
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
        assert raised.value.code == "mistral_key" and not raised.value.billed  # no 200: not billed
        assert mistral.off_seconds() == KEY_OFF_SECONDS
        clock.t += KEY_OFF_SECONDS - 1
        with pytest.raises(MistralUnavailable) as again:
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
        assert again.value.code == "mistral_key"
        assert len(fake.requests) == 1  # switched off: raised at once, nothing sent
        assert mistral.off_seconds() == 1
        clock.t += 1
        assert mistral.off_seconds() == 0.0
        mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
    assert KEY_OFF_SECONDS == 300
    assert len(fake.requests) == 2


@pytest.mark.parametrize("status", [400, 413, 422])
def test_a_refused_request_is_bad_reply_never_a_switch_off(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Lead decision (2 Nov 2026): 400, 413 and 422 refuse this page, not Mistral: BadReply
    (a refused page, not billed: no 200), no switch-off and no outage counted."""
    with running_fake() as fake:
        fake.script("ocr", error(500), error(500), *[error(status)] * 3, error(500), ok(page("Total")))
        mistral = client(fake)
        for _ in range(2):
            with pytest.raises(MistralUnavailable):
                mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
        for _ in range(3):
            with pytest.raises(BadReply) as refused:
                mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
            assert refused.value.__cause__ is None and refused.value.__suppress_context__
            assert str(refused.value) == "refused_request"  # review B2: not an unreadable 200
        with pytest.raises(MistralUnavailable) as third:  # the third outage in a row switches off
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
        assert third.value.code == "mistral"
        with pytest.raises(MistralUnavailable):
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
    assert len(fake.requests) == 6
    assert str(status) in caplog.text and SECRET not in caplog.text
    codes = [getattr(record, "code", None) for record in caplog.records]
    assert codes.count("refused_request") == 3 and "bad_reply" not in codes


@pytest.mark.parametrize("status", [408, 302, 418])
def test_other_statuses_are_outages(status: int) -> None:
    with running_fake() as fake:
        fake.script("ocr", Reply(status, {"object": "error", "message": SECRET}))
        with pytest.raises(MistralUnavailable) as raised:
            client(fake).read(PNG, model="mistral-ocr-4-1", seconds=10)
    assert raised.value.code == "mistral"


def test_three_outages_switch_off_60s() -> None:
    clock = Clock()
    with running_fake() as fake:
        fake.script("ocr", error(429), error(500), error(503), ok(page("Total")))
        mistral = client(fake, clock)
        for _ in range(3):
            with pytest.raises(MistralUnavailable) as raised:
                mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
            assert raised.value.code == "mistral"
        with pytest.raises(MistralUnavailable) as off:
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
        assert off.value.code == "mistral"
        assert len(fake.requests) == 3
        clock.t += OUTAGE_OFF_SECONDS
        mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
    assert OUTAGE_OFF_SECONDS == 60
    assert len(fake.requests) == 4


def test_network_failure_is_an_outage() -> None:
    with running_fake() as fake:
        url = fake.url + "/ocr"
    mistral = MistralClient(KEY, url=url, now=Clock())  # the fake has shut down: nothing listens
    with pytest.raises(MistralUnavailable) as raised:
        mistral.read(PNG, model="mistral-ocr-4-1", seconds=5)
    assert raised.value.code == "mistral"


def test_success_resets_count() -> None:
    with running_fake() as fake:
        fake.script("ocr", error(500), error(500), ok(page("A")), error(500), error(500), ok(page("B")))
        mistral = client(fake)
        for expected in ("outage", "outage", "ok", "outage", "outage", "ok"):
            if expected == "ok":
                mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
            else:
                with pytest.raises(MistralUnavailable):
                    mistral.read(PNG, model="mistral-ocr-4-1", seconds=10)
    assert len(fake.requests) == 6


def test_seconds_cap_timeout() -> None:
    with running_fake() as fake:
        fake.script("ocr", slow(ok(page("Total")), 5.0))
        mistral = client(fake)
        started = time.monotonic()
        with pytest.raises(MistralUnavailable) as raised:
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=0.3)
        assert time.monotonic() - started < 3.0
    assert raised.value.code == "mistral"

    class Opener:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def open(self, request: urllib.request.Request, timeout: float) -> Any:
            self.timeouts.append(timeout)
            raise TimeoutError

    opener = Opener()
    capped = MistralClient(KEY, opener=cast(urllib.request.OpenerDirector, opener), now=Clock())
    for seconds in (100.0, 7.5):
        with pytest.raises(MistralUnavailable):
            capped.read(PNG, model="mistral-ocr-4-1", seconds=seconds)
    assert opener.timeouts == [MISTRAL_TIMEOUT_SECONDS, 7.5]
    assert MISTRAL_TIMEOUT_SECONDS == 30
    for _ in range(OUTAGES_IN_A_ROW):  # review B4: no time left is not an outage
        with pytest.raises(BadReply, match=r"^mistral_no_time$"):
            capped.read(PNG, model="mistral-ocr-4-1", seconds=0)
    assert opener.timeouts == [MISTRAL_TIMEOUT_SECONDS, 7.5]  # no time left: nothing sent
    assert capped.off_seconds() == 0.0  # and never switched off


@pytest.mark.parametrize("failure", [ConnectionResetError(), IncompleteRead(b"{"), None])
def test_a_200_whose_body_fails_is_billed_then_an_outage(failure: Exception | None) -> None:
    """Review B1: once Mistral answered 200 the page is billed, whatever then happens to its
    body (a reset, a short read, a body trickling past the deadline); then it is an outage."""
    opener = cast(urllib.request.OpenerDirector, Answers(Body(failure)))
    mistral = MistralClient(KEY, opener=opener, now=Clock())
    for _ in range(OUTAGES_IN_A_ROW):
        with pytest.raises(MistralUnavailable) as raised:
            mistral.read(PNG, model="mistral-ocr-4-1", seconds=0.02)
        assert (raised.value.code, raised.value.billed) == ("mistral", True)
    assert mistral.off_seconds() == OUTAGE_OFF_SECONDS  # each counted as an outage


def test_key_and_markdown_never_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    raised: list[BaseException] = []
    with running_fake() as fake:
        fake.script("ocr", error(401))
        with pytest.raises(MistralUnavailable) as key_problem:
            client(fake).read(PNG, model="mistral-ocr-4-1", seconds=10)
        raised.append(key_problem.value)
        fake.script("ocr", error(500))
        with pytest.raises(MistralUnavailable) as outage:
            client(fake).read(PNG, model="mistral-ocr-4-1", seconds=10)
        raised.append(outage.value)
        fake.script("ocr", Reply(200, b"garbage " + SECRET.encode()))
        got, _ = client(fake).read(PNG, model="mistral-ocr-4-1", seconds=10)
    with pytest.raises(BadReply) as bad:
        read(got)
    raised.append(bad.value)
    with pytest.raises(BadReply) as bad_blocks:
        read(page(SECRET, [block("text", SECRET, (0, 0, float("inf"), 10))]))
    raised.append(bad_blocks.value)
    for error_raised in raised:
        assert error_raised.__cause__ is None and error_raised.__suppress_context__
        for shown in (str(error_raised), repr(error_raised)):
            assert KEY not in shown and SECRET not in shown
    assert KEY not in caplog.text and SECRET not in caplog.text
    assert "401" in caplog.text and "500" in caplog.text
    assert "invalid_request_error" in caplog.text and "3310" in caplog.text


# page_read.


def test_blocks_become_lines_in_order() -> None:
    blocks = [
        block("header", "Page 1 of 2", (0, 0, 2000, 40)),
        block("title", "# Invoice 12", (100, 60, 900, 120)),
        block("text", "Seller: Asha\nBuyer: Ravi", (100, 140, 900, 260)),
        block("list", "- Rice\n- Dal", (100, 280, 900, 400)),
        block("signature", "Asha", (1500, 800, 1900, 900)),
        block("footer", "Thank you", (0, 960, 2000, 1000)),
    ]
    markdown = "\n\n".join(b["content"] for b in blocks)
    result = read(page(markdown, blocks))
    assert texts(result) == [
        "Page 1 of 2",
        "Invoice 12",
        "Seller: Asha",
        "Buyer: Ravi",
        "Rice",
        "Dal",
        "Asha",
        "Thank you",
    ]
    assert [line.kind for line in result.lines] == [
        "header",
        "title",
        "paragraph",
        "paragraph",
        "list_item",
        "list_item",
        "signature",
        "footer",
    ]
    assert [line.block for line in result.lines] == [0, 1, 2, 2, 3, 3, 4, 5]
    assert all(line.confidence is None for line in result.lines)
    assert result.page_no == 3
    assert result.read_by == "mistral/mistral-ocr-4-1"
    assert (result.read_thinking, result.read_resolution, result.read_pass) == ("none", "page", "first")
    assert (result.handwritten, result.scripts, result.input_tokens, result.output_tokens) == (
        False,
        (),
        0,
        0,
    )
    assert not result.cached


def test_boxes_scaled_by_reply_dimensions() -> None:
    result = read(page("A\nB", [block("text", "A\nB", (200, 100, 1000, 300))]))
    for line in result.lines:
        assert (line.x0, line.y0, line.x1, line.y1) == (100, 50, 500, 150)
    edge = read(page("A", [block("text", "A", (0, 0, 2000, 1000))]))
    assert (edge.lines[0].x0, edge.lines[0].y0, edge.lines[0].x1, edge.lines[0].y1) == (0, 0, WIDTH, HEIGHT)


def test_boxes_clamped_to_the_page_and_a_flat_box_is_no_box() -> None:
    """Review B3: a box past the page is clamped to it; one with no area left is no box (the
    line is unpinned, on the whole page). Never a BadReply."""
    past = read(page("A\nB", [block("text", "A\nB", (-4, 0, 2001, 1001))]))  # 1 px past
    assert [box(line) for line in past.lines] == [(0, 0, WIDTH, HEIGHT)] * 2
    for flat in [(100, 200, 900, 200), (900, 0, 100, 10), (2100, 0, 2200, 10)]:  # no height, no width, off
        for content in ("A", "A\nB"):
            result = read(page(content, [block("text", content, flat)]), [(0.0, 0.0, 10.0, 10.0)])
            assert [box(line) for line in result.lines] == [(0, 0, WIDTH, HEIGHT)] * len(result.lines)
            assert {line.box_source for line in result.lines} == {"model"}
            assert "unpinned" in result.issues


def test_a_one_line_block_pins_and_a_block_with_fewer_rows_than_lines_does_not() -> None:
    # Changed on purpose (PRD 9 F-11): a block of two lines is pinned now when the found rows
    # inside it are two; with one found row it still is not (here), so its lines stay "model".
    blocks = [
        block("text", "Total 500", (200, 100, 1000, 140)),  # one line: (100, 50, 500, 70) ours
        block("text", "Line one\nLine two", (200, 200, 1000, 300)),  # two lines, one found row
        {"type": "text", "content": "No box here"},
    ]
    found = [(110.0, 52.0, 300.0, 68.0), (100.0, 100.0, 500.0, 150.0)]
    result = read(page("Total 500\n\nLine one\nLine two\n\nNo box here", blocks), found)
    total, one, two, nobox = result.lines
    assert total.box_source == "found"
    assert (total.x0, total.y0, total.x1, total.y1) == found[0]
    for line in (one, two):
        assert line.box_source == "model"
        assert (line.x0, line.y0, line.x1, line.y1) == (100, 100, 500, 150)
    assert nobox.box_source == "model"
    assert (nobox.x0, nobox.y0, nobox.x1, nobox.y1) == (0, 0, WIDTH, HEIGHT)
    assert "unpinned" in result.issues
    assert result.unpinned_lines == 3


def test_a_multi_line_blocks_lines_take_the_found_rows_inside_it() -> None:
    """PRD 9 F-11: a table block (a challan's rows) whose found rows match its lines one for
    one: each line gets its row, the union of the row's boxes clipped to the block, as "found".
    A found box whose centre is outside the block is not the block's."""
    rows = "| Name | Asha |\n| --- | --- |\n| Amount | 500 |\n| Date | 1 Nov |"
    blocks = [block("table", rows, (200, 200, 1800, 500))]  # (100, 100, 900, 250) ours
    found = [
        (110.0, 105.0, 300.0, 135.0),  # row 1: two cells
        (500.0, 108.0, 950.0, 132.0),  # past the block's right edge: clipped to 900
        (110.0, 155.0, 300.0, 185.0),  # row 2
        (500.0, 157.0, 700.0, 183.0),
        (110.0, 205.0, 700.0, 235.0),  # row 3: one box
        (110.0, 300.0, 700.0, 330.0),  # below the block: not its row
    ]
    result = read(page(rows, blocks), found)
    assert texts(result) == ["Name  Asha", "Amount  500", "Date  1 Nov"]
    assert [line.box_source for line in result.lines] == ["found"] * 3
    assert [box(line) for line in result.lines] == [
        (110, 105, 900, 135),
        (110, 155, 700, 185),
        (110, 205, 700, 235),
    ]
    assert result.issues == ()
    assert result.unpinned_lines == 0


@pytest.mark.parametrize(
    "found",
    [
        [(110.0, 105.0, 800.0, 135.0)],  # one row for two lines
        [(110.0, 105.0, 800.0, 125.0), (110.0, 160.0, 800.0, 180.0), (110.0, 210.0, 800.0, 240.0)],  # three
        [],
    ],
)
def test_a_multi_line_block_whose_rows_do_not_match_its_lines_stays_unpinned(
    found: list[tuple[float, float, float, float]],
) -> None:
    """PRD 9 F-11: never a guess. Rows that don't match the lines one for one pin none of them."""
    content = "Seller: Asha\nBuyer: Ravi"
    result = read(page(content, [block("text", content, (200, 200, 1800, 500))]), found)
    assert [line.box_source for line in result.lines] == ["model", "model"]
    assert [box(line) for line in result.lines] == [(100, 100, 900, 250)] * 2
    assert result.issues == ("unpinned",)


@pytest.mark.parametrize(
    ("content", "bottom", "found"),
    [
        # Row 1's cell wraps to a second line (its own found box) and row 3 is too faint to be
        # found: three rows for three lines, but not one a line (review of #676).
        ("Name  Asha\nAmount  500\nDate  1 Nov", 400.0,
         [(110.0, 105.0, 300.0, 125.0), (500.0, 105.0, 880.0, 125.0), (500.0, 130.0, 880.0, 150.0),
          (110.0, 200.0, 300.0, 220.0), (500.0, 200.0, 880.0, 220.0)]),
        # Two lines, the second missed, a stamp found near the bottom.
        ("Name  Asha\nAmount  500", 250.0, [(110.0, 105.0, 800.0, 125.0), (600.0, 200.0, 850.0, 245.0)]),
        # Two lines, the first wrapped onto a second found line, the second missed.
        ("Name  Asha\nAmount  500", 250.0, [(110.0, 105.0, 800.0, 125.0), (110.0, 130.0, 800.0, 150.0)]),
        # Rows 60 apart filling the block: row 1 wraps, row 2 is missed, row 3 is found.
        ("Name  Asha\nAmount  500\nDate  1 Nov", 250.0,
         [(110.0, 105.0, 800.0, 125.0), (500.0, 128.0, 800.0, 148.0), (110.0, 225.0, 800.0, 245.0)]),
    ],
)  # fmt: skip
def test_rows_that_match_the_count_but_not_the_layout_pin_no_line(
    content: str, bottom: float, found: list[tuple[float, float, float, float]]
) -> None:
    """PRD 9 F-11, CLAUDE.md rule 6: as many rows as lines is not enough; rows not evenly
    spaced over the block (a wrapped cell, a missed row, a stamp) pin no line of it."""
    ours = (100.0, 100.0, 900.0, bottom)
    assert len(mistral_reader._rows(ours, found)) == len(content.split("\n"))  # the count alone would pin
    result = read(page(content, [block("text", content, (200, 200, 1800, bottom * 2))]), found)
    assert [line.box_source for line in result.lines] == ["model"] * len(result.lines)
    assert result.issues == ("unpinned",)


def test_a_single_line_block_with_no_match_is_unpinned() -> None:
    result = read(page("Total", [block("text", "Total", (200, 100, 1000, 140))]), [])
    assert result.lines[0].box_source == "model"
    assert result.issues == ("unpinned",)


def test_no_blocks_all_unpinned() -> None:
    value = page("Seller: Asha\nBuyer: Ravi")
    result = page_read(
        value, page_no=1, model=OCR_3, found=[(0.0, 0.0, 999.0, 499.0)], width=WIDTH, height=HEIGHT
    )
    assert texts(result) == ["Seller: Asha", "Buyer: Ravi"]
    for line in result.lines:
        assert line.box_source == "model"
        assert (line.x0, line.y0, line.x1, line.y1) == (0, 0, WIDTH, HEIGHT)
        assert line.block is None
        assert line.kind == "paragraph"
    assert result.issues == ("unpinned",)
    assert result.read_by == "mistral/mistral-ocr-2512"
    assert read(page("Seller", [])).lines[0].box_source == "model"  # an empty list is no blocks too


def test_a_blank_page_is_no_text() -> None:
    result = read(page("![img-0.jpeg](img-0.jpeg)\n\n  \n", average=None))
    assert result.lines == ()
    assert result.issues == ("no_text",)
    assert result.quality is None


def test_markdown_cleanup_keeps_numbers_and_minus() -> None:
    markdown = "\n".join(
        [
            "## Total",
            "**Amount:** -500",
            "1. First item",
            "- bullet one",
            "  * star",
            "+ plus",
            "\\# not a heading",
            "__Name__ ______",
            "-500",
            "+91 98",
            "Rs\\. 1,200 \\*",
            "Sign: **",
        ]
    )
    assert texts(read(page(markdown))) == [
        "Total",
        "Amount: -500",
        "1. First item",
        "bullet one",
        "star",
        "plus",
        "# not a heading",
        "Name ______",
        "-500",
        "+91 98",
        "Rs. 1,200 *",
        "Sign: **",
    ]


def test_image_and_table_placeholders_dropped() -> None:
    markdown = "![img-0.jpeg](img-0.jpeg)\n[tbl-0.md](tbl-0.md)\nText ![img-1.jpeg](img-1.jpeg)\n[a link](x)"
    assert texts(read(page(markdown))) == ["Text", "[a link](x)"]


def test_table_rows_become_lines() -> None:
    table = "| Item | Qty | Rate |\n|---|:---:|---:|\n| Rice | 2 | -50 |\n| A \\| B |  | 1 |"
    for value in (page(table), page(table, [block("table", table, (0, 0, 2000, 400))])):
        result = read(value)
        assert texts(result) == ["Item  Qty  Rate", "Rice  2  -50", "A | B  1"]
        assert {line.kind for line in result.lines} == {"paragraph"}


def test_unsure_by_start_index_devanagari() -> None:
    # The markdown holds क़ as one code point (U+0958); the word, decomposed (U+0915 U+093C).
    first, second = "क़ुल राशि", "क़ुल 500"
    markdown = f"{first}\n{second}"
    decomposed = "क़ुल"
    words = [
        word(decomposed, 0.5, len(first) + 1),  # the second line's word, though both hold it
        word("500", 0.99, len(first) + 5),  # sure: never marked
    ]
    result = read(page(markdown, words=words))
    nfc = unicodedata.normalize("NFC", decomposed)
    assert texts(result) == [unicodedata.normalize("NFC", first), unicodedata.normalize("NFC", second)]
    assert [line.unsure for line in result.lines] == [(), (nfc,)]


def test_unsure_by_start_index_inside_blocks() -> None:
    blocks = [block("text", "Total 500", (0, 0, 2000, 100)), block("text", "Total 700", (0, 100, 2000, 200))]
    markdown = "# Heading\n\nTotal 500\n\nTotal 700"
    result = read(page(markdown, blocks, words=[word("Total", 0.3, markdown.index("Total 700"))]))
    assert [line.unsure for line in result.lines] == [(), ("Total",)]


def test_unsure_word_with_no_place_marks_every_line_holding_it() -> None:
    """Review B9: an unsure word its start_index does not place is unsure on every line that
    holds it, not only the first."""
    markdown = "कुल राशि\nकुल 500\nबाकी"
    for start in (None, 999, markdown.index("बाकी")):
        result = read(page(markdown, words=[word("कुल", 0.4, start)]))
        assert [line.unsure for line in result.lines] == [("कुल",), ("कुल",), ()]


def test_a_fault_in_our_own_reading_is_never_a_bad_reply() -> None:
    """Review B2: only a reply whose shape is wrong is a BadReply; our own code failing on a
    good one is a bug and goes up as it is."""
    with mock.patch.object(mistral_reader, "_clean", side_effect=TypeError("ours")), pytest.raises(TypeError):
        read(page("Total"))


def test_unsure_word_on_no_line_marks_the_nearest_line_whole() -> None:
    """Lead decision (2 Nov 2026): an unsure word placed neither by start_index nor by text
    goes to the line nearest its start_index, whole, so that whole line goes to review."""
    markdown = "Seller: Asha\nBuyer: Ravi\n\nTotal 500"
    words = [
        word("Asha", 0.4, 999),  # a wrong start: falls back to the first line holding it
        word("Ghost", 0.4, markdown.index("Ravi")),  # in no line: the line its start is in
        word("Gone", 0.4, markdown.index("Total") - 1),  # in no line, start between lines: nearest
        word("Lost", 0.4, None),  # in no line, no start: the first line
    ]
    result = read(page(markdown, words=words))
    assert [line.unsure for line in result.lines] == [
        ("Asha", "Seller: Asha"),
        ("Buyer: Ravi",),
        ("Total 500",),
    ]
    assert result.issues == ("unpinned",)  # no blocks; no issue of its own for the words
    assert all(entry in line.text for line in result.lines for entry in line.unsure)


@pytest.mark.parametrize(
    ("average", "quality"),
    [(0.99, "good"), (0.92, "good"), (0.919, "fair"), (0.80, "fair"), (0.79, "poor"), (None, None)],
)
def test_quality_bands(average: float | None, quality: str | None) -> None:
    assert read(page("Total", average=average)).quality == quality


def test_unicode_limits_and_controls() -> None:
    result = read(page("Total‮ 500\x07"))
    assert texts(result) == ["Total 500"]
    for bad in (page("A\ud800"), page("A" * (MAX_BLOCK_CHARS + 1)), page("A\n" * (MAX_BLOCKS_PER_PAGE + 1))):
        with pytest.raises(BadReply):
            read(bad)


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        {"markdown": 5},
        {"markdown": "A", "blocks": "A"},
        {"markdown": "A", "blocks": ["A"]},
        {"markdown": "A", "blocks": [{"type": "text", "content": 5}]},
        {"markdown": "A", "blocks": [{"type": 5, "content": "A"}]},
        {"markdown": "A", "blocks": [block("text", "A", (0, 0, 10, 10))]},  # no dimensions
        {
            "markdown": "A",
            "dimensions": dict(DIMS),
            "blocks": [{"type": "text", "content": "A", "top_left_x": 0}],
        },
        {"markdown": "A", "dimensions": {"width": 10}, "blocks": [block("text", "A", (0, 0, 1, 1))]},
        {
            "markdown": "A",
            "dimensions": {"width": True, "height": 10},
            "blocks": [block("text", "A", (0, 0, 1, 1))],
        },
        {"markdown": "A", "confidence_scores": {"word_confidence_scores": "A"}},
        {"markdown": "A", "confidence_scores": {"word_confidence_scores": ["A"]}},
        {"markdown": "A", "confidence_scores": {"word_confidence_scores": [{"confidence": 0.1}]}},
        {
            "markdown": "A",
            "dimensions": {"width": 0, "height": 10},
            "blocks": [block("text", "A", (0, 0, 1, 1))],
        },
        {
            "markdown": "A",
            "dimensions": dict(DIMS),
            "blocks": [{"type": "text", "content": "A", "top_left_x": "0"}],
        },
        {"markdown": "A", "dimensions": dict(DIMS), "blocks": [block("text", "A", (0, 0, float("nan"), 10))]},
        {"markdown": "A", "confidence_scores": [1]},
        {"markdown": "A", "confidence_scores": {"average_page_confidence_score": "high"}},
        {"markdown": "A", "confidence_scores": {"word_confidence_scores": [word("A", 0.1, -1)]}},
        {"markdown": "A", "confidence_scores": {"word_confidence_scores": [word("A", 0.1, True)]}},
        {
            "markdown": "A",
            "confidence_scores": {"word_confidence_scores": [{"text": "A", "confidence": "0.1"}]},
        },
        {"markdown": "A", "confidence_scores": {"word_confidence_scores": [{"text": 5, "confidence": 0.1}]}},
    ],
)
def test_bad_shape_raises_bad_reply(value: Any) -> None:
    with pytest.raises(BadReply):
        read(value)


def test_trimmed_reads_the_same_and_keeps_only_what_page_read_uses() -> None:
    markdown = "Seller: Asha\n\nTotal 500"
    blocks = [
        dict(block("text", "Seller: Asha", (0, 0, 2000, 100)), id="b1", extra=SECRET),
        block("text", "Total 500", (0, 100, 2000, 200)),
    ]
    words = [word("Asha", 0.4, 8), word("Total", 0.99, 14), word("500", 0.5, 20)]
    value = dict(page(markdown, blocks, words=words), images=[{"id": "img-0", "image_base64": SECRET}])
    kept = trimmed(value)
    assert read(kept) == read(value)
    assert SECRET not in str(kept)
    assert [w["text"] for w in kept["confidence_scores"]["word_confidence_scores"]] == ["Asha", "500"]
    no_scores = page("Total", average=None)
    assert read(trimmed(no_scores)) == read(no_scores)
