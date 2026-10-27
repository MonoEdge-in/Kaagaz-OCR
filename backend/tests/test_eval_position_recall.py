"""scripts/eval_position_recall.py (#381): the synthetic documents, the scoring, and a --fake run
through the real AnswerService with a scripted model (no network, no database)."""

import io
import re

from scripts.eval_position_recall import FACTS, POSITIONS, below, document, main


def test_each_document_plants_one_fact_near_the_start_in_the_middle_and_near_the_end() -> None:
    pages, planted = document(40_000, seed=1)

    assert len(pages) == 100 and [fact.position for fact in planted] == list(POSITIONS)
    assert [fact.page for fact in planted] == [5, 50, 95]
    for fact, (said, _, question) in zip(planted, FACTS, strict=True):
        assert said.format(code=fact.code) in pages[fact.page - 1] and fact.question == question
        assert sum(fact.code in text for text in pages) == 1  # on its own page only
    assert document(40_000, seed=1) == (pages, planted)  # the same every run


def test_below_names_each_row_whose_middle_recall_is_under_the_target() -> None:
    rows = {
        (20_000, "swift"): {"start": [True], "middle": [False, True], "end": [True]},
        (20_000, "precision"): {"start": [True], "middle": [True, True], "end": [False]},
    }
    assert below(rows, 0.9) == ["swift at 20000 tokens: middle recall 1/2"]
    assert below(rows, 0.5) == []


def test_the_fake_run_asks_every_fact_in_both_modes_and_finds_and_cites_each() -> None:
    out = io.StringIO()

    code = main(["--fake", "--sizes", "3000,40000", "--docs", "1"], out)

    lines = out.getvalue().splitlines()
    assert code == 0 and "BELOW TARGET" not in out.getvalue()
    rows = {tuple(line.split()[:2]): line.split()[2:] for line in lines[1:]}
    assert set(rows) == {(size, mode) for size in ("3000", "40000") for mode in ("swift", "precision")}
    assert all(cells == ["1/1", "1/1", "1/1"] for cells in rows.values()), out.getvalue()
    assert re.match(r"\s+tokens\s+mode\s+start\s+middle\s+end", lines[0])
