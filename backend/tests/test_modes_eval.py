"""scripts/run_modes_eval.py (features 25-27, council C13 invariant 4 and C14): the scoring
maths, the invariant check, and the --fake run of tests/fixtures/modes_eval_fake.json
through the real pipeline with scripted models (no network, no database)."""

import io
import unittest
from contextlib import redirect_stderr

import pytest

from scripts.run_modes_eval import MODES, Read, Tally, fake_run, main, problems, report, same, tally

TRUTH = {("bill.pdf", "total_amount"): "1,180.00", ("bill.pdf", "gstin"): "27AAKFS0812L1Z9"}


def read(text: str, label: str = "verified", field: str = "total_amount", document: str = "bill.pdf") -> Read:
    return Read(document, field, text, label)


class ScoringTest(unittest.TestCase):
    def test_each_count(self) -> None:
        reads = [
            read("₹ 1,180.00"),  # right: currency sign and spaces are the checker's to ignore
            read("1,130.00"),  # wrong and Verified
            read("27AAKFSO812L1Z9", "needs_review", "gstin"),  # wrong but in review: not counted wrong
            read("Patil", "verified", "party_name"),  # no truth for it
        ]
        self.assertEqual(
            tally(reads, TRUTH, tokens=5700),
            Tally(values=4, verified=3, wrong_verified=1, needs_review=1, abstain=0, unjudged=1, tokens=5700),
        )

    def test_a_truth_value_nothing_was_read_for_is_abstain(self) -> None:
        counts = tally([read("1,180.00")], TRUTH)
        self.assertEqual((counts.abstain, counts.values), (1, 1))
        self.assertEqual(tally([], TRUTH).abstain, 2)

    def test_same_is_the_checkers_text_equality_and_never_number_equality(self) -> None:
        self.assertTrue(same("Rs. 1,180.00", "1,180.00"))
        self.assertTrue(same("TOTAL", "total"))
        for a, b in (("(1,180)", "1,180"), ("01/02/2026", "1/2/2026"), ("1,24,560.00", "124560")):
            with self.subTest(a=a, b=b):
                self.assertFalse(same(a, b))

    def test_precision_is_right_verified_over_verified_and_none_without_verified(self) -> None:
        self.assertEqual(Tally(verified=4, wrong_verified=1).precision, 0.75)
        self.assertIsNone(Tally(needs_review=3).precision)


class ProblemsTest(unittest.TestCase):
    @staticmethod
    def modes(single: Tally, dynamic: Tally, ensemble: Tally) -> dict[str, Tally]:
        return dict(zip(MODES, (single, dynamic, ensemble), strict=True))

    def test_equal_or_better_is_no_problem(self) -> None:
        single = Tally(verified=4, wrong_verified=1)
        better = Tally(verified=6, wrong_verified=1)
        self.assertEqual(problems(self.modes(single, better, better)), [])
        self.assertEqual(problems(self.modes(Tally(), Tally(), Tally())), [])  # nothing Verified

    def test_ensemble_with_more_wrong_and_verified_than_single_is_broken(self) -> None:
        single = Tally(verified=4, wrong_verified=1)
        (found,) = problems(self.modes(single, single, Tally(verified=9, wrong_verified=2)))
        self.assertEqual(found, "Ensemble shows 2 wrong values as Verified, Single 1")

    def test_dynamic_with_lower_verified_precision_than_single_is_broken(self) -> None:
        single = Tally(verified=4, wrong_verified=1)  # 75%
        (found,) = problems(self.modes(single, Tally(verified=3, wrong_verified=1), single))
        self.assertEqual(found, "Dynamic's Verified precision 66.7% is below Single's 75.0%")

    def test_the_report_has_a_row_per_mode(self) -> None:
        rows = report(self.modes(Tally(verified=4, wrong_verified=1), Tally(), Tally())).splitlines()
        self.assertEqual([row.split()[0] for row in rows], ["mode", *MODES])
        self.assertEqual(rows[1].split()[-1], "75.0%")
        self.assertEqual(rows[2].split()[-1], "-")


@pytest.mark.eval
class FakeRunTest(unittest.TestCase):
    """The fixture through the real reading code. The table is pinned: a change in how a
    mode labels values shows here. Changed on purpose (Ensemble after the scan, 16 Oct
    2026): a scan no longer votes (labels.settle_votes is gone), so Ensemble labels as
    Single does, at more tokens, and the stamp case (the checker and both other models
    misread the same digit the main model read right) stays Needs review: the vote had
    Verified the wrong digit."""

    def test_the_per_mode_table(self) -> None:
        runs, truth = fake_run()
        tallies = {mode: tally(reads, truth, tokens) for mode, (reads, tokens) in runs.items()}
        rows = {
            mode: (c.values, c.verified, c.wrong_verified, c.needs_review, c.abstain, c.unjudged)
            for mode, c in tallies.items()
        }
        self.assertEqual(
            rows,
            {
                "single": (7, 3, 1, 4, 1, 0),
                "dynamic": (8, 7, 2, 1, 0, 0),
                "ensemble": (7, 3, 1, 4, 1, 0),
            },
        )
        self.assertLess(tallies["single"].tokens, tallies["dynamic"].tokens)
        self.assertLess(tallies["dynamic"].tokens, tallies["ensemble"].tokens)
        self.assertEqual(problems(tallies), [])

    def test_the_stamp_case_is_no_longer_the_ensemble_break(self) -> None:
        runs, truth = fake_run()
        stamp = "stamp over digits misleads the checker and the other models"
        labels = {
            mode: [(r.text, r.label) for r in reads if r.document == stamp]
            for mode, (reads, _t) in runs.items()
        }
        self.assertEqual(labels["single"], [("1,180.00", "needs_review")])
        self.assertEqual(labels["ensemble"], [("1,180.00", "needs_review")])
        self.assertEqual(truth[(stamp, "total_amount")], "1,180.00")


class MainTest(unittest.TestCase):
    def test_a_live_run_needs_its_truth_and_models(self) -> None:
        for argv in (
            [],
            ["--truth", "t.json", "--careful", "a/b"],
            ["--truth", "t", "--careful", "a/b", "--others", "a/c,a/d,a/e"],
        ):
            with (
                self.subTest(argv=argv),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as stopped,
            ):
                main(argv)
            self.assertEqual(stopped.exception.code, 2)

    @pytest.mark.eval
    def test_fake_prints_the_table_and_exits_0_with_no_broken_invariant(self) -> None:
        # Changed on purpose (Ensemble after the scan, 16 Oct 2026): see FakeRunTest.
        out = io.StringIO()
        self.assertEqual(main(["--fake"], out), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([line.split()[0] for line in lines[:4]], ["mode", *MODES])
        self.assertEqual(lines[4:], [])
