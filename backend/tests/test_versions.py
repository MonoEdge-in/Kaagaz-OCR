"""File management (Phase 2 PRD 1) stage 2, versions, without a database: the fingerprint (V1),
the name key (V2), the scoring table with the PRD's eight fixtures (V4), the exact overlap and
two invoices of one template (#521), the banner's words (V6, NB-17), compare (V11), the version
list's row (V12) and the one-time fingerprint sweep's worker wrapper. The SQL: test_versions_db.py.
"""

import random
import struct
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from kaagaz import versions, worker_main
from kaagaz.api.views import document_version
from kaagaz.db import versions as versions_sql
from kaagaz.db.engine import Database
from kaagaz.db.versions import view
from kaagaz.search.story_match import trigrams
from kaagaz.worker_main import FINGERPRINT_PASS, FingerprintOld

_ENGLISH = (  # noqa: SIM905 - a word list reads best as words
    "the tenant shall pay rent of rupees every month to landlord on or before fifth day agreement"
    " period eleven months deposit refundable notice premises flat society maintenance electricity water"
    " charges repairs keys witness signed party first second sub letting prohibited terms renewal mutual"
).split()
_MARATHI = (  # noqa: SIM905 - the same
    "गाव तालुका जिल्हा भूमापन क्रमांक उपविभाग भोगवटादार वर्ग क्षेत्र हेक्टर आर आकारणी रुपये पैसे"
    " इतर अधिकार कुळाचे नाव खाते क्रमांक फेरफार नोंद पीक पाहणी जलसिंचन जमीन महसूल"
).split()


def _text(seed: int, size: int, vocabulary: list[str] = _ENGLISH) -> list[str]:
    pick = random.Random(seed)  # noqa: S311 - a fixed seed makes test text, not a secret
    return [pick.choice(vocabulary) for _ in range(size)]


def _overlap(first: str, second: str) -> float | None:
    a, b = versions.fingerprint(first), versions.fingerprint(second)
    return versions.overlap(None if a is None else a[0], None if b is None else b[0])


def _name_similarity(first: str, second: str) -> float:
    """pg_trgm's similarity() of two name keys: the trigrams of every word, together."""
    a = set().union(*(trigrams(word) for word in versions.name_key(first).split()))
    b = set().union(*(trigrams(word) for word in versions.name_key(second).split()))
    return len(a & b) / len(a | b) if a | b else 0.0


def _exact(first: str, second: str) -> float | None:
    return versions.exact_overlap({1: first}, {1: second})


def _score(
    old: tuple[str, str, str], new: tuple[str, str, str], *, identifier: bool = False
) -> tuple[str | None, float | None, float]:
    """(outcome, text overlap, name similarity) of ``new`` (text, name, type) against ``old``, as
    spotting scores it: the exact overlap when both have a fingerprint (#521). A shared
    identifier counts only for the same type (the related documents rule)."""
    text_overlap = _overlap(old[0], new[0])
    if text_overlap is not None:
        text_overlap = _exact(old[0], new[0])
    name = _name_similarity(old[1], new[1])
    found = versions.outcome(text_overlap, name, identifier and old[2] == new[2])
    return found, text_overlap, name


# --- V1: the fingerprint ------------------------------------------------------------------


def test_v1_normalising_folds_case_digits_and_punctuation() -> None:
    assert versions.words("Survey No. ४४/२, Wakad — “Shinde”!") == [
        "survey",
        "no",
        "44",
        "2",
        "wakad",
        "shinde",
    ]
    assert versions.words("ＡＢＣ  def") == ["abc", "def"]  # noqa: RUF001 - NFKC: full-width letters, a hard space


def test_v1_sixty_four_values_sixteen_bands_and_none_under_twenty_words() -> None:
    words = _text(1, 200)
    found = versions.fingerprint(" ".join(words))
    assert found is not None
    minhash, bands = found
    assert (len(minhash), len(bands)) == (versions.HASHES, versions.BANDS) == (64, 16)
    assert all(-(2**63) <= value < 2**63 for value in [*minhash, *bands])  # each fits a bigint
    assert versions.fingerprint(" ".join(words)) == found  # the same text, the same fingerprint
    assert versions.fingerprint(" ".join(words).upper().replace(" ", ",\n  ")) == found  # layout, case
    assert versions.fingerprint(" ".join(words[:19])) is None  # V4 fixture: a short note
    assert versions.fingerprint(" ".join(words[:20])) is not None


