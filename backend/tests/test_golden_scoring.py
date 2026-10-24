"""The golden scorer (PRD 3 G1): outcomes, number formats, recall@5 and the intervals."""

import unittest
from typing import Any

from scripts.golden_scoring import (
    Hit,
    Outcome,
    Question,
    by_slice,
    category,
    compare,
    counts_for_recall,
    mcnemar,
    metrics,
    nlq_metrics,
    nlq_score,
    opening_problems,
    question_from,
    recall_hit,
    romanised_problems,
    score,
    spelled_out,
    split_of,
    totals,
    wilson,
    worst,
    worst_nlq,
    worst_score,
)


def question(
    kind: str = "number", value: Any = 11800, tolerance: float = 0, distractors: Any = (), **extra: Any
) -> Question:
    expect = {"kind": kind, "value": value, "tolerance": tolerance, "document": "bill.pdf", "pages": [15, 16]}
    expect["not"] = list(distractors)
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

    def test_devanagari_digits_parse(self) -> None:
        bill = "कुल ₹ ४५\u0966.\u0966\u0966"  # the Hindi bill's total line, 450.00
        self.assertEqual(score(question(value=450), answer(bill)).outcome, Outcome.RIGHT)
        self.assertEqual(score(question(value=45), answer(bill)).outcome, Outcome.WRONG)

    def test_a_distractor_in_the_answer_makes_it_wrong(self) -> None:
        balance = question(value=46800, distractors=[61800, 36800])
        self.assertEqual(score(balance, answer("The balance was Rs. 46,800.")).outcome, Outcome.RIGHT)
        listed = score(balance, answer("Balances: 61,800, 46,800 and 36,800.", label="verified"))
        self.assertEqual((listed.outcome, listed.wrong_verified), (Outcome.WRONG, True))
        gat = question(value=47, distractors=["45/2", "46/1"])
        self.assertEqual(score(gat, answer("Gat 47, next to 46/1.")).outcome, Outcome.WRONG)
        self.assertEqual(score(gat, answer("Gat number 47.")).outcome, Outcome.RIGHT)

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

    def test_a_text_matches_only_as_a_whole(self) -> None:
        percent, gat = question("text", "8%"), question("text", "45/2")
        self.assertEqual(score(percent, answer("It was 18% more.")).outcome, Outcome.WRONG)
        self.assertEqual(score(percent, answer("It was 8% more.")).outcome, Outcome.RIGHT)
        self.assertEqual(score(gat, answer("Gat 145/2.")).outcome, Outcome.WRONG)
        self.assertEqual(score(gat, answer("Gat 45/23.")).outcome, Outcome.WRONG)
        self.assertEqual(score(gat, answer("It is gat 45/2.")).outcome, Outcome.RIGHT)
        notice = question("text", ["60 days", "sixty days"])
        self.assertEqual(score(notice, answer("Sixty days' notice.")).outcome, Outcome.RIGHT)

    def test_not_found_questions(self) -> None:
        nothing = question("not_found")
        self.assertEqual(
            score(nothing, answer(status="not_found", label=None)).outcome, Outcome.CORRECT_NOT_FOUND
        )
        missed = score(nothing, answer("Rs. 50,000", label="needs_review"))
        self.assertEqual((missed.outcome, missed.wrong_verified), (Outcome.MISSED_NOT_FOUND, False))
        self.assertTrue(score(nothing, answer("Rs. 50,000", label="verified")).wrong_verified)
        # "unchecked" claims the figure exists in a card, so it is a miss
        unchecked = score(nothing, answer("Confirm it in Review.", status="unchecked"))
        self.assertEqual(unchecked.outcome, Outcome.MISSED_NOT_FOUND)

    def test_a_wrong_answer_labelled_verified_is_flagged(self) -> None:
        wrong = score(question(), answer("Rs. 10,000", label="verified"))
        self.assertEqual((wrong.outcome, wrong.wrong_verified), (Outcome.WRONG, True))
        self.assertFalse(score(question(), answer("Rs. 11,800", label="verified")).wrong_verified)

    def test_no_answer_to_a_question_with_one_is_abstained(self) -> None:
        # Changed on purpose (final polish, 16 Oct 2026): ask precision PR 1a scores an
        # abstention apart from a wrong answer (it was WRONG), so precision can be measured.
        for status in ("not_found", "unchecked"):
            marked = score(question(), answer("Rs. 11,800", status=status))
            self.assertEqual((marked.outcome, marked.wrong_verified), (Outcome.ABSTAINED, False), status)
            self.assertEqual(category(marked), "abstained")


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
        # Changed on purpose (final polish, 16 Oct 2026): every question now has a split
        # (ask precision PR 1a); "q1" and "q2" both hash to "test".
        self.assertEqual(sorted(slices), ["follow_up", "hinglish", "not_found", "test"])
        self.assertEqual(slices["follow_up"].outcomes[Outcome.CORRECT_NOT_FOUND], 1)
        low = worst([first, second])
        self.assertEqual(low.outcomes[Outcome.RIGHT], 0)
        self.assertEqual((low.wrong_verified, low.recall_hits, low.recall_of), (1, 0, 1))


