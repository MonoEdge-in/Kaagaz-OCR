"""``cli measure-compact`` (#488, Tushar, 4 Nov 2026): the offline proof of the compact reply over
the cached g3 page reads of the last N days, every organisation's (each read as itself): each is
encoded to the compact ``r`` and decoded back (builder A's ``encode_compact``/``decode_compact``,
a fake pair here), compared ignoring order and the block numbers (blocks as groupings), and the
pages, the lossless share, the characters and an output token estimate are printed. Numbers only:
never a word of a page. No AI call: the command has no model. On a fresh database, so the pages
counted are this test's alone."""

import io
import json
import re
from typing import Any

import pytest
from sqlalchemy import text

from kaagaz import cli
from kaagaz.db.engine import Database
from tests import test_migration_0002_db as base
from tests.db_support import insert_document, migrate, new_customer
from tests.test_migration_0023_db import as_owner

pytestmark = pytest.mark.db
fresh = base.fresh
SECRET = "SECRET-WORD-123"  # a page's words: never printed
_PUT = text(
    "INSERT INTO page_reads (customer_id, document_id, page_no, image_sha256, model, prompt_version, result,"
    " input_tokens, output_tokens) VALUES (:c, :d, :p, :sha, 'google/x', :v, CAST(:r AS jsonb), 2500, :out)"
)


def _line(order: int, text_: str, block: int, *, unsure: list[str] | None = None) -> dict[str, Any]:
    return {
        "order": order,
        "kind": "paragraph",
        "text": text_,
        "box_2d": [10 * order, 20, 10 * order + 8, 900],
        "unsure": unsure or [],
        "continues_on_next_page": False,
        "block": block,
        "table": None,
    }


def _result(*lines: dict[str, Any]) -> dict[str, Any]:
    page = {"quality": "good", "handwritten": False, "scripts": ["latin", "devanagari"], "issues": []}
    return {"page": page, "lines": list(lines)}


def _encode(result: dict[str, Any]) -> str:
    """The fake compact reply: the lines in reverse, block numbers changed, no order."""
    lines = [
        {key: value for key, value in line.items() if key != "order"} | {"block": 100 - line["block"]}
        for line in reversed(result["lines"])
    ]
    return json.dumps({"p": result["page"], "l": lines}, separators=(",", ":"))


def _decode(compact: str) -> dict[str, Any]:
    """Back to the reply's shape; a line with "LOSE" in it loses its unsure words."""
    raw = json.loads(compact)
    lines = [
        {**line, "order": n, "unsure": [] if "LOSE" in line["text"] else line["unsure"]}
        for n, line in enumerate(raw["l"], 1)
    ]
    return {"page": raw["p"], "lines": lines}


def _chars(result: dict[str, Any]) -> int:
    return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def test_it_prints_the_lossless_share_characters_and_tokens_and_never_a_word(
    fresh: tuple[str, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, db = fresh
    migrate(url, "head")
    monkeypatch.setattr(cli, "encode_compact", _encode)
    monkeypatch.setattr(cli, "decode_compact", _decode)
    pages = {
        "a": _result(_line(1, SECRET, 1), _line(2, "दुसरी ओळ", 1), _line(3, "Third", 2)),
        "b": _result(_line(1, "LOSE this", 1, unsure=["this"])),  # not lossless
        "c": _result(_line(1, "One line", 1)),
    }
    first, second = new_customer(db)[0], new_customer(db)[0]
    with db.customer(first) as conn:
        doc = insert_document(conn, first)
        for n, (key, version) in enumerate((("a", "g3"), ("b", "g3+minimal")), 1):
            conn.execute(
                _PUT,
                {
                    "c": first,
                    "d": doc,
                    "p": n,
                    "sha": "a" * 64,
                    "v": version,
                    "r": json.dumps(pages[key]),
                    "out": 800,
                },
            )
        for n, version in ((3, "c1"), (4, "g2")):  # never counted: not a g3 read
            conn.execute(
                _PUT,
                {
                    "c": first,
                    "d": doc,
                    "p": n,
                    "sha": "a" * 64,
                    "v": version,
                    "r": json.dumps(pages["a"]),
                    "out": 1,
                },
            )
        conn.execute(  # a g3 read older than the days asked for
            _PUT,
            {"c": first, "d": doc, "p": 5, "sha": "a" * 64, "v": "g3", "r": json.dumps(pages["a"]), "out": 1},
        )
    as_owner(db, "UPDATE page_reads SET created_at = now() - interval '3 days' WHERE page_no = 5")
    with db.customer(second) as conn:
        other = insert_document(conn, second)
        conn.execute(
            _PUT,
            {
                "c": second,
                "d": other,
                "p": 1,
                "sha": "b" * 64,
                "v": "g3",
                "r": json.dumps(pages["c"]),
                "out": 400,
            },
        )

    out = io.StringIO()
    assert cli.main(["measure-compact", "--days", "2"], db=db, out=out) == 0
    printed = out.getvalue()
    assert (
        SECRET not in printed
        and "दुसरी" not in printed
        and "LOSE" not in printed
        and "One line" not in printed
    )
    original = [_chars(pages[key]) for key in ("a", "b", "c")]
    compact = [len(_encode(pages[key])) for key in ("a", "b", "c")]
    ratio = (800 + 800 + 400) / sum(original)
    assert printed.splitlines() == [
        "measure-compact: the cached g3 page reads of the last 2 days, numbers only",
        "pages 3",
        "lossless 66.7% (2 of 3)",
        f"characters a page: original {sum(original) / 3:.0f} average, {max(original)} p95;"
        f" compact {sum(compact) / 3:.0f} average, {max(compact)} p95"
        f" ({sum(compact) / sum(original):.0%} of the original)",
        f"output tokens a page, at the cache's own {ratio:.2f} a character: original"
        f" {sum(original) / 3 * ratio:.0f}, compact {sum(compact) / 3 * ratio:.0f}",
    ]
    assert not re.search(r"[a-f0-9]{8}-[a-f0-9]{4}", printed)  # no ids either


def test_with_no_page_and_a_bad_day_count(
    fresh: tuple[str, Database], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, db = fresh
    migrate(url, "head")
    monkeypatch.setattr(cli, "encode_compact", _encode)
    monkeypatch.setattr(cli, "decode_compact", _decode)
    out = io.StringIO()
    assert cli.main(["measure-compact"], db=db, out=out) == 0
    assert out.getvalue().splitlines() == [
        "measure-compact: the cached g3 page reads of the last 30 days, numbers only",
        "pages 0",
    ]
    assert cli.main(["measure-compact", "--days", "0"], db=db, out=io.StringIO()) == 2


def test_a_page_the_pair_cannot_encode_or_decode_is_counted_not_lossless() -> None:
    page = _result(_line(1, "x", 1))

    def broken(_: object) -> str:
        raise ValueError("cannot")

    assert cli.compact_round_trip(page, broken, _decode) == (False, 0)
    assert cli.compact_round_trip(page, _encode, _decode) == (True, len(_encode(page)))
    moved = _result(_line(1, "x", 1), _line(2, "y", 1))
    split = _result(_line(1, "x", 1), _line(2, "y", 2))  # the same lines, in two blocks
    assert not cli.same_reply(moved, split) and cli.same_reply(moved, _decode(_encode(moved)))