def test_v1_a_spreadsheet_with_one_changed_cell_keeps_almost_every_value() -> None:
    """Section 10: the cell texts, one per line, as db/versions.page_texts joins them."""
    cells = [f"{name} {qty}" for name, qty in zip(_text(2, 150), range(150), strict=True)]
    changed = [*cells[:70], "cement 999", *cells[71:]]
    overlap = _overlap("\n".join(cells), "\n".join(changed))
    assert overlap is not None and overlap >= versions.TEXT_ALONE
    assert versions.outcome(overlap, 1.0, False) == versions.SUGGESTED


# --- V2: the name key -------------------------------------------------------------------


def test_v2_the_name_key_drops_extension_markers_dates_and_punctuation() -> None:
    """The same names test_versions_db.py runs through 0041's NAME_KEY in PostgreSQL."""
    assert versions.name_key("7/12 Survey 44 Wakad v2 (1).pdf") == "7 12 survey 44 wakad"  # 0041's backfill
    assert versions.name_key("Rent Agreement Shinde FINAL copy.docx") == "rent agreement shinde"
    assert versions.name_key("सातबारा ४४ - 12.06.2026.pdf") == "सातबारा 44"
    assert versions.name_key("Invoice_ver 3 – latest.PDF") == "invoice ver 3"  # noqa: RUF001 - an en dash
    assert versions.name_key("Rev. plan “A”.xlsx") == "plan a"
    assert versions.name_key("Version2 draft") == "draft"
    assert versions.name_key("review v 2x") == "review 2x"


# --- V4: the scoring table and the PRD's fixtures --------------------------------------------

_LEASE = _text(10, 400)
_LEASE_NAME = "Rent Agreement Shinde.pdf"


def test_v4_a_one_word_change_with_the_same_name_is_only_suggested_never_linked() -> None:
    """Changed on purpose (#521, Tushar, 6 Nov 2026): nothing is linked on its own any more, not
    even the old auto case (same name, same type and folder, 99% of the text)."""
    long = _text(10, 1200)
    changed = [*long[:600], "fourteenth", *long[601:]]
    old, new = (" ".join(long), _LEASE_NAME, "contract"), (" ".join(changed), _LEASE_NAME, "contract")
    found, overlap, name = _score(old, new)
    assert (found, name) == (versions.SUGGESTED, 1.0) and overlap is not None and round(overlap, 2) == 0.99
    assert not hasattr(versions, "AUTO")


def test_v4_a_renamed_changed_copy_is_suggested() -> None:
    changed = [*_LEASE[:150], "twelve", *_LEASE[151:]]
    found, overlap, name = _score(
        (" ".join(_LEASE), _LEASE_NAME, "contract"),
        (" ".join(changed), "Flat 7 lease signed.pdf", "contract"),
    )
    assert found == versions.SUGGESTED and name < versions.NAME_SIGNAL
    assert overlap is not None and overlap >= versions.TEXT_ALONE


def test_v4_a_rescan_of_the_same_paper_is_suggested() -> None:
    """Other line breaks and letter case, and two words the scanner misread."""
    rescan = list(_LEASE)
    rescan[50], rescan[250] = "tenarit", "agreernent"
    lines = "\n".join(" ".join(rescan[i : i + 9]).title() for i in range(0, len(rescan), 9))
    found, overlap, _ = _score(
        (" ".join(_LEASE), _LEASE_NAME, "contract"), (lines, "scan_0001.pdf", "contract")
    )
    assert found == versions.SUGGESTED and overlap is not None and overlap >= versions.TEXT_ALONE


_SATBARA = _text(20, 300, _MARATHI)
_SATBARA_NAME = "7/12 Survey 44 Wakad.pdf"


