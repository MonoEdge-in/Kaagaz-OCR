"""The story eval (tests/golden/questions_story.json, hashed): true and unrelated
story and question pairs, Romanised, Devanagari and English. Words only: meaning is
NOT RUN here (no model), so MEANING_THRESHOLDS stays empty (OFF).

The gate (council 3 Oct 2026): no question ever gets "match" on a story that is not
its own; the review's probes (24 stories, 31 unrelated questions) are in the file.
The floors pin today's results (PROVISIONAL), so a change to the rules that finds
less fails here: 27 of 38 true questions get their story first as "match", 33 get
it first at all, and at most 16 of 31 unrelated ones get a "maybe". Both modes.

The probes (tests/golden/questions_story_probes.json, hashed; council 3 Oct 2026,
"story search mode"): recurring documents, twins a year or a month apart, where
the old rules gave 7 wrong "match" in 7 of 16 questions (0 now: at most one
"match"); and one-word questions, found 15 of 23 times by the side cards' rule and
22 of 23 by "Search my stories", with no unrelated word suggesting anything.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from kaagaz.db.document_stories import check_story
from kaagaz.search import story_match
from kaagaz.search.story_match import Candidate

EVAL = Path(__file__).parent / "golden" / "questions_story.json"
EVAL_SHA256 = "c59a17a28d8f68103e7209c58d5c982cb8187ac6c3ce6152608a51fa162c3299"
RAW: dict[str, Any] = json.loads(EVAL.read_text(encoding="utf-8"))
PROBES = Path(__file__).parent / "golden" / "questions_story_probes.json"
PROBES_SHA256 = "eb91624bb28ca61797852c278ba8b3b58fadf4c3f9c3078ca89720b74ef8c577"
MODES: tuple[tuple[story_match.Mode, int], ...] = (("cards", story_match.MATCHES_MAX), ("stories", 10))


def _stories(raw: dict[str, Any]) -> list[Candidate]:
    return [Candidate(s["id"], s["id"], None, check_story(s["story"]).latin, None) for s in raw["stories"]]


def test_the_file_is_the_hashed_one_and_well_formed() -> None:
    assert hashlib.sha256(EVAL.read_bytes()).hexdigest() == EVAL_SHA256
    ids = {story["id"] for story in RAW["stories"]}
    assert len(ids) == len(RAW["stories"]) == 24
    assert {query["script"] for query in RAW["queries"]} == {"romanised", "devanagari", "english"}
    assert all(query["story"] is None or query["story"] in ids for query in RAW["queries"])


@pytest.mark.parametrize(("mode", "most"), MODES)
def test_no_question_matches_a_story_not_its_own_and_the_floors_hold(
    mode: story_match.Mode, most: int
) -> None:
    stories = _stories(RAW)
    counts: Counter[str] = Counter()
    for query in RAW["queries"]:
        words = story_match.question_words(query["q"])
        found = story_match.rank(words, stories, threshold=None, mode=mode, most=most)
        own = query["story"]
        counts["wrong_match"] += sum(1 for m in found if m.band == "match" and m.document_id != own)
        counts["true" if own else "unrelated"] += 1
        counts["unrelated_suggested"] += own is None and bool(found)
        if own and found and found[0].document_id == own:
            counts[f"first_{found[0].band}"] += 1
    assert (counts["wrong_match"], counts["true"], counts["unrelated"]) == (0, 38, 31)
    assert counts["first_match"] >= 27
    assert counts["first_match"] + counts["first_maybe"] >= 33
    assert counts["unrelated_suggested"] <= 16


def test_the_probes_file_is_the_hashed_one() -> None:
    assert hashlib.sha256(PROBES.read_bytes()).hexdigest() == PROBES_SHA256


@pytest.mark.parametrize(("mode", "most"), MODES)
def test_recurring_documents_never_match_their_twin_and_one_word_floors(
    mode: story_match.Mode, most: int
) -> None:
    raw: dict[str, Any] = json.loads(PROBES.read_text(encoding="utf-8"))
    stories = _stories(raw)
    counts: Counter[str] = Counter()
    for query in raw["queries"]:
        words = story_match.question_words(query["q"])
        found = story_match.rank(words, stories, threshold=None, mode=mode, most=most)
        wrong = [m for m in found if m.band == "match" and m.document_id != query["match"]]
        counts["wrong_match"] += len(wrong)
        counts[query["kind"]] += 1
        if query["match"] and found and (found[0].document_id, found[0].band) == (query["match"], "match"):
            counts["own_match_first"] += 1
        if query["kind"] == "one_word":
            relevant = set(query["relevant"])
            counts["related" if relevant else "unrelated"] += 1
            counts["found"] += bool(relevant & {m.document_id for m in found})
            counts["noise"] += not relevant and bool(found)
    assert counts["wrong_match"] == 0
    assert (counts["recurring"], counts["related"], counts["unrelated"]) == (16, 23, 9)
    assert counts["own_match_first"] >= 11  # every recurring question with one story keeps its "match"
    assert counts["found"] >= (15 if mode == "cards" else 22)
    assert counts["noise"] == 0
