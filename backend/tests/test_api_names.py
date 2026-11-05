"""The name rules for folders, tags and a document's new name (``kaagaz.api.names``): pure."""

import pytest

from kaagaz.api.names import clean_document_name, clean_name, org_name_ok


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


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("GST 2026/27", "GST 2026/27"),
        ("scans/2026/bill.pdf", "scans/2026/bill.pdf"),  # unlike an upload's name, the path stays
        ("  Sharma invoice.pdf\n", "Sharma invoice.pdf"),
        ("PAN ABCDE1234F.pdf", "PAN ABCDE1234F.pdf"),  # identity numbers are kept as typed
        ("a" * 255, "a" * 255),
        ("a" * 256, None),
        ("\t", None),
        ("", None),
    ],
)
def test_a_new_document_name_is_1_to_255_characters_with_no_path_strip(
    given: str, expected: str | None
) -> None:
    assert clean_document_name(given) == expected


# Production QA S5: a typed name follows the folder upload's rule for characters that can hide
# or reorder what is shown (``upload_name``), so "evil\u202etxt.pdf" can't pass as "evilfdp.txt".
HIDDEN = ["evil\u202etxt.pdf", "a\u200bb", "a\ufeffb", "a\u2066b", "a\u2028b", "a\ue000b", "a\x85b"]


@pytest.mark.parametrize("given", HIDDEN)
def test_a_typed_name_with_a_hidden_or_reordering_character_is_refused(given: str) -> None:
    assert clean_name(given, max_length=100) is None
    assert clean_document_name(given) is None


@pytest.mark.parametrize("given", ["क्‍ष", "र‌व", "GST 2026/27", "Bill \U0001f9fe.pdf"])
def test_a_typed_name_keeps_zwj_zwnj_slashes_and_emoji(given: str) -> None:
    assert clean_name(given, max_length=100) == given
    assert clean_document_name(given) == given


# UI audit G12 (16 Oct 2026): "asd" once became an admin screen's title.
@pytest.mark.parametrize("name", ["", "a", " b ", "asd", "ASD", "Test", "test  org", "aaa", "zz", "N/A"])
def test_a_placeholder_or_too_short_organisation_name_is_refused(name: str) -> None:
    assert not org_name_ok(name)


@pytest.mark.parametrize("name", ["Sharma Traders", "AB", "Test Prep Academy", "ACME", "मेहता टेक्सटाइल्स"])
def test_a_real_organisation_name_is_kept(name: str) -> None:
    assert org_name_ok(name)