def asked(qid: str, kind: str = "number", text: str = "?") -> Question:
    expect: dict[str, Any] = {"kind": kind, "document": "bill.pdf", "pages": [15]}
    expect |= {} if kind == "not_found" else {"value": 11800}
    return question_from({"id": qid, "question": text, "tags": [], "expect": expect})


class PrecisionTest(unittest.TestCase):
    """Ask precision PR 1a: multi_value, the metrics, the worst run, splits, the Romanised
    set and the paired gate."""

    def test_a_right_answer_with_another_money_amount_is_multi_value(self) -> None:
        both = score(question(), answer("Rs. 11,800 in all (Rs. 10,000 and 1,800 tax)."))
        self.assertEqual((both.outcome, both.multi_value), (Outcome.RIGHT, True))
        self.assertFalse(score(question(), answer("Rs. 11,800, on 12 August 2026, for 3 items.")).multi_value)
        named = asked("q1", text="Is Rs. 10,000 the total?")
        self.assertFalse(score(named, answer("No, the total is Rs. 11,800, not Rs. 10,000.")).multi_value)
        self.assertTrue(score(question("text", "Patil"), answer("Patil paid ₹5,000.")).multi_value)
        self.assertFalse(score(question(), answer("Rs. 10,000 and Rs. 5,000.")).multi_value)  # not right

    def test_each_metric_counts_what_it_says(self) -> None:
        hits = [Hit("bill.pdf", 15, 15), Hit("other.pdf", 15, 15)]
        scored = [
            (asked("a"), score(asked("a"), answer("Rs. 11,800", label="verified"), hits)),
            (asked("b"), score(asked("b"), answer("Rs. 11,800 of Rs. 20,000"))),
            (asked("c"), score(asked("c"), answer("Rs. 1", label="verified"))),
            (asked("d"), score(asked("d"), answer(status="not_found", label=None))),
            (asked("e", "not_found"), score(asked("e", "not_found"), answer(status="not_found", label=None))),
            (asked("f", "not_found"), score(asked("f", "not_found"), answer("Rs. 9"))),
        ]
        self.assertEqual(
            metrics(scored),
            {
                "answer_precision": (2, 4),
                "answer_precision_strict": (1, 4),
                "verified_precision": (1, 2),
                "verified_coverage": (1, 4),
                "coverage": (3, 4),
                "abstention_precision": (1, 2),
                "trap_accuracy": (1, 2),
                "citation_precision": (1, 2),
            },
        )

    def test_a_romanised_trap_is_right_only_when_every_spelling_is_not_found(self) -> None:
        item = {"id": "r41", "spellings": ["a?", "b?"], "tags": ["trap"], "expect": {"kind": "not_found"}}
        item["expect"] |= {"document": "bill.pdf", "pages": [1]}
        questions = [question_from(raw) for raw in spelled_out([item])]
        self.assertEqual([q.id for q in questions], ["r41#1", "r41#2"])
        self.assertEqual({q.slices for q in questions}, {("trap", "not_found", split_of("r41"))})
        found, missed = answer(status="not_found", label=None), answer("Rs. 9")
        one_missed = [(questions[0], score(questions[0], found)), (questions[1], score(questions[1], missed))]
        self.assertEqual(metrics(one_missed)["trap_accuracy"], (0, 1))
        both = [(q, score(q, found)) for q in questions]
        self.assertEqual(metrics(both)["trap_accuracy"], (1, 1))
        self.assertEqual(romanised_problems([item], {"bill.pdf"}), [])
        self.assertEqual(
            romanised_problems([item], {"other.pdf"}),
            [f"r41#{n}: document 'bill.pdf' is not in the sha256 manifest" for n in (1, 2)],
        )

    def test_the_derived_split_is_stable_and_a_file_split_wins(self) -> None:
        self.assertEqual([split_of("q1"), split_of("r01")], ["test", "dev"])  # never changes
        self.assertIn("test", asked("q1").slices)
        expect = {"kind": "not_found", "pages": []}
        own = question_from({"id": "q1", "question": "?", "tags": [], "expect": expect, "split": "dev"})
        self.assertEqual(own.slices, ("not_found", "dev"))

    def test_the_worst_run_is_wrong_verified_then_wrong_then_abstained(self) -> None:
        q = asked("q1")
        runs = [
            score(q, answer("Rs. 11,800", label="verified")),
            score(q, answer("Rs. 11,800")),
            score(q, answer(status="not_found", label=None)),
            score(q, answer("Rs. 1")),
            score(q, answer("Rs. 1", label="verified")),
        ]
        for n in range(1, len(runs)):
            self.assertIs(worst_score(runs[: n + 1]), runs[n])
            self.assertIs(worst_score(list(reversed(runs[: n + 1]))), runs[n])
        self.assertEqual([category(m) for m in runs[2:]], ["abstained", "wrong", "wrong_verified"])

    def test_the_paired_gate_fails_on_each_of_its_three_rules(self) -> None:
        def run(*outcomes: str, verified: int = 0) -> dict[str, dict[str, Any]]:
            return {f"q{i}": {"outcome": o, "verified": i < verified} for i, o in enumerate(outcomes)}

        base = run("right", "right", "right", "abstained", "wrong_verified", verified=3)
        same = compare(base, base)
        self.assertEqual(same, ([], [], 1.0))
        changed, failures, _ = compare(base, run("right", "right", "right", "wrong", "wrong", verified=3))
        self.assertEqual(changed, ["q3: abstained -> wrong", "q4: wrong_verified -> wrong"])
        self.assertEqual(failures, ["q3 moved into wrong"])  # wrong_verified -> wrong is better
        two = run("abstained", "abstained", "right", "abstained", "wrong_verified", verified=3)
        self.assertEqual(compare(base, two)[1], [])  # two right -> abstained, coverage down by 2
        three = run("abstained", "abstained", "abstained", "abstained", "wrong_verified")
        self.assertEqual(
            compare(base, three)[1],
            [
                "3 right answers abstained, at most 2",
                "verified_coverage fell from 3 to 0 questions, at most 2 fewer",
            ],
        )
        self.assertEqual(compare(base, {"q9": {"outcome": "wrong", "verified": False}}), ([], [], 1.0))

    def test_mcnemar_is_exact(self) -> None:
        self.assertEqual(mcnemar(0, 0), 1.0)
        self.assertEqual(mcnemar(5, 0), 0.0625)  # 2 * (1/2)**5
        self.assertEqual(mcnemar(3, 3), 1.0)
        self.assertAlmostEqual(mcnemar(1, 6), 2 * (1 + 7) / 128)