def test_v4_next_years_satbara_with_a_mutation_entry_is_suggested() -> None:
    """The worked example: the name hardly matches, most of the text and the survey number do."""
    mutation = _text(21, 35, _MARATHI)
    newer = [*_SATBARA[:150], *mutation, *_SATBARA[150:]]
    newer[10] = "२०२६"
    found, overlap, name = _score(
        (" ".join(_SATBARA), _SATBARA_NAME, "land_record"),
        (" ".join(newer), "Satbara 44.pdf", "land_record"),
        identifier=True,
    )
    assert found == versions.SUGGESTED and name < versions.NAME_SIGNAL
    assert overlap is not None and versions.TEXT_SIGNAL <= overlap < 0.9  # suggested on text alone now
    assert versions.reasons(overlap, name, "survey_number") == "84% the same text"


def test_v4_a_satbara_and_its_sale_deed_are_not_suggested() -> None:
    """They share the survey number, but not the type, and little text: related, never versions."""
    deed = _text(30, 400, _MARATHI + _ENGLISH)
    found, overlap, _ = _score(
        (" ".join(_SATBARA), _SATBARA_NAME, "land_record"),
        (" ".join(deed), "Sale Deed Shinde Survey 44.pdf", "contract"),
        identifier=True,
    )
    assert found is None and overlap is not None and overlap < versions.TEXT_SIGNAL


def test_v4_a_same_template_contract_for_another_party_is_not_suggested() -> None:
    def contract(party: str, rent: str) -> str:
        words = list(_LEASE)
        for place in range(0, len(words), 8):
            words[place] = party
        words[3::40] = [rent] * len(words[3::40])
        return " ".join(words)

    found, overlap, name = _score(
        (contract("shinde", "15000"), _LEASE_NAME, "contract"),
        (contract("patil", "18000"), "Rent Agreement Patil.pdf", "contract"),
    )
    assert found is None and overlap is not None and overlap < versions.TEXT_SIGNAL
    assert name == 0.5  # "Rent Agreement" alike, under NAME_SIGNAL: not even one signal


def test_v4_an_invoice_and_its_receipt_are_not_suggested() -> None:
    """They share the invoice number: one identifier, of two types, is never enough."""
    found, _, _ = _score(
        (" ".join(_text(40, 120)), "Invoice 1043.pdf", "invoice"),
        (" ".join(_text(41, 60)), "Receipt 1043.pdf", "receipt"),
        identifier=True,
    )
    assert found is None
    assert versions.outcome(None, None, True) is None  # even one type


def test_v4_a_short_note_gets_no_fingerprint() -> None:
    assert versions.fingerprint("Please pay the rent by Friday. Thanks, Anita") is None
    assert _overlap("Please pay the rent by Friday.", " ".join(_LEASE)) is None


def test_v4_the_table_at_its_edges() -> None:
    """Changed on purpose (#521, Tushar, 6 Nov 2026): 80% of the text alone suggests (was 90%),
    and the old auto row (name 0.8, text 0.95) is only suggested."""
    outcome = versions.outcome
    assert versions.TEXT_ALONE == versions.TEXT_SIGNAL == 0.8
    assert outcome(1.0, 1.0, True) == versions.SUGGESTED  # never anything stronger
    assert outcome(0.95, 0.8, False) == versions.SUGGESTED
    assert outcome(0.8, None, False) == versions.SUGGESTED  # text alone, at the line
    assert outcome(0.79, None, False) is None
    assert outcome(0.79, 1.0, False) is None  # the name is one signal of two
    assert outcome(0.79, 0.6, True) == versions.SUGGESTED  # two of: name and identifier
    assert outcome(None, 0.6, True) == versions.SUGGESTED  # under 20 words: as before
    assert outcome(None, 0.59, True) is None
    assert outcome(None, 1.0, False) is None


