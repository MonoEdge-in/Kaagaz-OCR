"""The name rule for folders and tags (``kaagaz.api.names``): pure, no database."""

import pytest

from kaagaz.api.names import clean_name


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("GST", "GST"),
        ("  Suppliers  ", "Suppliers"),
        ("GST 2026/27", "GST 2026/27"),  # no path strip: a slash is just a character
        ("tab\tand\nnew\x7fline", "tabandnewline"),
        ("\x00\x1f", None),
        ("", None),
        ("   ", None),
    ],
)
def test_control_characters_go_and_the_ends_are_trimmed(given: str, expected: str | None) -> None:
    assert clean_name(given, max_length=100) == expected


def test_a_name_over_the_limit_is_refused_not_cut() -> None:
    assert clean_name("a" * 40, max_length=40) == "a" * 40
    assert clean_name("a" * 41, max_length=40) is None
    assert clean_name("  " + "a" * 40 + "  ", max_length=40) == "a" * 40  # counted after the trim