# PRD 6 package G: the NLQ expectations. Replies use PRD 6's answer contract; today's have
# only status, sentences and citations, so a missing field must score as a failure.
ASKER = "Tanvi"
DAYS = {2041: 12, 2045: 15, 2049: 17, 2052: 18, 2057: 19, 2060: 23, 2063: 26, 2066: 29}
NAMES = {f"d{n}": f"invoice-inv-{n}.pdf" for n in DAYS} | {
    "o": "owale.pdf",
    "r": "rent.pdf",
    "l": "licence.pdf",
}
PROFIT = {2041: ("Rs. 18,400.00", 2, "verified"), 2063: ("Rs. 31,000.00", 1, "verified")}
PROFIT[2057] = ("Rs. 9,250.00", 1, "needs_review")


def nlq_question(
    text: str = "Calculate the Profit filed in all the invoices filled in month of September", **expect: Any
) -> Question:
    base = {"kind": "number", "value": 18400, "document": "invoice-inv-2041.pdf", "pages": [2]}
    return question_from({"id": "n1", "question": text, "split": "dev", "tags": [], "expect": base | expect})


def figure(n: int, field: str = "profit") -> dict[str, Any]:
    if field == "profit":
        printed, page, label = PROFIT[n]
        return {"field": field, "as_printed": printed, "page": page, "label": label}
    return {"field": field, "as_printed": f"{DAYS[n]}/09/2026", "page": 1, "label": "verified"}