def test_521_monthly_bills_name_and_identifier_need_half_the_text_when_both_have_one() -> None:
    """The council's bug fix 2: "Invoice 2025-01" and "Invoice 2025-02" share the name key and an
    account number, but little text; the two-of rule then needs 0.5 of the text too."""
    outcome = versions.outcome
    assert versions.TEXT_FLOOR == 0.5
    assert outcome(0.44, 1.0, True) is None  # two bills of one template (test_521_two_invoices_...)
    assert outcome(0.49, 1.0, True) is None
    assert outcome(0.5, 1.0, True) == versions.SUGGESTED
    assert outcome(0.0, 0.6, True) is None
    assert outcome(None, 0.6, True) == versions.SUGGESTED  # no fingerprint: the rule as before


# --- #521: the exact overlap -----------------------------------------------------------------


def _extended(shared: int, extra: int) -> tuple[str, str]:
    """Two texts of distinct words whose exact overlap is shared / (shared + extra): the second
    is the first with ``extra`` new words added at its end."""
    first = [f"w{n}" for n in range(shared + versions.SHINGLE - 1)]
    return " ".join(first), " ".join([*first, *(f"x{n}" for n in range(extra))])


def test_521_the_exact_overlap_decides_at_80_and_79() -> None:
    at_80, at_79 = _extended(80, 20), _extended(79, 21)
    assert _exact(*at_80) == 0.8 and versions.outcome(_exact(*at_80), None, False) == versions.SUGGESTED
    assert _exact(*at_79) == 0.79 and versions.outcome(_exact(*at_79), None, False) is None
    assert versions.reasons(_exact(*at_80), None, None) == "80% the same text"


def test_521_the_exact_overlap_reads_the_pages_as_the_fingerprint_does() -> None:
    """Normalised words, pages in order and shingles across a page break, as fingerprint()."""
    words = _text(3, 60)
    one = {1: " ".join(words)}
    split = {2: " ".join(words[30:]).upper(), 1: ", ".join(words[:30])}
    assert versions.exact_overlap(one, split) == 1.0
    assert versions.exact_overlap(one, {1: " ".join(words[:19])}) is None  # under MIN_WORDS: no text signal
    assert versions.exact_overlap(one, {1: ""}) is None


