"""Only plain text reaches an answer (kaagaz/answering/sanitize.py)."""

import pytest

from kaagaz.answering.sanitize import SNIPPET_MAX, clean_snippet, clean_text


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