def example_3(**changes: Any) -> dict[str, Any]:
    """PRD 6 Example 3's reply over the 8 September invoices, 3 of them printing a profit."""
    why = {
        n: f"INV-{n}, {DAYS[n]} Sep, " + ("profit as printed" if n in PROFIT else "no profit") for n in DAYS
    }
    shown = {n: [figure(n, "invoice_date"), *([figure(n)] if n in PROFIT else [])] for n in DAYS}
    said = ["I found 8 invoices dated September 2026.", "Only 3 of them print a profit, so no total."]
    reply: dict[str, Any] = {"kind": "answer", "status": "answered", "opening": "Sure, Tanvi.", "total": None}
    reply["sentences"] = [{"text": text, "citations": [1]} for text in said]
    reply["documents"] = [{"id": f"d{n}", "why": why[n], "figures": shown[n]} for n in DAYS]
    reply["assumptions"] = ["I took September as September 2026.", "5 more invoices print no profit."]
    return reply | changes


def said_too(reply: dict[str, Any], text: str) -> dict[str, Any]:
    return reply | {"sentences": [*reply["sentences"], {"text": text, "citations": [1]}]}


class NlqTest(unittest.TestCase):
    def test_the_prd_example_3_reply_keeps_the_numbers_rule(self) -> None:
        question = nlq_question(no_total={"allowed": None})
        self.assertEqual(nlq_score(question, example_3(), example_3(), NAMES, ASKER).violations, ())
        summed = nlq_score(question, {}, said_too(example_3(), "That is Rs. 58,650 in all."), NAMES, ASKER)
        self.assertEqual((summed.violations, summed.passed()["no_total"]), (("58650 in sentences",), False))
        total = {"as_text": "Rs. 58,650", "working": [figure(n) for n in PROFIT], "all_verified": False}
        refused = nlq_score(question, {}, example_3(total=total), NAMES, ASKER).violations
        self.assertEqual(refused, ("a total where none is permitted", "58650 in total"))

    def test_a_derived_share_breaks_the_numbers_rule(self) -> None:
        shares = [{"field": "owner_share", "as_printed": s, "page": 1, "label": None} for s in ("50%", "30%")]
        said = {
            "text": "The extract lists owners holding 50% and 30%. Please check page 1.",
            "citations": [1],
        }
        owners = {"id": "o", "why": "owners Aniket Rege 50%, Hrishikesh Ghatge 30%", "figures": shares}
        printed = {"status": "answered", "sentences": [said], "documents": [owners]}
        question = nlq_question("Tell me the unowned percentage of Survey 18?", no_total={"allowed": None})
        self.assertEqual(nlq_score(question, {}, printed, NAMES, ASKER).violations, ())
        unowned = said_too(printed, "So 20% unowned.")
        self.assertEqual(nlq_score(question, {}, unowned, NAMES, ASKER).violations, ("20% in sentences",))
        words = said_too(printed, "One-fifth is not owned, twenty percent.")
        self.assertEqual(
            nlq_score(question, {}, words, NAMES, ASKER).violations,
            ("one-fifth in sentences", "twenty percent in sentences"),
        )

    def test_a_permitted_total_is_the_sum_of_printed_verified_working(self) -> None:
        question = nlq_question("Add up the profit on the July 2026 invoices", no_total={"allowed": "12,250"})
        printed = {"page": 1, "label": "verified"}
        working = [printed | {"as_printed": p} for p in ("Rs. 6,800.00", "Rs. 5,450.00")]
        reply = {"status": "answered", "sentences": [{"text": "Rs. 12,250 in all."}], "documents": []}
        good = reply | {"total": {"as_text": "Rs. 12,250", "working": working, "all_verified": True}}
        self.assertEqual(nlq_score(question, {}, good, NAMES, ASKER).violations, ())
        unverified = [*working[:1], working[1] | {"label": "needs_review"}]
        bad = reply | {"total": {"as_text": "Rs. 12,250", "working": unverified}}
        self.assertEqual(
            nlq_score(question, {}, bad, NAMES, ASKER).violations,
            (
                "a total whose working is not printed, Verified figures",
                "12250 in sentences",
                "12250 in total",
            ),
        )
        no_working = nlq_score(question, {}, reply, NAMES, ASKER).violations
        self.assertEqual(no_working, ("12250 in sentences",))

    def test_todays_reply_is_read_from_its_citations(self) -> None:
        citation = {"n": 1, "document_id": "d2041", "label": "verified"} | dict.fromkeys(
            ["page_no", "page_from", "page_to"], 2
        )
        today = {
            "status": "answered",
            "label": "verified",
            "sentences": [{"text": "INV-2041 prints a profit of Rs. 18,400.00.", "citations": [1]}],
            "citations": [citation | {"snippet": "Profit on this invoice: Rs. 18,400.00"}],
        }
        question = nlq_question(
            documents={"expected": ["invoice-inv-2041.pdf"], "not": ["invoice-inv-2045.pdf"]},
            figure=figure(2041) | {"document": "invoice-inv-2041.pdf"},
            no_total={"allowed": None},
        )
        marked = nlq_score(question, today, today, NAMES, ASKER)
        self.assertEqual((marked.documents, marked.figure, marked.violations), ((1, 1, 1), True, ()))
        elsewhere = today | {"citations": [citation | dict.fromkeys(["page_no", "page_from", "page_to"], 1)]}
        self.assertFalse(nlq_score(question, today, elsewhere, NAMES, ASKER).figure)
        unsure = today | {"citations": [citation | {"label": "needs_review"}]}
        self.assertFalse(nlq_score(question, today, unsure, NAMES, ASKER).figure)

    def test_a_reply_without_the_new_fields_fails_and_never_errors(self) -> None:
        question = nlq_question(
            kind="not_found",
            documents={"expected": ["invoice-inv-2041.pdf"]},
            clarify={"choices": ["Maval"], "pick": "Maval"},
            figure=figure(2041) | {"document": "invoice-inv-2041.pdf"},
            no_total={"allowed": None},
            soft_not_found={"closest": ["rent.pdf"]},
        )
        failed = dict.fromkeys(["documents", "clarify", "figure", "soft_not_found"], False)
        for reply in ({}, {"status": "not_found", "sentences": [], "closest_documents": [{"id": "r"}]}):
            marks = nlq_score(question, reply, reply, NAMES, ASKER).passed()
            self.assertEqual(marks, {"clarify_turns": True, "no_total": True} | failed)
            self.assertIn(
                score(question, reply).outcome, (Outcome.MISSED_NOT_FOUND, Outcome.CORRECT_NOT_FOUND)
            )

    def test_a_question_back_is_clarified_and_scored_on_its_choices(self) -> None:
        asks = {
            "kind": "clarify",
            "status": "answered",
            "opening": "Good evening, Tanvi.",
            "clarify": {
                "question": "Which places?",
                "choices": [{"id": "c1", "label": "Maval"}, "Khalapur", "Panvel"],
            },
        }
        for kind in ("text", "not_found"):
            marked = score(nlq_question(kind=kind, value="45/2"), asks)
            self.assertEqual(
                (marked.outcome, category(marked), marked.answered), (Outcome.CLARIFIED, "abstained", False)
            )
        wanted = nlq_question(clarify={"choices": ["Maval", "Khalapur", "Panvel"], "pick": "Maval"})
        self.assertTrue(nlq_score(wanted, asks, example_3(), NAMES, ASKER).passed()["clarify"])
        more = nlq_question(clarify={"choices": ["Maval", "Thane"], "pick": "Maval"})
        self.assertFalse(nlq_score(more, asks, example_3(), NAMES, ASKER).passed()["clarify"])
        unneeded = nlq_score(nlq_question(clarify=None), asks, example_3(), NAMES, ASKER)
        self.assertFalse(unneeded.passed()["clarify"])
        self.assertTrue(
            nlq_score(nlq_question(clarify=None), example_3(), example_3(), NAMES, ASKER).passed()["clarify"]
        )
        self.assertFalse(
            nlq_score(wanted, asks, example_3(), NAMES, ASKER, clarify_turns=3).passed()["clarify_turns"]
        )

    def test_a_figure_needs_its_document_page_and_label(self) -> None:
        wanted = figure(2057) | {"document": "invoice-inv-2057.pdf"}
        for expected, right in (
            ({"label": "needs_review"}, True),
            ({"label": None}, True),
            ({"label": "verified"}, False),
            ({"page": 2}, False),
        ):
            question = nlq_question(figure=wanted | expected)
            self.assertIs(nlq_score(question, {}, example_3(), NAMES, ASKER).figure, right, expected)

    def test_the_soft_rejection_names_the_asker_and_its_closest_documents(self) -> None:
        question = nlq_question(
            "Show me the lease for the Nashik godown",
            kind="not_found",
            soft_not_found={"closest": ["rent.pdf", "licence.pdf"]},
        )
        soft = {
            "status": "not_found",
            "opening": "Sorry, Tanvi,",
            "sentences": [{"text": "I couldn't find exactly that, but these are the closest documents."}],
            "closest_documents": [{"id": "r"}, {"id": "l"}],
        }
        self.assertTrue(nlq_score(question, soft, soft, NAMES, ASKER).soft)
        self.assertFalse(nlq_score(question, soft, soft | {"opening": "Sorry,"}, NAMES, ASKER).soft)
        self.assertFalse(
            nlq_score(question, soft, soft | {"closest_documents": [{"id": "r"}]}, NAMES, ASKER).soft
        )
        bare = soft | {"sentences": [{"text": "Not found."}]}
        self.assertFalse(nlq_score(question, soft, bare, NAMES, ASKER).soft)

    def test_the_documents_listed_against_the_expected_and_the_unwanted(self) -> None:
        question = nlq_question(documents={"expected": [NAMES[f"d{n}"] for n in DAYS], "not": ["owale.pdf"]})
        self.assertEqual(nlq_score(question, {}, example_3(), NAMES, ASKER).documents, (8, 8, 8))
        extra = example_3(documents=[*example_3()["documents"][:7], {"id": "o"}])
        marked = nlq_score(question, {}, extra, NAMES, ASKER)
        self.assertEqual(
            (marked.documents, marked.unwanted, marked.passed()["documents"]), ((7, 8, 8), 1, False)
        )
        self.assertEqual(nlq_metrics([marked])["documents_recall"], (7, 8))
        self.assertEqual(
            worst_nlq([nlq_score(question, {}, example_3(), NAMES, ASKER), marked]).documents, (7, 8, 8)
        )

    def test_openings_name_the_asker_once_and_never_repeat(self) -> None:
        self.assertEqual(
            opening_problems(["Good evening, Tanvi.", "Sure.", None, "Here they are."], ASKER), []
        )
        self.assertEqual(
            opening_problems(["Hello.", "Sure.", "Sure.", " ".join(["word"] * 13)], ASKER),
            [
                "no name on the first reply",
                "reply 3 repeats an opening",
                "reply 4's opening is over 12 words",
            ],
        )

    def test_the_paired_gate_fails_an_expectation_newly_failed(self) -> None:
        def run(no_total: bool, figures: int) -> dict[str, dict[str, Any]]:
            marks = [{"no_total": no_total or i > 0, "figure": i >= figures} for i in range(4)]
            return {
                f"q{i}": {"outcome": "right", "verified": False, "expect": mark}
                for i, mark in enumerate(marks)
            }

        self.assertEqual(compare(run(True, 0), run(True, 2))[1], [])  # 2 figures newly failed: allowed
        self.assertEqual(compare(run(True, 0), run(True, 3))[1], ["3 questions newly fail figure, at most 2"])
        changed, failures, _ = compare(run(True, 0), run(False, 0))
        self.assertEqual(
            (changed, failures),
            (["q0: no_total passed -> failed"], ["1 questions newly fail no_total, at most 0"]),
        )


if __name__ == "__main__":
    unittest.main()
