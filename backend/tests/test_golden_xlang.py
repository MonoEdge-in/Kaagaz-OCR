"""The cross-language eval (council 4 Oct 2026, PR A): tests/golden/questions_xlang.json,
English and Romanised questions over 6 synthetic scanned PDFs in tests/golden/xlang/
(Hindi, Marathi, Gujarati, Bengali, Tamil, Urdu). CI checks the files only: the live
run needs the stack and the model (scripts/run_golden.py --set xlang, NOT RUN here)."""

import copy
import json
import unicodedata
from pathlib import Path
from typing import Any

from scripts.golden_scoring import (
    XLANG_SHA256,
    Outcome,
    file_sha256,
    question_from,
    score,
    sha256_manifest,
    xlang_problems,
)

GOLDEN = Path(__file__).resolve().parent / "golden"
QUESTIONS = GOLDEN / "questions_xlang.json"
DOCS = GOLDEN / "xlang"


def _load() -> tuple[list[dict[str, Any]], dict[str, str]]:
    raw: list[dict[str, Any]] = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return raw, sha256_manifest((DOCS / "documents.sha256").read_text(encoding="utf-8"))


def test_the_file_meets_every_rule_and_is_frozen() -> None:
    raw, manifest = _load()
    assert xlang_problems(raw, manifest) == []
    # An edit means a new baseline: change XLANG_SHA256 with it, on purpose.
    assert file_sha256(QUESTIONS) == XLANG_SHA256


def test_every_document_exists_and_matches_its_sha256() -> None:
    raw, manifest = _load()
    assert {item["expect"]["document"] for item in raw} == set(manifest)
    on_disk = {path.name: file_sha256(path) for path in DOCS.glob("*.pdf")}
    assert on_disk == manifest
    for name in manifest:
        assert (DOCS / name).read_bytes().startswith(b"%PDF-")


def test_no_hidden_characters_and_every_text_is_nfc() -> None:
    text = QUESTIONS.read_text(encoding="utf-8")
    assert [hex(ord(ch)) for ch in text if unicodedata.category(ch) in {"Cf", "Co", "Cs"}] == []
    assert unicodedata.is_normalized("NFC", text)


def test_both_splits_cover_every_language() -> None:
    raw, _ = _load()
    slices = [set(question_from(item).slices) for item in raw]
    for language in ("hi", "mr", "gu", "bn", "ta", "ur"):
        assert all(any({language, split} <= s for s in slices) for split in ("dev", "test"))


def test_a_broken_entry_is_refused() -> None:
    raw, manifest = _load()
    broken = copy.deepcopy(raw)
    broken[0]["tags"] = ["hi", "mr", "english", "name"]
    broken[1]["split"] = "train"
    broken[2]["question"] = "मासिक किराया कितना है?"
    broken[3]["expect"]["document"] = "missing.pdf"
    assert xlang_problems(broken[:-1], manifest) == [
        "x01: tags must name one language, one style and one type",
        "x02: split must be one of ('dev', 'test')",
        "x03: the question must be typed in English letters",
        "x04: document 'missing.pdf' is not in the sha256 manifest",
        "59 questions and 9 not-found, not 60 and 10",
    ]


def test_other_script_digits_are_folded_on_both_sides() -> None:
    raw, _ = _load()
    by_id = {item["id"]: question_from(item) for item in raw}
    loan, letter_date = by_id["x31"], by_id["x36"]  # 85,000 and 14/01/2026 on the Bengali page

    def said(text: str) -> dict[str, Any]:
        return {"status": "answered", "label": "needs_review", "sentences": [{"text": text}]}

    bengali = str.maketrans("0123456789", "".join(chr(0x09E6 + n) for n in range(10)))
    assert score(loan, said(f"The loan is {'85,000'.translate(bengali)}.")).outcome == Outcome.RIGHT
    dated = f"The letter is dated {'14/01/2026'.translate(bengali)}."
    assert score(letter_date, said(dated)).outcome == Outcome.RIGHT
    assert score(letter_date, said("The letter is dated 14 January 2026.")).outcome == Outcome.RIGHT
    assert score(loan, said("The monthly instalment is 2,625.")).outcome == Outcome.WRONG
