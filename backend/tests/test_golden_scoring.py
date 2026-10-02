"""The golden scorer (PRD 3 G1): outcomes, number formats, recall@5 and the intervals."""

import unittest
from typing import Any

from scripts.golden_scoring import (
    Hit,
    Outcome,
    Question,
    by_slice,
    counts_for_recall,
    question_from,
    recall_hit,
    score,
    totals,
    wilson,
    worst,
)


def question(kind: str = "number", value: Any = 11800, tolerance: float = 0, **extra: Any) -> Question:
    expect = {"kind": kind, "value": value, "tolerance": tolerance, "document": "bill.pdf", "pages": [15, 16]}
    if kind == "not_found":
        del expect["value"]
    return question_from({"id": "q1", "question": "?", "tags": [], "expect": expect, **extra})


def answer(text: str = "", *, status: str = "answered", label: str | None = "needs_review") -> dict[str, Any]:
    return {"status": status, "label": label, "sentences": [{"text": text, "citations": [1]}] if text else []}


class NumberTest(unittest.TestCase):
    def test_rupee_and_dollar_formats_parse(self) -> None:
        for text in ("The grand total is Rs. 11,800.00.", "Total: ₹11,800", "It is 11800 rupees."):
            self.assertEqual(score(question(), answer(text)).outcome, Outcome.RIGHT, text)
        self.assertEqual(
            score(question(value=12000), answer("The profit was $12,000.")).outcome, Outcome.RIGHT
        )

    def test_indian_grouping_parses(self) -> None:
        self.assertEqual(score(question(value=500000), answer("Up to Rs. 5,00,000.")).outcome, Outcome.RIGHT)
        self.assertEqual(score(question(value=50000), answer("Up to Rs. 5,00,000.")).outcome, Outcome.WRONG)

    def test_the_tolerance_is_absolute_and_zero_by_default(self) -> None:
        self.assertEqual(score(question(), answer("Rs. 11,801")).outcome, Outcome.WRONG)
        self.assertEqual(score(question(tolerance=1), answer("Rs. 11,801")).outcome, Outcome.RIGHT)
        self.assertEqual(score(question(tolerance=1), answer("Rs. 11,802")).outcome, Outcome.WRONG)
        self.assertEqual(score(question(value=2.1), answer("2.10 hectares")).outcome, Outcome.RIGHT)


class OutcomeTest(unittest.TestCase):
    def test_text_answers_match_any_listed_text_in_any_case(self) -> None:
        shinde = question("text", ["Shinde", "शिंदे"])
        self.assertEqual(score(shinde, answer("The previous owner was शिंदे विठ्ठल.")).outcome, Outcome.RIGHT)
        self.assertEqual(score(shinde, answer("It was SHINDE.")).outcome, Outcome.RIGHT)
        self.assertEqual(score(shinde, answer("It was Patil.")).outcome, Outcome.WRONG)

    def test_not_found_questions(self) -> None:
        nothing = question("not_found")
        self.assertEqual(
            score(nothing, answer(status="not_found", label=None)).outcome, Outcome.CORRECT_NOT_FOUND
        )
        missed = score(nothing, answer("Rs. 50,000", label="needs_review"))
        self.assertEqual((missed.outcome, missed.wrong_verified), (Outcome.MISSED_NOT_FOUND, False))
        self.assertTrue(score(nothing, answer("Rs. 50,000", label="verified")).wrong_verified)

    def test_a_wrong_answer_labelled_verified_is_flagged(self) -> None:
        wrong = score(question(), answer("Rs. 10,000", label="verified"))
        self.assertEqual((wrong.outcome, wrong.wrong_verified), (Outcome.WRONG, True))
        self.assertFalse(score(question(), answer("Rs. 11,800", label="verified")).wrong_verified)

    def test_no_answer_to_a_question_with_one_is_wrong(self) -> None:
        for status in ("not_found", "unchecked"):
            marked = score(question(), answer("Rs. 11,800", status=status))
            self.assertEqual((marked.outcome, marked.wrong_verified), (Outcome.WRONG, False), status)


class RecallTest(unittest.TestCase):
    def test_an_expected_page_inside_a_hit_of_the_expected_document(self) -> None:
        self.assertTrue(recall_hit(question(), [Hit("other.pdf", 15, 16), Hit("bill.pdf", 16, 17)]))
        self.assertTrue(recall_hit(question(), [Hit("bill.pdf", 14, 15)]))
        self.assertFalse(recall_hit(question(), [Hit("bill.pdf", 1, 14), Hit("other.pdf", 15, 15)]))

    def test_only_the_first_five_hits_count(self) -> None:
        misses = [Hit("bill.pdf", 1, 1)] * 5
        self.assertFalse(recall_hit(question(), [*misses, Hit("bill.pdf", 15, 15)]))
        self.assertTrue(recall_hit(question(), [*misses[:4], Hit("bill.pdf", 15, 15)]))

    def test_not_found_and_fixed_route_questions_are_left_out(self) -> None:
        self.assertTrue(counts_for_recall(question(), "search"))
        self.assertFalse(counts_for_recall(question(), "fixed_query"))
        self.assertFalse(counts_for_recall(question("not_found"), "search"))


class TotalsTest(unittest.TestCase):
    def test_wilson_matches_the_published_interval(self) -> None:
        low, high = wilson(24, 30)
        self.assertAlmostEqual(low, 0.6269, places=4)
        self.assertAlmostEqual(high, 0.9050, places=4)
        self.assertEqual(wilson(0, 0), (0.0, 1.0))
        self.assertEqual(wilson(0, 10)[0], 0.0)
        self.assertEqual(wilson(10, 10)[1], 1.0)

    def test_totals_slices_and_the_worst_run(self) -> None:
        follow = question_from(
            {
                "id": "q2",
                "question": "?",
                "follow_up_of": "q1",
                "tags": ["hinglish"],
                "expect": {"kind": "not_found", "pages": []},
            }
        )
        good = [
            (question(), score(question(), answer("Rs. 11,800")), True),
            (follow, score(follow, answer(status="not_found")), None),
        ]
        bad = [
            (question(), score(question(), answer("Rs. 1", label="verified")), False),
            (follow, score(follow, answer("x")), None),
        ]
        first, second = totals(good), totals(bad)
        self.assertEqual((first.outcomes[Outcome.RIGHT], first.recall_hits, first.recall_of), (1, 1, 1))
        self.assertEqual((second.wrong_verified, second.outcomes[Outcome.MISSED_NOT_FOUND]), (1, 1))
        slices = by_slice(good)
        self.assertEqual(sorted(slices), ["follow_up", "hinglish", "not_found"])
        self.assertEqual(slices["follow_up"].outcomes[Outcome.CORRECT_NOT_FOUND], 1)
        low = worst([first, second])
        self.assertEqual(low.outcomes[Outcome.RIGHT], 0)
        self.assertEqual((low.wrong_verified, low.recall_hits, low.recall_of), (1, 0, 1))


if __name__ == "__main__":
    unittest.main()
