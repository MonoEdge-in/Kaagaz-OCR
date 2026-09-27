"""Reciprocal rank fusion of the three search lists, without a database."""

import math

import pytest

from kaagaz.search.hybrid import RRF_K, fuse, vector_literal


def test_a_piece_found_two_ways_beats_the_top_of_one_list() -> None:
    fused = fuse(
        word=[("only-words", 0.9), ("both", 0.2)],
        fuzzy=[("only-fuzzy", 0.95), ("both", 0.4)],
        meaning=[],
    )
    assert [hit.piece_id for hit in fused] == ["both", "only-fuzzy", "only-words"]
    assert fused[0].score == pytest.approx(2 / (RRF_K + 2))
    assert fused[1].score == pytest.approx(1 / (RRF_K + 1))


def test_scores_add_one_over_k_plus_rank_per_list() -> None:
    fused = fuse(
        word=[("a", 3.0), ("b", 2.0), ("c", 1.0)],
        fuzzy=[("c", 0.7)],
        meaning=[("b", 0.8), ("c", 0.6)],
    )
    scores = {hit.piece_id: hit.score for hit in fused}
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61 + 1 / 62)
    assert [hit.piece_id for hit in fused] == ["c", "b", "a"]


def test_each_list_keeps_its_raw_score_in_its_own_field() -> None:
    fused = {hit.piece_id: hit for hit in fuse([("a", 0.1)], [("a", 0.5), ("b", 0.4)], [("b", 0.93)])}
    assert (fused["a"].word_score, fused["a"].fuzzy_score, fused["a"].meaning_score) == (0.1, 0.5, None)
    assert (fused["b"].word_score, fused["b"].fuzzy_score, fused["b"].meaning_score) == (None, 0.4, 0.93)


def test_ties_break_on_the_piece_id() -> None:
    fused = fuse(word=[("z", 1.0)], fuzzy=[("m", 1.0)], meaning=[("a", 1.0)])
    assert [hit.piece_id for hit in fused] == ["a", "m", "z"]


def test_a_repeat_in_one_list_counts_once_at_its_best_rank() -> None:
    fused = {hit.piece_id: hit for hit in fuse([("a", 0.9), ("b", 0.5), ("a", 0.1)], [], [])}
    assert fused["a"].score == pytest.approx(1 / 61)
    assert fused["a"].word_score == 0.9
    assert fused["b"].score == pytest.approx(1 / 62)


def test_k_changes_the_weight_of_rank() -> None:
    fused = fuse([("a", 1.0), ("b", 1.0)], [], [], k=0)
    assert [hit.score for hit in fused] == [1.0, 0.5]
    with pytest.raises(ValueError, match="k"):
        fuse([], [], [], k=-1)


def test_nothing_in_gives_nothing_out() -> None:
    assert fuse([], [], []) == []


def test_vector_literal_is_pgvector_text_and_refuses_non_numbers() -> None:
    assert vector_literal([0.5, -1.0, 2]) == "[0.5,-1.0,2.0]"
    for bad in ([], [math.nan], [math.inf, 1.0]):
        with pytest.raises(ValueError, match="vector"):
            vector_literal(bad)
