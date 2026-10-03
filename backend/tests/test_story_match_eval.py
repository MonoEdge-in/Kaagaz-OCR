"""The story eval (tests/golden/questions_story.json, hashed): true and unrelated
story and question pairs, Romanised, Devanagari and English. Words only: meaning is
NOT RUN here (no model), so MEANING_THRESHOLDS stays empty (OFF).

The gate (council 3 Oct 2026): no question ever gets "match" on a story that is not
its own; the review's probes (24 stories, 31 unrelated questions) are in the file.
The floors pin today's results (PROVISIONAL), so a change to the rules that finds
less fails here: 27 of 38 true questions get their story first as "match", 33 get
it first at all, and at most 16 of 31 unrelated ones get a "maybe".
"""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from kaagaz.db.document_stories import check_story
from kaagaz.search import story_match
from kaagaz.search.story_match import Candidate

EVAL = Path(__file__).parent / "golden" / "questions_story.json"
EVAL_SHA256 = "c59a17a28d8f68103e7209c58d5c982cb8187ac6c3ce6152608a51fa162c3299"
RAW: dict[str, Any] = json.loads(EVAL.read_text(encoding="utf-8"))


def test_the_file_is_the_hashed_one_and_well_formed() -> None:
    assert hashlib.sha256(EVAL.read_bytes()).hexdigest() == EVAL_SHA256
    ids = {story["id"] for story in RAW["stories"]}
    assert len(ids) == len(RAW["stories"]) == 24
    assert {query["script"] for query in RAW["queries"]} == {"romanised", "devanagari", "english"}
    assert all(query["story"] is None or query["story"] in ids for query in RAW["queries"])


def test_no_question_matches_a_story_not_its_own_and_the_floors_hold() -> None:
    stories = [Candidate(s["id"], s["id"], None, check_story(s["story"]).latin, None) for s in RAW["stories"]]
    counts: Counter[str] = Counter()
    for query in RAW["queries"]:
        found = story_match.rank(story_match.question_words(query["q"]), stories, threshold=None)
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
