"""The golden questions file and its documents (PRD 3 G2): checked on every pull request."""

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from scripts.golden_scoring import file_sha256, problems, sha256_manifest

GOLDEN = Path(__file__).parent / "golden"


def load() -> tuple[list[dict[str, Any]], dict[str, str]]:
    raw = json.loads((GOLDEN / "questions.json").read_text(encoding="utf-8"))
    return raw, sha256_manifest((GOLDEN / "documents.sha256").read_text(encoding="utf-8"))


class GoldenQuestionsTest(unittest.TestCase):
    def test_the_file_meets_every_minimum(self) -> None:
        raw, manifest = load()
        self.assertEqual(problems(raw, manifest), [])

    def test_the_manifest_matches_every_document_byte_for_byte(self) -> None:
        _, manifest = load()
        on_disk = {path.name: file_sha256(path) for path in (GOLDEN / "docs").iterdir()}
        self.assertEqual(on_disk, manifest)

    def test_a_follow_up_must_point_to_an_earlier_question(self) -> None:
        raw, manifest = load()
        broken = copy.deepcopy(raw)
        broken[1]["follow_up_of"] = raw[-1]["id"]
        self.assertIn(
            f"{raw[1]['id']}: follow_up_of {raw[-1]['id']!r} is not an earlier question",
            problems(broken, manifest),
        )

    def test_every_document_must_be_in_the_manifest(self) -> None:
        raw, manifest = load()
        broken = copy.deepcopy(raw)
        broken[0]["expect"]["document"] = "missing.pdf"
        self.assertIn(
            f"{raw[0]['id']}: document 'missing.pdf' is not in the sha256 manifest",
            problems(broken, manifest),
        )

    def test_too_few_not_found_questions_is_refused(self) -> None:
        raw, manifest = load()
        kept = [q for q in raw if q["expect"]["kind"] != "not_found" or q["id"] == "g03"]
        found = problems(kept, manifest)
        self.assertIn("1 not_found, at least 5", found)
        self.assertIn(f"{len(kept)} questions, not 30", found)

    def test_each_kind_needs_the_right_value(self) -> None:
        raw, manifest = load()
        broken = copy.deepcopy(raw)
        broken[0]["expect"]["value"] = "12000"  # a number answer given as text
        broken[1]["expect"]["value"] = 1  # a not-found question with a value
        broken[5]["expect"]["value"] = ["Patil", ""]
        found = problems(broken, manifest)
        self.assertIn(f"{raw[0]['id']}: a number answer needs a number value", found)
        self.assertIn(f"{raw[1]['id']}: a not-found question has no value", found)
        self.assertIn(f"{raw[5]['id']}: a text answer needs a text value, or a list of them", found)

    def test_a_cross_page_answer_names_two_pages(self) -> None:
        raw, manifest = load()
        broken = copy.deepcopy(raw)
        broken[0]["expect"]["pages"] = [15]
        self.assertIn(
            f"{raw[0]['id']}: a cross-page answer names at least 2 pages", problems(broken, manifest)
        )

    def test_ids_are_unique_and_tags_known(self) -> None:
        raw, manifest = load()
        broken = copy.deepcopy(raw)
        broken[2]["id"] = raw[0]["id"]
        broken[3]["tags"] = ["made_up"]
        found = problems(broken, manifest)
        self.assertIn(f"{raw[0]['id']}: the id is used twice", found)
        self.assertTrue(any(p.startswith(f"{raw[3]['id']}: tags must be a list") for p in found))


if __name__ == "__main__":
    unittest.main()
