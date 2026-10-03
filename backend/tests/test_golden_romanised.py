"""The Romanised eval (PR 2b-2): tests/golden/questions_romanised.json, Marathi and
Hindi questions typed in English letters about the golden documents.

40 answerable questions, each in 2 or 3 spellings, and 15 not-found traps (gat 48 for
gat 47, a near name, kar/khar, an heir, Rashid for a receipt, "check kar"...). The
``expect`` is golden_scoring's. The file is frozen by its sha256, and no spelling uses
a word the sound key or the glossary was tuned on (ENGINEERING_STANDARDS 3.7,
leakage). CI checks the file only: the live run needs the stack and a model (NOT RUN).
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from kaagaz.answering import glossary
from scripts.golden_scoring import KINDS, file_sha256, sha256_manifest
from tests.test_sound_key import DIFFERENT, KNOWN_MERGES, SAME

GOLDEN = Path(__file__).resolve().parent / "golden"
QUESTIONS = GOLDEN / "questions_romanised.json"
# Any edit must change this line too, and means a new baseline.
QUESTIONS_SHA256 = "a935c1b79a54379d7d963f2e5836fcf929d715fe88efc6a3713a3840987a266d"
ANSWERABLE, NOT_FOUND = 40, 15
TAGS = frozenset({"land_record", "hindi_bill", "bank", "invoice", "agreement", "names", "inflected", "trap"})
# Spellings the rules were tuned on (the council's measurements and the unit tests).
_TUNED_WORDS = (
    "malak malik bhogvatdar bhogwatdar khatedar jamin zamin zameen bhumi kshetra kshetrafal kshetraphal"
    " rakba ferfar pherphar akarni aakarni pik fasal shetkari kisan hektar hectare ekar gav gaon taluka"
    " tahsil tehsil jilha zilla jila bijak bil pavti pawti rasid raseed kimmat kimat keemat rakkam rakam"
    " raqam ekun ekoon kharedidar kharidar vikreta grahak vikri bikri kharch kharcha nafa bhade bhaade"
    " kiraya kiraaya pagar vetan khate khata shillak shilak vyaj byaj thev jama thakbaki bakaya bharna"
    " bhugtan karj karz karja rin gaikwad gaekwad patil shinde shindey kulkarni deshpande jadhav jadhao"
    " pawar chavan chauhan deshmukh bhosale ramchandra ganpat dnyaneshwar gyaneshwar krushna thackeray"
    " wadgaon vadgaon haveli pune satbara utara jaminicha malakache malakacha bhogvatdarache maalak"
    " gayakavad varsha varas khar"
)
TUNED = frozenset(
    _TUNED_WORDS.split()
    + list(glossary._ROMANISED_SPELLINGS)
    + [word for _, group in SAME for word in group]
    + [word for row in KNOWN_MERGES for word in row[1:]]
    + [word for row in DIFFERENT for word in row]
)
_DEVANAGARI = re.compile("[ऀ-ॿ]")


def _load() -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return raw


def problems(raw: list[dict[str, Any]], documents: set[str]) -> list[str]:
    """What is wrong with the file, as messages; [] when nothing is. O(q * s)."""
    found: list[str] = []
    ids = Counter(str(item.get("id")) for item in raw)
    found += [f"{name}: the id is not unique" for name, count in ids.items() if count > 1]
    kinds = Counter(item["expect"]["kind"] == "not_found" for item in raw)
    if (kinds[False], kinds[True]) != (ANSWERABLE, NOT_FOUND):
        found.append(
            f"{kinds[False]} answerable and {kinds[True]} not-found, not {ANSWERABLE} and {NOT_FOUND}"
        )
    for item in raw:
        name, expect, spellings = item["id"], item["expect"], item["spellings"]
        least = 1 if expect["kind"] == "not_found" else 2
        if not least <= len(set(spellings)) == len(spellings) <= 3:
            found.append(f"{name}: needs {least} to 3 different spellings")
        if expect["kind"] not in KINDS or (expect["kind"] != "not_found") == ("value" not in expect):
            found.append(f"{name}: the kind and the value do not agree")
        if "document" in expect and expect["document"] not in documents:
            found.append(f"{name}: document '{expect['document']}' is not in the sha256 manifest")
        if expect["kind"] != "not_found" and not expect["pages"]:
            found.append(f"{name}: an answer needs its pages")
        if not set(item["tags"]) <= TAGS:
            found.append(f"{name}: unknown tags")
        for spelling in spellings:
            if _DEVANAGARI.search(spelling):
                found.append(f"{name}: '{spelling}' is not in English letters")
            tuned = sorted(set(re.findall("[a-z]+", spelling.casefold())) & TUNED)
            if tuned:
                found.append(f"{name}: '{spelling}' uses tuned words {tuned}")
    return found


def _documents() -> set[str]:
    return set(sha256_manifest((GOLDEN / "documents.sha256").read_text(encoding="utf-8")))


def test_the_file_meets_every_rule() -> None:
    assert problems(_load(), _documents()) == []


def test_the_questions_are_frozen() -> None:
    assert file_sha256(QUESTIONS) == QUESTIONS_SHA256


def test_a_tuned_spelling_is_refused() -> None:
    raw = _load()
    raw[0] = {**raw[0], "spellings": ["gat 45/2 cha malak kon?", "45/2 cha khaatedaar kon?"]}
    assert problems(raw, _documents()) == ["r01: 'gat 45/2 cha malak kon?' uses tuned words ['malak']"]


def test_too_few_spellings_a_missing_document_and_wrong_counts_are_refused() -> None:
    raw = _load()
    raw[1] = {**raw[1], "spellings": raw[1]["spellings"][:1]}
    raw[2] = {**raw[2], "expect": {**raw[2]["expect"], "document": "missing.csv"}}
    assert problems(raw[:-1], _documents()) == [
        "40 answerable and 14 not-found, not 40 and 15",
        "r02: needs 2 to 3 different spellings",
        "r03: document 'missing.csv' is not in the sha256 manifest",
    ]


def test_a_devanagari_spelling_and_a_repeated_id_are_refused() -> None:
    raw = _load()
    raw[3] = {**raw[3], "id": "r01", "spellings": ["गट 45/2 मालक?", "45/2 cha purvicha maalik kon?"]}
    assert problems(raw, _documents()) == [
        "r01: the id is not unique",
        "r01: 'गट 45/2 मालक?' is not in English letters",
    ]