def test_521_over_the_compare_limits_there_is_no_exact_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Then spotting keeps the estimate and marks it estimated (test_versions_db.py)."""
    monkeypatch.setattr(versions, "MAX_COMPARE_WORDS", 40)
    monkeypatch.setattr(versions, "MAX_COMPARE_TOTAL", 70)
    page = " ".join(_text(4, 40))
    assert versions.exact_overlap({1: page}, {1: page}) == 1.0
    assert versions.exact_overlap({1: page + " more"}, {1: page}) is None  # one page too long
    assert versions.exact_overlap({1: page, 2: page}, {1: page}) is None  # too many words in all
    assert versions.exact_overlap({1: page}, {1: page, 2: page}) is None


# Two invoices of one template (#521): only the numbers differ.
_ITEMS = [
    ("Cement OPC 53 grade bag", "2523", 50, 380),
    ("TMT bar 12 mm", "7214", 20, 640),
    ("Binding wire kg", "7217", 10, 85),
    ("PVC pipe 4 inch", "3917", 12, 410),
    ("Wall putty 40 kg", "3214", 6, 720),
]


def _invoice(number: str, date: str, items: list[tuple[str, str, int, int]]) -> str:
    lines = " ".join(
        f"{n} {what} {hsn} {qty} {rate} {qty * rate}" for n, (what, hsn, qty, rate) in enumerate(items, 1)
    )
    taxable = sum(qty * rate for _, _, qty, rate in items)
    tax = round(taxable * 0.09)
    return (
        "Tax Invoice Sharma Traders Shop 12 Market Yard Gultekdi Pune 411037 GSTIN 27ABCPS9876K1Z2"
        f" Phone 9822012345 Invoice No {number} Date {date} Bill To Patil Hardware Stores Wakad Pune"
        " GSTIN 27AAACS1234F1Z5 Place of Supply Maharashtra Sr No Description HSN Qty Rate Amount"
        f" {lines} Taxable Value {taxable} CGST 9% {tax} SGST 9% {tax} Total {taxable + 2 * tax}"
        " Amount in words Rupees as above only Bank HDFC Bank Account No 50100123456789 IFSC HDFC0001234"
        " Terms Goods once sold will not be taken back Interest at 18% after 30 days Subject to Pune"
        " jurisdiction For Sharma Traders Authorised Signatory"
    )


def test_521_two_invoices_of_one_template_with_other_numbers_are_not_suggested() -> None:
    """Every number of the bill differs (its number, date, quantities, rates and totals): 44% of
    the 5-word shingles are the same, so the template alone never reaches 80%. GSTIN and PAN
    never link (extraction/links.py), and the invoice numbers differ, so no identifier either."""
    reorder = [(what, hsn, qty - 3, rate + 5) for what, hsn, qty, rate in _ITEMS]
    first = (_invoice("SHT/2026/0412", "03-10-2026", _ITEMS), "Sharma Traders SHT-2026-0412.pdf", "invoice")
    second = (_invoice("SHT/2026/0587", "21-10-2026", reorder), "Sharma Traders SHT-2026-0587.pdf", "invoice")
    found, overlap, name = _score(first, second)
    assert found is None and overlap is not None and round(overlap, 2) == 0.44
    assert name >= versions.NAME_SIGNAL  # one signal of the two needed


def test_521_a_repeat_bill_for_the_same_goods_reaches_90_and_is_suggested() -> None:
    """Reported to Tushar (#521), not tuned: the same goods and quantities, only the bill's number
    and date changed, is 90% the same text (128 of 142 shingles), so it is suggested; it was at
    the old 90% line too. Keep separate answers it once for the pair."""
    first = (_invoice("SHT/2026/0412", "03-10-2026", _ITEMS), "Bill 0412.pdf", "invoice")
    second = (_invoice("SHT/2026/0587", "21-10-2026", _ITEMS), "Bill 0587.pdf", "invoice")
    found, overlap, _ = _score(first, second)
    assert found == versions.SUGGESTED and overlap is not None and round(overlap, 2) == 0.90


# --- V6: the banner's words --------------------------------------------------------------


def test_v6_nb17_the_reason_is_the_share_of_text_else_the_name_and_identifier_in_plain_words() -> None:
    """Changed on purpose (#521, NB-17): the banner's brackets, "(86% the same text)"; a name- or
    identifier-led suggestion says why in words instead."""
    reasons = versions.reasons
    assert reasons(0.86, 0.31, "survey_number") == "86% the same text"
    assert reasons(0.96, 1.0, None) == "96% the same text"
    assert reasons(0.86, None, None, estimated=True) == "about 86% the same text"
    assert reasons(0.5, 1.0, "survey_number") == "same name and same survey number"
    assert reasons(None, 0.7, "agreement_number") == "similar name and same agreement number"
    assert reasons(0.79, 0.2, None) == ""


def test_521_the_share_is_rounded_down_and_100_only_when_counted_exactly_the_same() -> None:
    """The council's Pattern A, with Tushar's change of 6 Nov: the whole percent below (86.9% is
    86%), never 100% for a near match, unless the text was counted exactly the same (a re-scan or
    a re-save: other bytes, the same text); never from the estimate. The overlap comes back from
    the database as a 4-byte real, so 0.83 reads 0.8299999833: still 83%."""
    reasons = versions.reasons
    assert reasons(0.869, None, None) == "86% the same text"
    assert reasons(struct.unpack("f", struct.pack("f", 0.83))[0], None, None) == "83% the same text"
    assert reasons(0.996, None, None) == "99% the same text"
    assert reasons(1.0, None, None) == "100% the same text"
    assert reasons(1.0, None, None, estimated=True) == "about 99% the same text"
    assert reasons(0.828125, None, None, estimated=True) == "about 82% the same text"


def test_v6_a_suggestion_says_which_side_this_document_is() -> None:
    row: dict[str, Any] = {
        "id": "s1",
        "document_id": "d3",
        "document_name": "Satbara 44.pdf",
        "candidate_family_id": "d1",
        "candidate_id": "d1",
        "candidate_name": "7/12 Survey 44 Wakad.pdf",
        "text_overlap": 0.86,
        "text_overlap_exact": True,
        "name_similarity": 0.31,
        "shared_identifier": "survey_number",
        "outcome": "suggested",
        "status": "pending",
        "next_version_no": 2,
    }
    shown = view(row, "d3")
    assert shown["role"] == "new" and view(row, "d1")["role"] == "candidate"
    assert shown["candidate"] == {"family_id": "d1", "id": "d1", "name": "7/12 Survey 44 Wakad.pdf"}
    assert shown["next_version_no"] == 2
    assert (shown["reasons"], shown["text_overlap_estimated"]) == ("86% the same text", False)
    for exact in (False, None):  # None: a suggestion from before 0076, an estimate
        estimated = view({**row, "text_overlap_exact": exact}, "d3")
        assert estimated["reasons"] == "about 86% the same text" and estimated["text_overlap_estimated"]
    no_text = view({**row, "text_overlap": None, "text_overlap_exact": None, "name_similarity": 1.0}, "d3")
    assert no_text["reasons"] == "same name and same survey number"
    assert no_text["text_overlap_estimated"] is False


# --- V11: compare --------------------------------------------------------------------------


def test_v11_compare_gives_runs_of_same_added_and_removed_page_by_page() -> None:
    left = {1: "same first page", 2: "mutation entry none recorded here"}
    right = {1: "same first page", 2: "mutation entry 1234 recorded here by talathi", 3: "new page"}
    found = versions.compare(left, right, max_pages=10)
    assert [page["changes"] for page in found["pages"]] == [0, 2, 1]
    assert found["pages"][1]["runs"] == [
        {"kind": "same", "text": "mutation entry"},
        {"kind": "removed", "text": "none"},
        {"kind": "added", "text": "1234"},
        {"kind": "same", "text": "recorded here"},
        {"kind": "added", "text": "by talathi"},
    ]
    assert found["summary"] == "3 changes on 2 pages"
    assert versions.compare(left, right, max_pages=2)["summary"] == "2 changes on page 2"  # MAX_PAGES
    assert versions.compare(left, left, max_pages=10)["summary"] == "No changes"
    assert versions.compare({1: "a b"}, {1: "a c"}, max_pages=10)["summary"] == "1 change on page 1"
    assert versions.compare(left, right, max_pages=10)["truncated"] is False


def test_521_compare_counts_the_words_changed_and_each_sides_words_page_by_page() -> None:
    found = versions.compare({1: "a b c d", 2: "x"}, {1: "a z z d e", 3: "new page"}, max_pages=10)
    counts = [(page["words_changed"], page["left_words"], page["right_words"]) for page in found["pages"]]
    assert counts == [(3, 4, 5), (1, 1, 0), (2, 0, 2)]  # "b c" for "z z", then "e"; page 2 gone


def test_521_the_overview_says_the_share_the_words_changed_and_the_pages_new_or_gone() -> None:
    """See the differences: the summary line in plain words."""

    def page(no: int, changed: int, left: int = 10, right: int = 10) -> dict[str, int]:
        return {
            "page_no": no,
            "words_changed": changed,
            "left_words": left,
            "right_words": right,
            "changes": 1,
        }

    pages = [page(1, 0), page(2, 30), page(3, 0), page(4, 0), page(5, 8), page(6, 12, left=0, right=12)]
    assert versions.overview(pages, 0.94) == "94% the same. 38 words changed on pages 2 and 5. Page 6 is new."
    assert versions.overview([page(1, 1), page(2, 3, right=0)], 0.869) == (
        "86% the same. 1 word changed on page 1. Page 2 is gone."
    )
    three = [page(2, 1), page(4, 1), page(7, 1)]
    assert versions.overview(three, 0.8).endswith("3 words changed on pages 2, 4 and 7.")
    assert versions.overview(pages, 1.0) == "The text is the same in both files."  # a re-scan or re-save
    assert (
        versions.overview(pages, 0.9, truncated=True)
        == "These files are long, so only the first part is compared."
    )
    assert versions.overview(pages[:2], None) == "30 words changed on page 2."  # under 20 words: no share


def test_v11_compare_caps_words_per_page_and_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions, "MAX_COMPARE_WORDS", 50)
    monkeypatch.setattr(versions, "MAX_COMPARE_TOTAL", 100)
    page = " ".join(["word"] * versions.MAX_COMPARE_WORDS)
    big = dict.fromkeys(range(1, versions.MAX_COMPARE_TOTAL // versions.MAX_COMPARE_WORDS + 2), page)
    found = versions.compare(big, big, max_pages=100)
    assert [p["truncated"] for p in found["pages"]][-2:] == [False, True]  # the total ran out
    assert found["pages"][-1]["runs"] == [] and found["truncated"] is True
    long_page = versions.compare({1: page + " more"}, {1: page}, max_pages=10)
    assert long_page["pages"][0]["truncated"] and long_page["truncated"]


# --- V12: the version list ----------------------------------------------------------------


def test_v12_each_version_says_its_note_uploader_and_how_it_joined() -> None:
    row: dict[str, Any] = {
        "id": "d3",
        "version_no": 2,
        "status": "ready",
        "is_current": True,
        "created_at": datetime(2026, 10, 20, tzinfo=UTC),
        "display_name": "Satbara 44.pdf",
        "version_note": "Mutation 1234 added",
        "uploaded_by": "u1",
        "uploaded_by_name": "Priya",
        "linked": "suggested",
    }
    shown = document_version(row)  # type: ignore[arg-type]
    assert shown["joined"] == "linked" and shown["uploaded_by"] == {"id": "u1", "name": "Priya"}
    assert (shown["version_note"], shown["is_current"]) == ("Mutation 1234 added", True)
    assert document_version({**row, "linked": None})["joined"] == "uploaded"  # type: ignore[arg-type]
    assert document_version({**row, "linked": "auto"})["joined"] == "auto"  # type: ignore[arg-type]


# --- section 7: the one-time sweep ---------------------------------------------------------


class _Idle:
    def run_once(self) -> bool:
        return False


def test_the_fingerprint_sweep_runs_on_idle_turns_until_a_pass_finds_none() -> None:
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    found = [FINGERPRINT_PASS, 3, 0]
    calls: list[int] = []

    def sweep() -> int:
        calls.append(1)
        return found.pop(0)

    wrapper = FingerprintOld(_Idle(), sweep, lambda: now[0])
    assert wrapper.run_once() and wrapper.run_once()  # a full pass: the next idle turn goes on
    assert not wrapper.run_once()  # 3 found: the next waits a minute
    now[0] += timedelta(minutes=1)
    assert wrapper.run_once()  # none found: done for good
    now[0] += timedelta(hours=1)
    assert not wrapper.run_once() and len(calls) == 3


def test_v17_a_pass_with_nothing_due_ends_the_sweep_only_once_nothing_is_pending() -> None:
    """Review (#648): a pass that finds nothing due while an organisation's run is still to do (its
    switch off, or a new file waiting for its fingerprint) waits a minute and tries again; the
    sweep ends for good only once every organisation's run has finished."""
    now = [datetime(2026, 10, 20, tzinfo=UTC)]
    pending = [True]
    calls: list[int] = []

    def sweep() -> int:
        calls.append(1)
        return 0

    wrapper = FingerprintOld(_Idle(), sweep, lambda: now[0], pending=lambda: pending[0])
    assert wrapper.run_once()  # nothing due, a run still to do
    now[0] += timedelta(minutes=1)
    assert wrapper.run_once()  # so it looks again
    pending[0] = False
    now[0] += timedelta(minutes=1)
    assert wrapper.run_once()  # nothing due, nothing pending: done for good
    now[0] += timedelta(hours=1)
    assert not wrapper.run_once() and len(calls) == 3


def test_v17_the_finder_runs_after_the_fingerprints_in_the_same_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#648: a pass that found fingerprints to write does no finding; once none is missing it finds;
    the sweep ends only when neither has anything left."""
    missing, finding = [2, 0, 0], [7, 0]
    monkeypatch.setattr(worker_main, "fingerprint_missing", lambda _db: missing.pop(0))
    no_database = cast(Database, None)  # the two passes are stand-ins: no database is reached
    passes = [worker_main.old_documents(no_database, lambda: finding.pop(0)) for _ in range(3)]
    assert passes == [2, 7, 0]
    assert missing == finding == []


# --- V17 (#648): the version finder's groups -------------------------------------------------


def _strong(text_overlap: float) -> tuple[float, float, bool]:
    return (text_overlap, 0.0, False)


def test_v17_connected_pairs_make_one_group_and_a_lone_file_none() -> None:
    """X~Y and X~Z make one group of three, though Y and Z are not alike; a pair's files are in
    the group once; a file with no pair is in none."""
    pairs = [("x", "y", _strong(0.9)), ("x", "z", _strong(0.83)), ("a", "b", _strong(0.81))]
    assert versions.groups(pairs) == [["a", "b"], ["x", "y", "z"]]
    assert versions.groups([]) == []


def test_v17_a_chain_longer_than_the_cap_is_cut_at_its_weakest_links() -> None:
    """25 files in a chain, each pair weaker than the one before: the 20 strongest-joined make one
    group, the 5 at the weak end another; never a group over MAX_GROUP."""
    files = [f"f{n:02}" for n in range(25)]
    chain = [(files[n], files[n + 1], _strong(0.99 - n / 100)) for n in range(24)]
    found = versions.groups(chain)
    assert [len(group) for group in found] == [20, 5]
    assert found[0] == files[:20] and found[1] == files[20:]
    assert all(len(group) <= versions.MAX_GROUP for group in versions.groups(chain, cap=3))


def test_v17_the_strongest_pair_joins_first_and_the_same_pairs_give_the_same_groups() -> None:
    """With room for 2 files, b joins a (0.95), not c (0.85); in any order of the pairs."""
    pairs = [("b", "c", _strong(0.85)), ("a", "b", _strong(0.95)), ("c", "d", _strong(0.9))]
    assert versions.groups(pairs, cap=2) == [["a", "b"], ["c", "d"]]
    assert versions.groups(list(reversed(pairs)), cap=2) == [["a", "b"], ["c", "d"]]


def test_v6_accept_losing_a_race_to_reject_is_already_decided_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The suggestion still read pending, but a reject (no family locks) decided it first: the
    accept's guarded UPDATE finds no row, so Refused rolls the link back before any write."""
    suggestion = {"id": "s1", "document_id": "n1", "candidate_family_id": "f1", "status": "pending"}
    # Changed on purpose (#594): the state says whom the document was held for too.
    state = {
        "in_trash": False,
        "current_id": "c1",
        "alone": True,
        "status": "ready",
        "from_folder": None,
        "held_for": None,
    }
    replies = {
        versions_sql._SUGGESTION: suggestion,
        versions_sql._LINK_STATE: state,
        versions_sql._ACCEPT: None,
        versions_sql._LOCKED_FAMILY: False,  # Changed on purpose (PRD 7, #605): neither family locked
    }
    ran: list[Any] = []

    class _Result:
        def __init__(self, row: Any) -> None:
            self.row = row

        def mappings(self) -> "_Result":
            return self

        def first(self) -> Any:
            return self.row

        one = first

        def scalar_one(self) -> Any:
            return self.row

    class _Conn:
        def execute(self, statement: Any, _params: Any) -> _Result:
            ran.append(statement)
            return _Result(replies[statement])

    monkeypatch.setattr(versions_sql, "lock_family", lambda *_args: "locked")
    # Changed on purpose (#593, 0085): the link takes the tree lock before the family locks.
    monkeypatch.setattr(versions_sql, "_lock_tree", lambda *_args: None)
    with pytest.raises(versions_sql.Refused) as refused:
        versions_sql.link(_Conn(), "c", "s1", by="u")  # type: ignore[arg-type]
    assert refused.value.code == "already_decided"
    assert ran[-1] is versions_sql._ACCEPT  # nothing written after the lost race
