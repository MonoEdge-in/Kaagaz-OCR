"""Only plain text reaches an answer (kaagaz/answering/sanitize.py)."""

import pytest

from kaagaz.answering.sanitize import SNIPPET_MAX, clean_snippet, clean_text, strip_ignorable


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Grand Total: Rs. 1,20,000/-", "Grand Total: Rs. 1,20,000/-"),
        ("Pay at https://evil.example/pay?x=1 today", "Pay at today"),
        ("See www.evil.example now", "See now"),
        ("ftp://files.example/a.txt is here", "is here"),
        ("Click [our portal](https://evil.example) now", "Click our portal now"),
        ("Logo ![logo](https://evil.example/x.png) here", "Logo here"),
        ("<b>Total</b> <script>alert(1)</script>due", "Total alert(1)due"),
        ('<img src="x" onerror="alert(1)">Amount', "Amount"),
        ("Run javascript:alert(1) now", "Run now"),
        ("Open data:text/html;base64,AAAA now", "Open now"),
        ("Tab\there\nnew line", "Tab here new line"),
        ("Bell\x07 and null\x00 gone", "Bell and null gone"),
        ("Right‮to left⁦ marks", "Rightto left marks"),
        ("   spaced    out   ", "spaced out"),
        ("Total ........ 500", "Total 500"),  # dot leaders: four or more of . _ · …
        ("Name ______ Date ··········· x ………… y", "Name Date x y"),
        ("Rs. 1,20,000... and ___ stay", "Rs. 1,20,000... and ___ stay"),
    ],
)
def test_strips_links_images_markup_and_controls(raw: str, clean: str) -> None:
    assert clean_text(raw) == clean


def test_snippet_is_cut_to_the_limit_with_an_ellipsis() -> None:
    snippet = clean_snippet("word " * 200)
    assert len(snippet) <= SNIPPET_MAX
    assert snippet.endswith("…")


def test_short_snippet_is_kept_whole_and_cleaned() -> None:
    assert clean_snippet("total_amount: <i>1,20,000</i>") == "total_amount: 1,20,000"
    assert SNIPPET_MAX == 300


def test_strip_ignorable_drops_every_default_ignorable_code_point() -> None:
    """Unicode's Default_Ignorable_Code_Point: category Cf, plus the marks listed here."""
    ignorable = [
        "\u00ad", "\u200b", "\u200d", "\u2060", "\ufeff", "\u034f", "\u115f", "\u1160", "\u17b4",
        "\u17b5", "\u180b", "\u180f", "\u3164", "\ufe00", "\ufe0f", "\uffa0", "\U000e0100", "\U000e01ef",
    ]  # fmt: skip
    assert strip_ignorable("1" + "".join(ignorable) + "8") == "18"
    shown = "\u0915\u094d\u0937 \u20b91,18,000 \u2013 \u201cx\u201d a\u0307 \u2800"
    assert strip_ignorable(shown) == shown  # visible marks and letters stay
