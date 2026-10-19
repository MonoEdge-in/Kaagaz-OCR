"""File management (Phase 2 PRD 1) stage 2, versions, without a database: the fingerprint (V1),
the name key (V2), the scoring table with the PRD's eight fixtures (V4), the banner's words
(V6), compare (V11), the version list's row (V12) and the one-time fingerprint sweep's worker
wrapper. The SQL: test_versions_db.py.
"""

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from kaagaz import versions
from kaagaz.api.views import document_version
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


def _score(
    old: tuple[str, str, str],
    new: tuple[str, str, str],
    *,
    identifier: bool = False,
    same_folder: bool = True,
) -> tuple[str | None, float | None, float]:
    """(outcome, text overlap, name similarity) of ``new`` (text, name, type) against ``old``.
    A shared identifier counts only for the same type (the related documents rule)."""
    text_overlap = _overlap(old[0], new[0])
    name = _name_similarity(old[1], new[1])
    same_type = old[2] == new[2]
    found = versions.outcome(
        text_overlap, name, identifier and same_type, same_type=same_type, same_folder=same_folder
    )
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
    assert versions.outcome(overlap, 1.0, False, same_type=False, same_folder=True) == versions.SUGGESTED


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


def test_v4_a_one_word_change_with_the_same_name_is_linked_in_the_same_folder() -> None:
    changed = [*_LEASE[:200], "fourteenth", *_LEASE[201:]]
    old, new = (" ".join(_LEASE), _LEASE_NAME, "contract"), (" ".join(changed), _LEASE_NAME, "contract")
    found, overlap, name = _score(old, new)
    assert (found, name) == (versions.AUTO, 1.0) and overlap is not None and overlap >= 0.95
    assert _score(old, new, same_folder=False)[0] == versions.SUGGESTED  # another folder: asked


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
    assert overlap is not None and versions.TEXT_SIGNAL <= overlap < versions.AUTO_TEXT
    assert versions.reasons(overlap, name, "survey_number").endswith("and the survey number matches")


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
    assert versions.outcome(None, None, True, same_type=True, same_folder=True) is None  # even one type


def test_v4_a_short_note_gets_no_fingerprint() -> None:
    assert versions.fingerprint("Please pay the rent by Friday. Thanks, Anita") is None
    assert _overlap("Please pay the rent by Friday.", " ".join(_LEASE)) is None


def test_v4_the_table_at_its_edges() -> None:
    outcome = versions.outcome
    assert outcome(0.95, 0.8, False, same_type=True, same_folder=True) == versions.AUTO
    assert outcome(0.95, 0.8, False, same_type=False, same_folder=True) == versions.SUGGESTED
    assert outcome(0.9, None, False, same_type=False, same_folder=False) == versions.SUGGESTED  # text alone
    assert outcome(0.89, None, False, same_type=True, same_folder=True) is None
    assert outcome(0.8, 0.6, False, same_type=False, same_folder=False) == versions.SUGGESTED
    assert outcome(0.8, None, True, same_type=False, same_folder=False) == versions.SUGGESTED
    assert outcome(None, 0.6, True, same_type=False, same_folder=False) == versions.SUGGESTED
    assert outcome(0.79, 0.59, True, same_type=True, same_folder=True) is None


# --- V6: the banner's words --------------------------------------------------------------


def test_v6_the_reasons_name_each_signal_that_matched_in_plain_words() -> None:
    reasons = versions.reasons
    assert (
        reasons(0.91, 0.2, "agreement_number")
        == "91% of the text is the same and the agreement number matches"
    )
    assert reasons(0.96, 0.85, None) == "96% of the text is the same and the name is almost the same"
    assert reasons(0.5, 0.7, "survey_number") == "The name is almost the same and the survey number matches"
    assert reasons(0.86, 0.31, "survey_number") == "86% of the text is the same and the survey number matches"


def test_v6_a_suggestion_says_which_side_this_document_is() -> None:
    row: dict[str, Any] = {
        "id": "s1",
        "document_id": "d3",
        "document_name": "Satbara 44.pdf",
        "candidate_family_id": "d1",
        "candidate_id": "d1",
        "candidate_name": "7/12 Survey 44 Wakad.pdf",
        "text_overlap": 0.86,
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
    assert shown["reasons"] == "86% of the text is the same and the survey number matches"


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
