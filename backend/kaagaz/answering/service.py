"""Answer one customer's question, with a source for every sentence.

The API calls ``AnswerService.answer(customer_id, question, scope=...)`` with the
chat's resolved scope (PRD 3 H2), and stores ``Answer.to_json()`` (the ``Answer``
type in frontend/lib/types.ts, field for field). A scope that matches nothing is
"not found" before any query or model call (edge case 1); otherwise every step
below (fixed queries, unchecked, search, neighbours) sees only documents in it.

A follow-up (PRD 3 H1) is first rewritten to stand on its own from the
conversation's last turns (kaagaz/answering/rewrite.py); the steps below then run
on the rewrite, and the answer carries it as ``rewritten_question``.

The steps (docs/architecture.md):

1. ``hinglish.normalise``: the question in plain English words and keywords.
2. ``dates.date_range``: the date range it names, from today in
   ``settings.timezone``.
3. ``intent.plan_fixed``: a fixed query, only when every part resolves.
4. **Fixed route**: run the query; the sentence is a template filled only
   from the query's result (numbers written the Indian way). Citations are
   the values used. No rows: not found. A highest or lowest resting on a value
   not verified, shown or only compared, says so and cites it; a total, and a party's
   sum, follow the numbers rule (PRD 6 C and C2, totals.py).
   **Unchecked**: no plan because no checked value holds the figure, but an
   unchecked card value does: no number, a calm line saying it is in these
   documents, and the documents where it appears (status ``unchecked``, answering/unchecked.py).
5. **Search route**: embed the question (skipped without a model), retrieve,
   the evidence gate on the hits, then the hits' neighbours (Q4) after them,
   then a draft (the model, or quoting without one), then ``verify`` drops
   every sentence its evidence's text does not back.
6. ``sanitize`` on every sentence and snippet. The label is verified only
   when every value or piece behind the answer is verified, and no sentence
   rests on neighbours alone (PROVISIONAL, plan decision 8: the gate never
   judged them). Nor is it verified when the gate passed only through the
   glossary's Marathi and Hindi words, or a sentence citing evidence in a script
   other than Latin was checked against nothing (``verify.unchecked_cross_script``;
   council 2 Oct 2026, PR2; any script, council 4 Oct 2026).
7. My words (council 3 Oct 2026, A3, PROVISIONAL): on the search route only, the
   asking user's own words the question uses (``expansion.expand``, read once inside
   ``Database.user``) are searched as OR alternatives and may count at the gate
   (``"personal"``). An answer that used one carries them as ``understood_words``
   and is never verified. They never reach ``normalise``, the fixed queries, the
   model's prompt or the sentences.
8. Translation (council 4 Oct 2026, PROVISIONAL; ANSWER_TRANSLATE_QUESTION, off by
   default): on the search route, an all-Latin question whose first hits are mostly in
   another script is translated into that script (``translate.py``) and searched again,
   the question and its translations fused (``TranslatedSearch``). The gate may then pass
   on any hit's score (``any_hit_scores``). The answer is never verified when the first
   search alone would not pass the gate, or a cited hit was found, or lifted over the
   score, by the translation only. The translation is never in the model's prompt, the
   answer or the logs. A question typed in another script is searched as asked.
9. Pages that couldn't be read (PRD 5 F6, ``hard_to_read.py``): a not-found answer, when a
   document in scope has an unreadable or uncertain page, says so and names the pages
   (``reading_note``, ``unreadable_pages``); a cited text that holds a word the reader
   was unsure of, or couldn't read, makes its citation ``hard_to_read`` and the answer
   needs review, with "Part of this text was hard to read." One more statement for each.
10. Context across pages (PRD 5 CX5, CX6, M2; the ``Context`` port, the PostgreSQL one
    with the PostgreSQL retriever, else only the one a test passes). With the model on,
    when the scope's pieces add up to ANSWER_WHOLE_DOC_TOKENS or fewer (0: never), the
    evidence is every piece in reading order, and the gate asks only that every number
    asked is in them (``holds_numbers``: the keyword rule would turn away an English
    question about a Hindi or Marathi document; the model and verify decide, and the label
    is judged per sentence as ever); no search, aspect, topic, section or neighbour.
    Otherwise the rewrite also lists up to QUERY_ASPECTS_MAX aspects (even with no
    history), each searched with the question and fused (``TranslatedSearch``, one
    batched embed); after the gate, the pieces of the topics the question, an aspect or a
    hit names (TOPIC_EXPAND_MAX_PIECES a document, never a hit, the nearest a hit first)
    and of the two best sections (a word match, or a summary at least
    ANSWER_MIN_EVIDENCE_SCORE near in meaning; TOPIC_EXPAND_MAX_PIECES a section) come
    after the hits, then the neighbours. Each document
    that supplies the model's evidence adds one card line, "AI summary, not evidence",
    with no id, so it is never cited. M4: the evidence is cut from its end (neighbours,
    then topic and section pieces, then hits), and the cards before any of it.
11. Memory (PRD 5 M3, kaagaz/answering/memory.py): the conversation's summary and memory
    go to the rewrite only; ``remember`` writes a new one.
12. Model choice and Ensemble (features 25-27, C7): the asker's ``ChatChoice`` names the
    draft's model(s) and thinking level. With more than one, each drafts from the same
    evidence at once (``_ensemble``) and the lead's draft is answered; the answer is
    verified only when today's rules say so and every other model's checked sentences
    hold the lead's numbers from the same sources (``agreement``). Agreement only ever
    adds Needs review.
13. Every document (Tushar, 16 Oct 2026, kaagaz/answering/exhaustive.py): a question that
    asks how many or which documents hold a word or phrase is answered before the fixed
    route from every document in scope, never the top hits: the exact count, the documents
    by name with their pages, each citing its first matching piece (route ``fixed_query``,
    no model). The count sentence cites nothing: it sums up the cited lines after it, so no
    sentence carries more than one number. No document holds it: the steps above answer as
    before.
14. Steady answers (16 Oct 2026): the evidence, the prompt, the schema and verify are the
    same whichever model drafts. The rewrite and aspects (which steer the search) and the
    translation are asked for at REWRITE_TEMPERATURE (rewrite.py), so what is searched does
    not change from one run to the next; the draft stays at the model's default (Google's
    guidance for Gemini 3).
15. Most relevant picks (16 Oct 2026, kaagaz/answering/pins.py): on the search route, after
    the gate, a hit on a page the asker picked earlier (this conversation, or another on the
    same words) moves up a few places, only when it passes the gate by itself. It never adds
    a piece or changes whether there is an answer; the timing line counts the hits moved.
16. Also mentions (17 Oct 2026, ``_mentioned``): after an answered or not-found answer (not
    a count, not while the model is down), the other documents in scope holding the
    question's key terms (Jalna, जालना) as whole words, from one word-index statement; the
    answer's text, label and verify never see them. A not-found answer also names the key terms
    no document in scope holds (#410, ``_missing``: one such statement a term).
17. Owners and places (PRD 6 E, kaagaz/answering/owners.py): a question naming people, places or a
    property the company's land values hold, with an owning or share word (or a property with a map
    word: its map values and neighbours as printed), is answered first, from
    fixed queries over ``value_keys``: code-built sentences and one ``documents`` line a record
    (answer contract v2), every figure a cited value as printed. So is an open list naming nothing
    ("all owners", "every survey number", #381): every record in scope, never the top hits; and
    step 4's total may be bounded ("the total of invoices over ₹50,000", ``intent.bound_asked``).
18. Clarify (PRD 6 B1, clarify.py): with the model and a vocabulary port (as ``context``), the
    rewrite call runs on every question and gives the plan; an unclear name is asked (status
    ``clarify``), a resolved one put in the question. ``pending``: a stored clarify answered,
    with no second rewrite. No plan: every step above as before.
19. Ask speed (24 Oct 2026): a first question's rewrite and plan call (no history, no memory)
    ends within ANSWER_PLAN_TIMEOUT_SECONDS, else it is answered with no plan or aspects (code
    ``plan_timeout``); a follow-up's keeps the model's timeout, its search needs the rewrite.
    Without whole documents, the first question's words, embedding and search (no aspects) run
    beside that call (``_overlapped``), each database step in its own transaction; they are used
    only when the call asks no clarify, leaves the question as it was and lists no aspects, so
    the answer is the one the steps above give one after another. A call that would be for the
    plan alone is not made when the scope's vocabulary has no key (``Vocabulary.any_keys``):
    with no name to resolve or offer, step 18 could change nothing.
20. Nothing lost in the middle (#381, 27 Oct 2026): a model reads the middle of a long prompt
    least closely. Every draft's evidence goes in best first and the prompt lays it out with the
    best at its start and its end, the weakest in the middle (``draft.edges``); a whole scope's
    pieces (step 10) are first ranked by the question's own search (``_ranked``: one embedding,
    one search), never left in reading order. Each piece keeps its number, so every citation
    points at the same piece, page and spot. A Precision answer (high thinking) whose evidence is
    more than ANSWER_PARTS_TOKENS is read in at most ANSWER_PARTS_MAX parts at once, each asked
    for the facts that answer the question (answering/parts.py), and drafted from the pieces
    they name only; none named is not found, and a part that fails leaves the draft to all the
    evidence in one call, as before (``_read_in_parts``). The timing line's ``parts``: how many.
21. Streaming Ask (#535, answering/stream.py): when ``POST /api/chat/stream`` asks, the answer
    sends its real stages, the ``sources`` the draft reads (before the draft), and one model's draft
    streamed. Each of its sentences is sent once it passes the checks the answer will run on it
    (``_Preview``: the draft's shape, ``verify``, then decision 2's final check), with the answer's
    own citations. Everything above still runs in full after it, and that answer is the truth. An
    Ensemble's or a quoted draft sends no sentence. The timing line's ``streamed`` and
    ``first_sentence_ms``.

No source, no answer: an answered result always has at least one sentence,
every sentence cites at least one source (but step 13's count, which sums up its cited
lines), and every source has a region.

Nothing here logs the question or any document text: a model error is logged
as its code, and each model answer as the model's tag and ANSWER_PROMPT_HASH.
Every not-found answer logs one line, "answer not found", with a fixed reason
code only (``_not_found``'s ``reason``). Every answer also logs one INFO line on its own
logger, ``kaagaz.answering.timing`` (ask precision PR 1a, D): the route, the status, the
not-found reason or the label, whole 0/1, the aspect count, and the milliseconds of each
stage (rewrite, embed, search, widen, draft, verify; time.monotonic, wall time on the
answer's thread, adding up to the total), of the model limiter's waits (``LIMITER_WAIT``,
summed over parallel drafts and step 19's overlap) and in all. ``overlap`` is 1 when step
19's early search was used; ``overlap_ms``, the time it took, used or not, inside
``rewrite_ms``; ``redrafts``, the draft's model calls made again for a sentence too long to cut
(#535, draft.py). Codes and numbers only.

Time: one ``available_fields`` query only for a question with an aggregate
word and no unknown topic word, then either one fixed query or one search
(then one neighbours statement for the usable hits) plus at most one model
call (one per agent with Ensemble, at most three, in parallel; step 20's parts add at most
ANSWER_PARTS_MAX, in parallel, before it); the rest is
O(e * t) over e pieces of evidence of t characters. e is at most
ANSWER_MAX_EVIDENCE hits plus 2 * ANSWER_NEIGHBOURS neighbours per hit.
"""

import copy
import logging
import re
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from functools import partial, wraps
from itertools import zip_longest
from typing import Any, Literal
from zoneinfo import ZoneInfo

from kaagaz.ai.client import (
    ModelClient,
    ModelOutputError,
    ModelUnavailable,
    estimate_tokens_cautiously,
    input_token_budget,
)
from kaagaz.ai.openrouter import LIMITER_WAIT
from kaagaz.ai.usage import billed_as
from kaagaz.answering import glossary, stream, voice
from kaagaz.answering.clarify import (
    Pending,
    PgVocabulary,
    Step,
    Vocabulary,
    after_reply,
    first_step,
    refers_back,
)
from kaagaz.answering.dates import date_range, describe_range, mentions_date
from kaagaz.answering.draft import (
    ANSWER_PROMPT_HASH,
    SYSTEM_PROMPT,
    Draft,
    DraftSentence,
    card_line,
    checked,
    extractive_draft,
    fit_cards,
    fit_evidence,
    model_draft,
)
from kaagaz.answering.evidence import (
    CAPPED,
    INSTRUCTION_NOTE,
    Context,
    Evidence,
    Gate,
    Hit,
    Lists,
    PgRetriever,
    Retriever,
    Translated,
    TranslatedSearch,
    any_hit_scores,
    closest_documents,
    enough_evidence,
    has_word,
    holds_numbers,
    looks_like_instructions,
    number_evidence,
    numbers_asked,
    reads_like_instructions,
    related_documents,
    valid_regions,
    word_text,
)
from kaagaz.answering.exhaustive import (
    MAX_LISTED,
    MAX_MENTIONED,
    Exhaustive,
    Mentions,
    PgExhaustive,
    asks_documents,
    count_sentence,
    document_sentence,
    exhaustive_query,
    key_terms,
    more_text,
    pages_with,
)
from kaagaz.answering.expansion import NO_EXPANSION, Expansion, PgWords, WordSource, expand
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, FixedQueries, FixedRow, PgFixedQueries, ValueRef
from kaagaz.answering.focus import (
    PgFocus,
    Profile,
    about_focus,
    asks_other,
    asks_related,
    names_subject,
    points_back,
    related,
    subject,
    understood,
)
from kaagaz.answering.focus import parts as split_parts
from kaagaz.answering.format import indian_number
from kaagaz.answering.hard_to_read import (
    HARD_TO_READ_NOTE,
    FlaggedPage,
    PgReadingQuality,
    ReadingQuality,
    WordMark,
    hard_to_read,
    not_found_note,
)
from kaagaz.answering.hinglish import NormalisedQuestion, normalise
from kaagaz.answering.intent import FixedPlan, bound_asked, metric_asked, plan_fixed
from kaagaz.answering.memory import NO_MEMORY, Memory, allowed_tokens, prompt_text, summarise
from kaagaz.answering.owners import PgOwners, owner_ask
from kaagaz.answering.owners import reply as owners_reply
from kaagaz.answering.parts import PARTS_PROMPT_HASH, read_part, split
from kaagaz.answering.pins import Pin, lifts, pinned_first
from kaagaz.answering.rewrite import MAX_QUESTION, Rewritten, Turn, rewrite_and_plan
from kaagaz.answering.rewrite import MAX_REPLY_TOKENS as REWRITE_REPLY_TOKENS
from kaagaz.answering.rewrite import SYSTEM_PROMPT as REWRITE_PROMPT
from kaagaz.answering.sanitize import clean_snippet, clean_text
from kaagaz.answering.stream import Events, StreamingModel, draft_streamed
from kaagaz.answering.totals import tell
from kaagaz.answering.translate import target_scripts, translate
from kaagaz.answering.unchecked import PgUncheckedValues, UncheckedValues, unchecked_sentence
from kaagaz.answering.verify import numbers_in, unchecked_cross_script, verify
from kaagaz.chunking.script import ascii_digits, has_other_script_letters, spaced_words
from kaagaz.db.engine import Database
from kaagaz.db.models import Agent, ChatChoice
from kaagaz.search.hybrid import MAX_LIMIT
from kaagaz.search.indexing import recipe_tag
from kaagaz.search.scope import EVERYTHING, Scope
from kaagaz.settings import Settings

_log = logging.getLogger(__name__)
# Its own logger: tests pin kaagaz.answering.service's INFO lines (the module docstring).
_timing_log = logging.getLogger("kaagaz.answering.timing")
_STAGES = ("rewrite", "embed", "search", "widen", "draft", "verify")

# "unchecked": the figure asked for exists only as unchecked card values, so no
# number is given (kaagaz/answering/unchecked.py).
Status = Literal["answered", "not_found", "unchecked", "clarify"]
Route = Literal["fixed_query", "search"]
_MAX_NAME = 100  # a party name longer than this is cut in the sentence
_REPLY_TOKENS = 1024  # PROVISIONAL: a draft's reply budget (num_predict), kept free of evidence
_FENCE_TOKENS = 10  # an evidence tag and its line breaks
# KZ-006: a draft that failed for one of these came from a model that was up (a reply too
# long, malformed or refused, or no room for the evidence), so its not found is no outage.
_REPLY_FAULTS = frozenset(
    {"evidence_too_large", "output_truncated", "invalid_output", "blocked", "answer_shape"}
)
# Features 25-27, C7 M9: an Ensemble answer's one deadline is MODEL_TIMEOUT_SECONDS and this
# much more from when its drafts start, for every agent's call and repair.
ENSEMBLE_SLACK_SECONDS = 10.0
PART_WORKERS = 2  # step 22: parts answered at once, each holding at most one pooled connection


@dataclass(frozen=True, slots=True)
class Citation:
    n: int  # what sentences refer to, from 1
    kind: Literal["piece", "value"]
    ref_id: str
    document_id: str
    document_name: str
    detected_type: str
    page_no: int  # the page the source card opens: its first region's
    regions: list[dict[str, Any]]  # grouped by page, in page order (H4)
    snippet: str
    label: str
    page_from: int  # H4: the pages the source covers ("Pages 15 to 16")
    page_to: int
    label_page_no: int | None  # H4: where a value's label is, when it was located
    label_regions: list[dict[str, Any]]
    hard_to_read: bool = False  # PRD 5 F6: the cited text holds an unsure or illegible word
    # Ranked sources (Tushar, 16 Oct 2026): the hit's place in the search's fused order (1 is
    # the most relevant); None where there is no such order (values, a whole document).
    rank: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "kind": self.kind,
            "ref_id": self.ref_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "detected_type": self.detected_type,
            "page_no": self.page_no,
            "regions": [dict(region) for region in self.regions],
            "snippet": self.snippet,
            "label": self.label,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "label_page_no": self.label_page_no,
            "label_regions": [dict(region) for region in self.label_regions],
            "hard_to_read": self.hard_to_read,
            "rank": self.rank,
        }


@dataclass(frozen=True, slots=True)
class AnswerSentence:
    text: str
    citations: list[int]


@dataclass(frozen=True, slots=True)
class Team:
    """Who drafted an answer the asker's choice named (features 25-27, C7): the names
    and ids of the agents that replied, the one whose draft was used first; how many
    were asked; whether the others confirmed it ("agreed", "not_confirmed", or None with
    one agent); the thinking level."""

    agents: tuple[str, ...]
    agent_ids: tuple[str, ...]
    asked: int
    agreement: Literal["agreed", "not_confirmed"] | None
    thinking: str


@dataclass(frozen=True, slots=True)
class Answer:
    status: Status
    label: str | None  # None when not found
    sentences: list[AnswerSentence]
    citations: list[Citation]
    closest_documents: list[dict[str, Any]]  # {"id", "display_name"}, and "words" (never in to_json)
    route: Route
    normalized_question: str
    model_used: bool
    rewritten_question: str | None = None  # H1: what was searched for, when a follow-up was rewritten
    understood_words: tuple[tuple[str, str, str], ...] = ()  # (word, meaning, personal or organization)
    # PRD 5 F6: the note on pages that couldn't be read, the pages it names, and how many in all.
    reading_note: str | None = None
    unreadable_pages: tuple[FlaggedPage, ...] = ()
    unreadable_page_count: int = 0
    team: Team | None = None  # features 25-27: who drafted it, when the asker's choice did
    # "Also in" (Tushar, 16 Oct 2026): documents the search found but the answer does not
    # cite (evidence.related_documents).
    related_documents: list[dict[str, Any]] = field(default_factory=list)
    # "Also mentions" (Tushar, 17 Oct 2026): {"terms", "total", "documents"}, other documents in
    # scope holding the question's key terms (``AnswerService._mentioned``); in to_json only
    # when there is one, so answers without it keep their shape.
    mentions: dict[str, Any] | None = None
    # PRD 6 A, answer contract v2 (``_shaped``): the documents listed under the answer, each
    # {document_id, title, why, citations}, and what was assumed. The opening is the API's.
    documents: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    # KZ-006: not found while the model was down, so "not found" may be wrong; the route
    # answers 503 instead. Never in to_json.
    unavailable: bool = False
    # Decision 2's final check (``_checked``), never in to_json: the full text behind each
    # citation (its snippet is cut), and the numbers a fixed query gave a code-written sentence.
    backing: dict[int, str] = field(default_factory=dict)
    vouched: frozenset[str] = frozenset()
    # PRD 6 B1 (answer contract v2): the question to ask instead of an answer, with what
    # answering it needs (kept on the server), and what was assumed; in to_json only when set.
    clarify: dict[str, Any] | None = None
    resume: dict[str, Any] | None = None
    # #410: a not-found answer's key terms that no document in scope holds (``_missing``); in
    # to_json only when there is one, so answers without it keep their shape.
    missing_words: tuple[str, ...] = ()
    # #535: the model was down, or the question's (or an aspect's) vector missing, for this answer,
    # so a repeated question never reuses it (routers/chat.py). Never in to_json.
    degraded: bool = False
    # #535 (test-server log, 7 Nov 2026): "timeout" when the outage is a draft that took too long,
    # so the words say what to do instead of "ask again in a minute"; in to_json only then.
    outage_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The ``Answer`` of frontend/lib/types.ts, exactly; ``agents``, ``agent_ids``,
        ``agents_asked``, ``agreement`` and ``thinking`` only with a ``team``. ``opening`` is
        None here: only the API knows the conversation and the asker (routers/chat.py)."""
        team = (
            {}
            if self.team is None
            else {
                "agents": list(self.team.agents),
                "agent_ids": list(self.team.agent_ids),
                "agents_asked": self.team.asked,
                "agreement": self.team.agreement,
                "thinking": self.team.thinking,
            }
        )
        return {
            "version": 2,
            "status": self.status,
            "label": self.label,
            "opening": None,
            "documents": [dict(document) for document in self.documents],
            "assumptions": list(self.assumptions),
            "sentences": [{"text": s.text, "citations": list(s.citations)} for s in self.sentences],
            "citations": [citation.to_json() for citation in self.citations],
            "closest_documents": [
                {"id": doc["id"], "display_name": doc["display_name"]} for doc in self.closest_documents
            ],
            "route": self.route,
            "normalized_question": self.normalized_question,
            "model_used": self.model_used,
            "rewritten_question": self.rewritten_question,
            "understood_words": [
                {"word": w, "meaning": m, **({"origin": origin} if origin != "personal" else {})}
                for w, m, origin in self.understood_words
            ],
            "reading_note": self.reading_note,
            "unreadable_pages": [page.to_json() for page in self.unreadable_pages],
            "unreadable_page_count": self.unreadable_page_count,
            "related_documents": [dict(doc) for doc in self.related_documents],
            **team,
            **({} if self.mentions is None else {"mentions": self.mentions}),
            **({"missing_words": list(self.missing_words)} if self.missing_words else {}),
            **({"clarify": self.clarify, "resume": self.resume} if self.clarify else {}),
            # KZ-006 (24 Oct 2026): the model was down, so this not found is no answer either way.
            **({"outage": True} if self.unavailable else {}),
            **({"outage_reason": self.outage_reason} if self.unavailable and self.outage_reason else {}),
        }


@dataclass(slots=True)
class _Timing:
    """One answer's stage times for the timing line (the module docstring): ``lap`` adds
    the time since the last lap to a stage."""

    start: float
    last: float
    ms: dict[str, float] = field(default_factory=dict)
    whole: bool = False
    aspects: int = 0
    reason: str | None = None  # the not-found reason code
    pinned: int = 0  # hits a Most relevant pick moved up (answering/pins.py)
    degraded: bool = False  # the model was down for the question's vector or its draft (KZ-006)
    overlap: bool = False  # step 19's early search was used
    overlap_ms: float = 0.0  # the time it took, beside the plan call
    parts: int = 0  # step 20: the parts the evidence was read in (0: one call, as before)
    redrafts: int = 0  # #535: model calls made again for a sentence too long to cut (draft.py)
    streamed: bool = False  # step 21: asked through POST /api/chat/stream
    first_sentence_ms: float = 0.0  # step 21: when its first checked sentence was sent (0: none)
    vectorless: bool = False  # #535: the model on, the question searched without a meaning vector
    outage: str | None = None  # #535: the code the draft failed with while the model was down

    def lap(self, stage: str) -> None:
        now = time.monotonic()
        self.ms[stage] = self.ms.get(stage, 0.0) + (now - self.last) * 1000
        self.last = now


_TIMING: ContextVar[_Timing | None] = ContextVar("answer_timing", default=None)
# Step 22: the answer's one deadline, on time.monotonic(), that a part's draft must end by (None: its own).
_PART_DEADLINE: ContextVar[float | None] = ContextVar("part_deadline", default=None)


@dataclass(frozen=True, slots=True)
class _Early:
    """Step 19's early search (``AnswerService._early``): the asker's words, the question's
    vector and hits, whether the model was down for the vector (KZ-006), and how long it took;
    ``lists``, the question's own lists when aspects may come, to fuse with theirs (#535)."""

    expansion: Expansion
    vector: list[float] | None
    hits: Sequence[Hit]
    degraded: bool
    ms: float
    lists: Lists | None = None


def _timing() -> _Timing:
    """The current answer's timing; a throwaway one outside ``AnswerService.answer``."""
    return _TIMING.get() or _Timing(time.monotonic(), time.monotonic())


def _timed[**P](answer: Callable[P, Answer]) -> Callable[P, Answer]:
    """``answer`` with the timing line logged after it. The last stretch is "verify"
    after a draft, else "search" (a fixed query, or the gate's not found). An answer that raised
    still writes it (review of #535), its route and status "-" and its outcome the error's code
    (``answer_timeout`` for the stream's cap) or type."""

    @wraps(answer)
    def timed(*args: P.args, **kwargs: P.kwargs) -> Answer:
        now = time.monotonic()
        timing = _Timing(now, now, streamed=stream.EVENTS.get() is not None)
        token, waits = _TIMING.set(timing), LIMITER_WAIT.set([0.0])
        result: Answer | None = None
        failed = "-"
        try:
            result = answer(*args, **kwargs)
            return result
        except Exception as error:
            failed = str(getattr(error, "code", None) or type(error).__name__)
            raise
        finally:
            timing.lap("verify" if "draft" in timing.ms else "search")
            waited = LIMITER_WAIT.get() or [0.0]
            _TIMING.reset(token)
            LIMITER_WAIT.reset(waits)
            _log_timing(timing, waited[0], result, failed)

    return timed


def _log_timing(timing: _Timing, waited: float, result: Answer | None, failed: str) -> None:
    """The timing line (the module docstring); ``failed``: the outcome of an answer that raised."""
    _timing_log.info(
        "answer timing route=%s status=%s outcome=%s whole=%d aspects=%d pinned=%d overlap=%d %s"
        " overlap_ms=%.0f limiter_wait_ms=%.0f total_ms=%.0f parts=%d redrafts=%d"
        " streamed=%d first_sentence_ms=%.0f",
        "-" if result is None else result.route,
        "-" if result is None else result.status,
        failed if result is None else timing.reason or result.label,
        timing.whole,
        timing.aspects,
        timing.pinned,
        timing.overlap,
        " ".join(f"{stage}_ms={timing.ms.get(stage, 0.0):.0f}" for stage in _STAGES),
        timing.overlap_ms,
        waited * 1000,
        (timing.last - timing.start) * 1000,
        timing.parts,
        timing.redrafts,
        timing.streamed,
        timing.first_sentence_ms,
    )


class AnswerService:
    """``retriever``, ``translated_search``, ``fixed_queries``, ``unchecked`` and ``words``
    default to the PostgreSQL ones; tests pass fakes. ``translator``: the client the question
    is translated with (its own timeout), used only with ANSWER_TRANSLATE_QUESTION on.
    ``reading``: the pages that couldn't be read and the word marks (F6); the PostgreSQL
    one with the PostgreSQL retriever, else only the one a test passes, as
    ``translated_search``; so is ``context``, PRD 5's whole documents, topics, sections
    and cards (step 10 of the module docstring)."""

    def __init__(
        self,
        *,
        db: Database,
        model: ModelClient | None,
        settings: Settings,
        clock: Callable[[], datetime],
        retriever: Retriever | None = None,
        fixed_queries: FixedQueries | None = None,
        unchecked: UncheckedValues | None = None,
        words: WordSource | None = None,
        translator: ModelClient | None = None,
        translated_search: TranslatedSearch | None = None,
        reading: ReadingQuality | None = None,
        context: Context | None = None,
        exhaustive: Exhaustive | None = None,
        mentions: Mentions | None = None,
        vocabulary: Vocabulary | None = None,
        focus: PgFocus | None = None,
    ) -> None:
        self._model = model
        self._translator = translator if settings.answer_translate_question else None
        self._settings = settings
        self._clock = clock
        self._zone = ZoneInfo(settings.timezone)
        # What the answer's evidence may take: the window (M1) less the reply, its repair and the prompt.
        self._evidence_budget = input_token_budget(
            num_ctx=settings.model_context_tokens,
            max_tokens=settings.model_answer_max_tokens,
            system=SYSTEM_PROMPT,
        )
        self._rewrite_budget = input_token_budget(
            num_ctx=settings.model_context_tokens, max_tokens=REWRITE_REPLY_TOKENS, system=REWRITE_PROMPT
        )
        pg = PgRetriever(
            db,
            recipe=recipe_tag(settings.model_embed),
            ef_search=settings.search_ef,
            neighbours=settings.answer_neighbours,
            topic_pieces=settings.topic_expand_max_pieces,
            min_score=settings.answer_min_evidence_score,
            max_scan_tuples=settings.search_max_scan_tuples,
            exact_below=settings.search_exact_below,
        )
        self._retriever: Retriever = pg if retriever is None else retriever
        self._translated: TranslatedSearch | None = pg if retriever is None else translated_search
        self._fixed = fixed_queries if fixed_queries is not None else PgFixedQueries(db)
        self._unchecked = unchecked if unchecked is not None else PgUncheckedValues(db)
        self._words = words if words is not None else PgWords(db)
        self._reading: ReadingQuality | None = PgReadingQuality(db) if retriever is None else reading
        self._context: Context | None = pg if retriever is None else context
        self._exhaustive: Exhaustive | None = PgExhaustive(db) if retriever is None else exhaustive
        self._mentions: Mentions | None = PgExhaustive(db) if retriever is None else mentions
        self._owners = PgOwners(db) if retriever is None else None
        self._vocabulary: Vocabulary | None = PgVocabulary(db) if retriever is None else vocabulary
        self._focus: PgFocus | None = PgFocus(db) if retriever is None else focus  # package K

    @_timed
    def answer(
        self,
        customer_id: str,
        question: str,
        *,
        scope: Scope = EVERYTHING,
        history: Sequence[Turn] = (),
        user_id: str | None = None,
        memory: Memory = NO_MEMORY,
        choice: ChatChoice | None = None,
        pins: Sequence[Pin] = (),
        pending: Pending | None = None,
        clarify: bool = True,
        cited: Sequence[str] = (),
        last_cited: Sequence[str] = (),
    ) -> Answer:
        """Answer ``question`` for ``customer_id`` only, from documents inside ``scope``;
        ``history`` is the conversation's earlier turns, oldest first; ``user_id``, the
        asker, whose own words (only) may help the search and as whom every transaction of the
        answer runs, on every thread (PRD 7, ``Database.user``; None: nobody's, on the
        organisation, for tests and tools); ``memory``, the
        conversation's (M3), for the rewrite only; ``choice``, who drafts the answer
        (``kaagaz.db.models.resolve_chat``, features 25-27; None: MODEL_CHAT at low, as
        before). Only the draft uses it: the rewrite, translation and memory stay on
        MODEL_CHAT. ``pins``: the asker's Most relevant picks that may move a hit up
        (answering/pins.py; ``kaagaz.db.chat.pins_for``). A ``choice`` with no agent
        (``models.NO_MODEL``: the asker's Ask setting is None, 0038) answers as with the model
        off: no model call at all, the lines found quoted. ``pending``: the stored clarify
        this answers (step 18, ``clarify.reply_to``), whose question is answered instead;
        ``clarify`` False (the ``nlq_clarify`` switch, 0052): no plan, so no clarify. ``cited``:
        the documents the conversation's earlier answers cite, newest turn first (a missing
        subject's choices, ``clarify.first_step``). ``last_cited``: the documents the last answer
        cited, which the asker can still see (#629): when "this" points back at one, its property
        is never asked."""
        if choice is not None and not choice.agents:
            keyless = copy.copy(self)  # shallow: the same ports, without the model clients
            keyless._model = keyless._translator = None
            return keyless._respond(
                customer_id,
                question,
                scope,
                history,
                user_id,
                memory,
                None,
                pins,
                pending,
                clarify,
                cited,
                last_cited,
            )
        return self._respond(
            customer_id,
            question,
            scope,
            history,
            user_id,
            memory,
            choice,
            pins,
            pending,
            clarify,
            cited,
            last_cited,
        )

    def _respond(
        self,
        customer_id: str,
        question: str,
        scope: Scope,
        history: Sequence[Turn],
        user_id: str | None,
        memory: Memory,
        choice: ChatChoice | None,
        pins: Sequence[Pin],
        pending: Pending | None,
        clarify: bool = True,
        cited: Sequence[str] = (),
        last_cited: Sequence[str] = (),
    ) -> Answer:
        """``answer``'s work, timed by it."""
        stream.emit("stage", {"stage": "understanding"})  # step 21: the rewrite and plan, when any
        # #629: a property pick searches its own documents (a pick of another kind, every one).
        if pending is not None and pending.kind == "property" and pending.documents:
            inside = scope.document_ids
            picked = (
                pending.documents if inside is None else tuple(d for d in pending.documents if d in inside)
            )
            scope = replace(scope, document_ids=picked)
        if scope.matches_nothing:
            return _not_found("search", question.strip(), [], model_used=False, reason="notfound_empty_scope")
        whole = self._whole(customer_id, user_id, scope, self._input_cap(choice))
        _timing().lap("search")
        # Step 18: the names of the scope's documents only, when it names them.
        vocabulary = None if self._vocabulary is None else self._vocabulary.within(scope.document_ids)
        if pending is not None:  # step 18: its picks checked again, then the names left; no rewrite
            step = after_reply(vocabulary, customer_id, user_id, pending)
            return self._step_answer(
                customer_id, step, pending.question, scope, user_id, whole, (), choice, pins
            )
        # Step 22 (package K): the focus "this" points at, and a question of several asks in parts.
        profile = self._profile(customer_id, user_id, question, last_cited)
        asked, total = split_parts(question)
        if len(asked) > 1:
            return self._parts_answer(
                customer_id, question, asked, total, scope, user_id, whole, choice, pins, profile
            )
        turns = self._settings.chat_history_turns
        recent = history[-turns:] if turns else ()
        remembered = prompt_text(memory)
        done = Rewritten(None, (), None)
        early: Future[_Early] | None = None
        # CX6: the aspects are asked for, even on a first question, when they can be searched.
        most = self._settings.query_aspects_max if whole is None and self._widens() else 0
        planned = clarify and self._vocabulary is not None  # step 18: the plan, on every question
        alone = self._model is not None and planned and not (recent or remembered or most)
        if alone and vocabulary is not None and not vocabulary.any_keys(customer_id, user_id=user_id):
            planned = False  # step 19: a call for the plan alone, with no name it could check or offer
        if self._model is not None and (recent or remembered or most or planned):
            first = not (recent or remembered)  # step 19: a first question's call has its deadline
            plan_time = self._settings.answer_plan_timeout_seconds
            rewriting = partial(
                rewrite_and_plan,
                self._model,
                question,
                recent,
                memory=remembered,
                allowed=allowed_tokens(memory),
                aspects_max=most,
                budget=self._rewrite_budget,
                plan=planned,
                deadline=time.monotonic() + plan_time if first else None,
            )
            if first and whole is None:
                done, early = self._overlapped(rewriting, customer_id, question, scope, user_id, most > 0)
            else:
                done = rewriting()
            if not (recent or remembered or most):
                done = Rewritten(None, (), done.plan)  # called for the plan alone
        timing = _timing()
        timing.lap("rewrite")
        timing.whole, timing.aspects = whole is not None, len(done.aspects)
        step = Step(done.question or question, ())
        narrowed = replace(scope, timezone=EVERYTHING.timezone) != EVERYTHING  # any filter at all
        if done.plan is not None and vocabulary is not None:
            last = last_cited if refers_back(question) else ()
            step = first_step(
                vocabulary, customer_id, user_id, step.question, done.plan, narrowed, cited, last
            )
        step, exclude = _focused(step, question, profile, scope)
        kept = early if step.question == question else None  # step 19: searched the question as asked
        return self._step_answer(
            customer_id,
            step,
            question,
            scope,
            user_id,
            whole,
            done.aspects,
            choice,
            pins,
            kept,
            profile,
            exclude,
        )

    def _profile(
        self, customer_id: str, user_id: str | None, question: str, last_cited: Sequence[str]
    ) -> Profile | None:
        """Step 22: the focus's profile (focus.py), the first of ``last_cited`` (the router's: still
        live), only for a question that points back or asks for related documents; one statement."""
        if self._focus is None or not last_cited or not (points_back(question) or asks_related(question)):
            return None
        return self._focus.profile(customer_id, user_id, last_cited[0])

    def _parts_answer(
        self,
        customer_id: str,
        question: str,
        asked: Sequence[str],
        total: int,
        scope: Scope,
        user_id: str | None,
        whole: Sequence[Hit] | None,
        choice: ChatChoice | None,
        pins: Sequence[Pin],
        profile: Profile | None,
    ) -> Answer:
        """Step 22: each of ``asked`` (``focus.parts``) answered as a question of its own (no rewrite,
        no clarify; the focus put in as ``_focused`` puts it; a part that points back with no focus,
        at the part before it), at most PART_WORKERS at once (an answer holds at most two pooled
        connections, routers/chat.py), each in a copy of the context with its own timing and no
        streamed sentence. A part not started by the answer's one deadline (MODEL_TIMEOUT_SECONDS +
        ENSEMBLE_SLACK_SECONDS, as ``_ensemble``) is not answered, and a part's draft must end by it
        (``_PART_DEADLINE``). Parts draft with the choice's lead model only, never an Ensemble (cost).
        A part that points back at the part before it, which names its own property or person ("Who
        owns Gat 12? What is its area?"), is searched with that part, never the focus. Each part's
        stage times are added to the answer's (two at once, so they may add up to more than its
        total). Then ``_together``."""
        deadline = time.monotonic() + self._settings.model_timeout_seconds + ENSEMBLE_SLACK_SECONDS
        lead = (
            choice if choice is None or len(choice.agents) <= 1 else replace(choice, agents=choice.agents[:1])
        )
        stream.emit("stage", {"stage": "searching"})  # step 21: the parts' lookups start here

        def one(index: int, part: str) -> tuple[Answer, _Timing] | None:
            if time.monotonic() > deadline:
                return None
            start = time.monotonic()
            _TIMING.set(_Timing(start, start))  # this thread's copy of the context only
            _PART_DEADLINE.set(deadline)
            stream.EVENTS.set(None)
            before = asked[index - 1] if index else ""
            if (
                before
                and points_back(part)
                and not names_subject(part)
                and (profile is None or names_subject(before))
            ):
                step, exclude = Step(f"{part} ({before})", ()), None
            else:
                step, exclude = _focused(Step(part, ()), part, profile, scope)
            answer = self._step_answer(
                customer_id, step, part, scope, user_id, whole, (), lead, pins, None, profile, exclude
            )
            return answer, _timing()

        with ThreadPoolExecutor(max_workers=PART_WORKERS) as pool:  # leaving it joins every thread
            futures = [pool.submit(copy_context().run, one, n, part) for n, part in enumerate(asked)]
        results = [future.result() for future in futures]
        timing = _timing()
        for result in results:
            if result is not None:
                timing.degraded = timing.degraded or result[1].degraded
                timing.vectorless = timing.vectorless or result[1].vectorless
                for stage, ms in result[1].ms.items():
                    timing.ms[stage] = timing.ms.get(stage, 0.0) + ms
        return _together(question, asked, total, [None if r is None else r[0] for r in results])

    def _overlapped(
        self,
        rewriting: Callable[[], Rewritten],
        customer_id: str,
        question: str,
        scope: Scope,
        user_id: str | None,
        fused: bool = False,
    ) -> tuple[Rewritten, Future[_Early]]:
        """Step 19: the rewrite and plan call, with ``_early`` beside it on a thread of its own,
        in a copy of the context (billing, the rate scope, the limiter's waits, as ``_ensemble``);
        both are done when this returns. The early search's failure is raised only where it is
        used (``_answer``), as the same step would raise it there. ``fused``: aspects may come."""
        with ThreadPoolExecutor(max_workers=1) as pool:  # leaving it joins the thread
            early = pool.submit(copy_context().run, self._early, customer_id, question, scope, user_id, fused)
            done = rewriting()
        if early.exception() is None:
            _timing().overlap_ms = early.result().ms
        return done, early

    def _early(
        self, customer_id: str, question: str, scope: Scope, user_id: str | None, fused: bool = False
    ) -> _Early:
        """Step 19: the asker's words (``_expansion``), then the question's vector and hits as
        ``_search_answer`` gets them with no aspects; nothing with nothing to search. On its own
        thread: its own timing, so the answer's laps are the answer thread's only. ``fused``
        (aspects may come, #535): searched as the fused search searches it with no other text (the
        same hits), keeping its lists, so the aspects' search fuses them rather than search it again."""
        start = time.monotonic()
        _TIMING.set(_Timing(start, start))  # this thread's copy of the context only
        expansion = self._expansion(customer_id, question, scope, user_id)
        _, query_text, spoken = _searched_as(question, normalise(question))
        vector: list[float] | None = None
        hits: Sequence[Hit] = ()
        lists: Lists | None = None
        if query_text:
            vector = self._embed_all([spoken])[0]
            limit = self._search_limit(question, scope)
            if fused and self._translated is not None:
                found = self._translated.search_translated(
                    customer_id, query_text, vector, [], limit, scope, expansion, user_id=user_id
                )
                hits, lists = found.hits, found.lists
            else:
                hits = self._hits(customer_id, user_id, query_text, vector, [], scope, expansion, limit)
        took = (time.monotonic() - start) * 1000
        return _Early(expansion, vector, hits, _timing().degraded, took, lists)

    def _step_answer(
        self,
        customer_id: str,
        step: Step,
        question: str,
        scope: Scope,
        user_id: str | None,
        whole: Sequence[Hit] | None,
        aspects: Sequence[str],
        choice: ChatChoice | None,
        pins: Sequence[Pin],
        early: Future[_Early] | None = None,
        profile: Profile | None = None,
        exclude: str | None = None,
    ) -> Answer:
        """The clarify ``step`` asks, else the answer to its question (``_respond``'s end);
        ``early``: step 19's search of that question, when there was one. Step 22: ``profile``, the
        focus, answers a question for its related documents (``_related_answer``); ``exclude``, the
        focus left out of the search ("any other document")."""
        asked = step.question
        rewritten = asked if asked != question and len(asked) <= MAX_QUESTION else None
        if step.clarify is not None:  # no search, no draft
            asking = Answer("clarify", None, [], [], [], "search", asked, True, rewritten)
            return replace(asking, clarify=step.clarify, resume=step.resume)
        stream.emit("stage", {"stage": "searching"})  # step 21: every route's lookup starts here
        if profile is not None and asks_related(question) and about_focus(question):
            listed = self._related_answer(customer_id, user_id, profile, scope, asked)
            if listed is not None:  # none related: the search answers, the focus left out
                found = self._checked(listed, asked)
                return replace(found, rewritten_question=rewritten, assumptions=list(step.assumptions))
        answer = self._checked(
            self._answer(customer_id, asked, scope, user_id, whole, aspects, choice, pins, early, exclude),
            asked,
        )
        # KZ-006: an answer found without the model stays answered; a not found may be the outage,
        # said as one (``outage``), never as "not found", with the closest documents still listed
        # (never "busy", 24 Oct 2026).
        unavailable = answer.status == "not_found" and _timing().degraded
        answer = self._unreadable(customer_id, user_id, answer, scope)
        if not unavailable:  # an outage says so instead (KZ-006)
            answer = self._missing(customer_id, user_id, asked, answer, scope)
        mentioned = self._mentioned(customer_id, user_id, asked, answer, scope, exclude)
        answer = self._shaped(customer_id, user_id, asked, mentioned, scope)
        said = [*answer.assumptions, *step.assumptions]
        degraded = _timing().degraded or _timing().vectorless  # #535: never reused for a repeat
        reason = "timeout" if unavailable and _timing().outage == "timeout" else None
        return replace(
            answer,
            rewritten_question=rewritten,
            unavailable=unavailable,
            assumptions=said,
            degraded=degraded,
            outage_reason=reason,
        )

    def _checked(self, answer: Answer, question: str) -> Answer:
        """Decision 2's final check (PRD 6 A, voice.py) over every sentence, whoever wrote it:
        each number and fraction word must be the question's, a page of a source it cites, in
        the full text or the name of a source it cites (a figure as printed), or one the fixed
        query that wrote the sentence gave (``vouched``). A sentence that fails is dropped and
        its reason code logged, never its text; none left is not found. O(s * t)."""
        if answer.status != "answered":
            return answer
        asked = voice.numbers(question) | answer.vouched
        by_n = {citation.n: citation for citation in answer.citations}
        kept: list[AnswerSentence] = []
        for sentence in answer.sentences:
            sources = [by_n[n] for n in sentence.citations if n in by_n]
            reason = _unsupported(sentence.text, sources, asked, answer.backing)
            if reason is None:
                kept.append(sentence)
            else:
                _log.info("answer text dropped", extra={"code": f"final_check:sentence:{reason}"})
        if len(kept) == len(answer.sentences):
            return answer
        if not kept:
            closest, used = answer.closest_documents, answer.model_used
            return _not_found(
                answer.route,
                answer.normalized_question,
                closest,
                model_used=used,
                reason="notfound_final_check",
            )
        # The sources still cited, numbered from 1 again in order, as every answer's are.
        cited = sorted({n for sentence in kept for n in sentence.citations if n in by_n})
        new = {old: index for index, old in enumerate(cited, start=1)}
        return replace(
            answer,
            sentences=[AnswerSentence(s.text, [new[n] for n in s.citations if n in new]) for s in kept],
            citations=[replace(by_n[old], n=n) for old, n in new.items()],
            backing={new[old]: text for old, text in answer.backing.items() if old in new},
        )

    def _shaped(
        self, customer_id: str, user_id: str | None, question: str, answer: Answer, scope: Scope
    ) -> Answer:
        """Contract v2's ``documents`` (PRD 6 A): at most MAX_DOCUMENTS, each with its title
        and why (voice.py, from its card and the question) and the citations that are its. An
        answer's are the documents it cites, in order, but a count of documents (step 13),
        whose lines name each document with its pages already; a not-found or unchecked
        answer's are its closest documents, citing nothing. One statement for the cards."""
        if answer.status == "answered" and answer.sentences and not answer.sentences[0].citations:
            return answer  # step 13's count
        if answer.documents:
            return answer  # step 17 listed its records itself (owners.py)
        rows: dict[str, tuple[str, list[Citation]]] = {}
        for c in answer.citations if answer.status == "answered" else []:
            rows.setdefault(c.document_id, (c.document_name, []))[1].append(c)
        for doc in answer.closest_documents if answer.status != "answered" else []:
            rows.setdefault(str(doc["id"]), (str(doc["display_name"]), []))
        listed = list(rows.items())[: voice.MAX_DOCUMENTS]
        if not listed:
            return answer
        listed_ids = [d for d, _ in listed]
        found = self._context.cards(customer_id, listed_ids, scope, user_id=user_id) if self._context else {}
        normalised = normalise(question)
        keywords = [
            k for k in glossary.content_words(normalised.keywords) if not any(ch.isdigit() for ch in k)
        ]
        closest = {str(doc["id"]): list(doc.get("words") or []) for doc in answer.closest_documents}
        fallback = {"answered": "", "unchecked": "The figure appears here"}.get(
            answer.status, "Close to your words"
        )
        documents = []
        for document_id, (name, cited) in listed:
            card = found.get(document_id, ("", None))[1]
            text = word_text(" ".join(answer.backing.get(c.n, c.snippet) for c in cited))
            words = closest.get(document_id) or [k for k in keywords if has_word(k, text)]
            fields = [c.snippet.partition(":")[0] for c in cited if c.kind == "value"]
            said = voice.why(question, card, fields=fields, words=words, dated=mentions_date(normalised))
            reason = voice.unsupported(said, voice.numbers(question))
            if reason is not None:  # decision 2: never shown; why is written without digits
                _log.info("answer text dropped", extra={"code": f"final_check:why:{reason}"})
            documents.append(
                {
                    "document_id": document_id,
                    "title": voice.title(name, card),
                    "why": (said if reason is None else "") or fallback,
                    "citations": [c.n for c in cited],
                }
            )
        return replace(answer, documents=documents)

    def _mentioned(
        self,
        customer_id: str,
        user_id: str | None,
        question: str,
        answer: Answer,
        scope: Scope,
        exclude: str | None = None,
    ) -> Answer:
        """ "Also mentions" (Tushar, 17 Oct 2026): every other document in ``scope`` that holds
        one of the question's key terms (``key_terms``, the cited text's first) as whole words,
        not cited nor in "Also in", most matching pieces first, at most MAX_MENTIONED, and how
        many in all. Only for an answered or not-found answer, never a count (it lists its
        documents already); none without a key term. One statement over the word index."""
        if self._mentions is None or answer.status not in ("answered", "not_found"):
            return answer
        if exhaustive_query(question) is not None:
            return answer
        terms = key_terms(question, [c.snippet for c in answer.citations])
        if not terms:
            return answer
        shown = (
            {c.document_id for c in answer.citations}
            | {d["document_id"] for d in answer.related_documents}
            | {d["id"] for d in answer.closest_documents}  # a not-found answer's list (CodeRabbit)
            | ({exclude} if exclude else set())  # step 22: the focus, when "other" leaves it out
        )
        total, documents = self._mentions.mentions(
            customer_id, terms, scope, sorted(shown), MAX_MENTIONED, user_id=user_id
        )
        if not total:
            return answer
        return replace(answer, mentions={"terms": terms, "total": total, "documents": documents})

    def _missing(
        self, customer_id: str, user_id: str | None, question: str, answer: Answer, scope: Scope
    ) -> Answer:
        """#410 (Vrushit's UAT, 27 Oct 2026): a not-found answer names the question's key terms
        (``key_terms``: never a frame, total, date or form word) that no document in ``scope``
        holds as whole words, by the word index "Also mentions" uses, so the soft line can say
        which part of the question matched nothing (voice.soft_line). One statement a term, at
        most MAX_MENTION_TERMS, for a not-found answer only."""
        if self._mentions is None or answer.status != "not_found":
            return answer
        missing = tuple(
            term
            for term in key_terms(question)
            if not self._mentions.mentions(customer_id, [term], scope, [], 1, user_id=user_id)[0]
        )
        return replace(answer, missing_words=missing) if missing else answer

    def remember(self, turns: Sequence[Turn], memory: Memory, cited: Sequence[str]) -> Memory | None:
        """M3: the conversation's new summary and memory from ``memory`` and the ``turns``
        since (oldest first), with the documents ``cited`` in them; None with no model, or
        when it is down or replies badly. One model call (kaagaz/answering/memory.py)."""
        return None if self._model is None else summarise(self._model, turns, memory, cited)

    def _widens(self) -> bool:
        """CX6's steps can run: a context port and a fused search to search aspects with."""
        return self._context is not None and self._translated is not None

    def _input_cap(self, choice: ChatChoice | None) -> int | None:
        """Faster Swift (24 Oct 2026): ANSWER_SWIFT_INPUT_TOKENS for a Swift or Low answer, so
        a quick question never sends 120k tokens to the model; None for Precision, or 0 set."""
        cap = self._settings.answer_swift_input_tokens
        quick = choice is not None and choice.thinking in ("low", "medium")
        return cap if quick and cap > 0 else None

    def _whole(
        self, customer_id: str, user_id: str | None, scope: Scope, cap: int | None = None
    ) -> Sequence[Hit] | None:
        """CX5: every piece of ``scope``, in reading order, when the model is on and they fit
        ANSWER_WHOLE_DOC_TOKENS (or ``cap``, the smaller), counted cautiously as the evidence
        is; None otherwise. The port's size check (characters / 4) comes first, so a big
        scope reads no text."""
        budget = (
            self._settings.answer_whole_doc_tokens
            if cap is None
            else min(cap, self._settings.answer_whole_doc_tokens)
        )
        if self._model is None or self._context is None or budget <= 0:
            return None
        pieces = self._context.whole(customer_id, scope, budget, user_id=user_id)
        if pieces is None or sum(_tokens(item) for item in number_evidence(pieces)) > budget:
            return None
        return pieces

    def _unreadable(self, customer_id: str, user_id: str | None, answer: Answer, scope: Scope) -> Answer:
        """F6: a not-found answer names the pages in ``scope`` that couldn't be read well
        (unreadable or uncertain, kept as is or not), with the note; any other answer, or
        no such page, is as it was. One statement for a not-found answer."""
        if answer.status != "not_found" or self._reading is None:
            return answer
        pages, total = self._reading.flagged_pages(customer_id, scope=scope, user_id=user_id)
        note = not_found_note(pages, total)
        if note is None:
            return answer
        return replace(answer, reading_note=note, unreadable_pages=tuple(pages), unreadable_page_count=total)

    def _marked(
        self,
        customer_id: str,
        user_id: str | None,
        citations: list[Citation],
        regions: dict[int, list[dict[str, Any]]],
    ) -> list[Citation]:
        """F6: each citation ``hard_to_read`` when a word mark sits inside the cited text,
        at ``regions`` (by citation number: a piece's every line, a value's box). One
        statement for the cited documents' word marks."""
        if self._reading is None or not citations:
            return citations
        cited = [citation.document_id for citation in citations]
        marks = self._reading.word_marks(customer_id, cited, user_id=user_id)
        if not marks:
            return citations
        return [
            replace(citation, hard_to_read=hard_to_read(citation.document_id, regions[citation.n], marks))
            for citation in citations
        ]

    def _hard_values(self, customer_id: str, user_id: str | None, values: Sequence[ValueRef]) -> set[str]:
        """F6 for every figure a total or a ranking compares, listed or not (PRD 6 C): the ids
        of the values whose spot holds a word mark. One statement for all their documents' word
        marks; each value is checked against its own document's marks only."""
        if self._reading is None or not values:
            return set()
        marks: dict[str, list[WordMark]] = {}
        documents = [value.document_id for value in values]
        for mark in self._reading.word_marks(customer_id, documents, user_id=user_id):
            marks.setdefault(mark.document_id, []).append(mark)
        return {
            value.value_id
            for value in values
            if hard_to_read(
                value.document_id, valid_regions([value.region]), marks.get(value.document_id, [])
            )
        }

    def _answer(
        self,
        customer_id: str,
        question: str,
        scope: Scope,
        user_id: str | None,
        whole: Sequence[Hit] | None = None,
        aspects: Sequence[str] = (),
        choice: ChatChoice | None = None,
        pins: Sequence[Pin] = (),
        early: Future[_Early] | None = None,
        exclude: str | None = None,
    ) -> Answer:
        """``whole``: every piece of the scope (CX5), or None to search; ``aspects``: CX6's;
        ``choice``: who drafts and ``pins``, the asker's picks (``answer``); ``early``: step
        19's search of ``question``; ``exclude``: step 22's focus, left out of the search."""
        normalised = normalise(question)
        # "Hinglish understood as" is shown only when there was Hinglish to understand.
        shown = normalised.english if normalised.hinglish else question.strip()
        if self._owners is not None:
            owned = self._owners_answer(self._owners, customer_id, user_id, question, shown, scope)
            if owned is not None:
                return owned
        query = exhaustive_query(question)
        if query is not None and self._exhaustive is not None:
            counted = self._exhaustive_answer(self._exhaustive, customer_id, user_id, *query, shown, scope)
            if counted is not None:
                return counted
        found_range = date_range(normalised, self._today())
        fixed = self._fixed.scoped(scope)
        fields = partial(fixed.available_fields, customer_id, user_id=user_id)  # only if a plan could follow
        bound = bound_asked(normalised, numbers_asked(question, normalised.keywords))  # #381
        plan = plan_fixed(normalised, found_range, mentions_date(normalised), fields, bound)
        if plan is not None:
            return self._fixed_answer(customer_id, user_id, plan, shown, fixed)
        unchecked = self._unchecked_answer(customer_id, user_id, normalised, shown, scope)
        if unchecked is not None:
            return unchecked
        found = None if early is None else early.result()  # read beside the plan call (step 19)
        expansion = (
            self._expansion(customer_id, question, scope, user_id) if found is None else found.expansion
        )
        answer = self._search_answer(
            customer_id,
            user_id,
            question,
            normalised,
            shown,
            scope,
            expansion,
            whole,
            aspects,
            choice,
            pins,
            found,
            exclude,
        )
        if not expansion.used:
            return answer
        # The note, and never verified whatever passed (council 3 Oct 2026, A3).
        label = None if answer.label is None else "needs_review"
        return replace(answer, label=label, understood_words=expansion.understood())

    def _expansion(self, customer_id: str, question: str, scope: Scope, user_id: str | None) -> Expansion:
        """My words: read once, for this question and this user only, never cached."""
        document_id = (
            scope.document_ids[0] if scope.document_ids is not None and len(scope.document_ids) == 1 else None
        )
        words = () if user_id is None else self._words.words(customer_id, user_id, document_id=document_id)
        return expand(question, words) if words else NO_EXPANSION

    def _unchecked_answer(
        self, customer_id: str, user_id: str | None, normalised: NormalisedQuestion, shown: str, scope: Scope
    ) -> Answer | None:
        """A figure asked for that no checked value holds but a card does: no number,
        the documents where it appears, and a calm line to open them
        (decided 30 Sep 2026). None when no card holds it either."""
        asked = metric_asked(normalised)
        if asked is None:
            return None
        concept, fields = asked
        documents = self._unchecked.documents_with(customer_id, fields, scope=scope, user_id=user_id)
        if not documents:
            return None
        return Answer(
            status="unchecked",
            label="needs_review",
            sentences=[AnswerSentence(text=unchecked_sentence(concept), citations=[])],
            citations=[],
            closest_documents=documents,
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
        )

    # --- fixed route --------------------------------------------------------

    def _owners_answer(
        self, source: PgOwners, customer_id: str, user_id: str | None, question: str, shown: str, scope: Scope
    ) -> Answer | None:
        """Step 17 (kaagaz/answering/owners.py): not found, offering the records that match only
        part of it, when none matches all; None when it asks no such thing or no record matches."""
        ask = owner_ask(question, partial(source.resolve, customer_id, scope=scope, user_id=user_id))
        found = None if ask is None else owners_reply(source, customer_id, user_id, ask, scope)
        if found is None:
            return None
        if not found.answered:
            return _not_found(
                "fixed_query", shown, found.closest, model_used=False, reason="notfound_owners_part"
            )
        citations: list[Citation] = []
        numbered: dict[str, int] = {}
        spotless: set[str] = set()  # the numbers of values with no spot, never vouched for

        def cite(refs: Sequence[ValueRef]) -> list[int]:
            for ref in refs:
                regions = valid_regions([ref.region])
                if not regions:  # its printed label (0054) is its own text too
                    spotless.update(voice.numbers(f"{ref.value_text}\n{ref.label_text or ''}"))
                elif ref.value_id not in numbered:  # never shown without its spot
                    numbered[ref.value_id] = len(citations) + 1
                    citations.append(_value_citation(len(citations) + 1, ref, regions))
            return list(dict.fromkeys(numbered[ref.value_id] for ref in refs if ref.value_id in numbered))

        sentences = [AnswerSentence(text, cite(refs)) for text, refs in found.sentences]
        documents = [
            {"document_id": document_id, "title": title, "why": why, "citations": cite(refs)}
            for document_id, title, why, refs in found.documents
        ]
        citations = self._marked(customer_id, user_id, citations, {c.n: c.regions for c in citations})
        hard = any(c.hard_to_read for c in citations)
        verified = all(c.label == "verified" for c in citations) and not hard
        return Answer(
            status="answered",
            label="verified" if verified else "needs_review",
            sentences=sentences,
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
            reading_note=HARD_TO_READ_NOTE if hard else None,
            documents=documents,
            # Decision 2: owners.py writes these sentences: counts of the records listed and
            # values quoted as printed, so the final check keeps their numbers; a value with no
            # spot is not cited, so its figure must be in no sentence the check keeps.
            vouched=frozenset().union(*(voice.numbers(text) for text, _ in found.sentences)) - spotless,
        )

    def _related_answer(
        self, customer_id: str, user_id: str | None, profile: Profile, scope: Scope, shown: str
    ) -> Answer | None:
        """Step 22 (focus.py): the documents in ``scope`` related to the focus, ranked, each one line
        with why and its values as printed, cited, written in code (route ``fixed_query``, no model);
        the first line says how many and sums up the lines below. None when none is related: the
        search answers. One statement, and one for the word marks (F6)."""
        if self._focus is None:
            return None
        listed = related(profile, self._focus.matches(customer_id, user_id, profile, scope))
        if not listed:
            return None
        citations: list[Citation] = []
        lines: list[AnswerSentence] = []
        documents: list[dict[str, Any]] = []
        for found in listed:
            numbers = []
            for ref in found.refs:
                citations.append(_value_citation(len(citations) + 1, ref, valid_regions([ref.region])))
                numbers.append(len(citations))
            title = clean_text(found.name)[:_MAX_NAME].strip()
            lines.append(AnswerSentence(f"{title}: {found.why}.", numbers))
            documents.append(
                {"document_id": found.document_id, "title": title, "why": found.why, "citations": numbers}
            )
        citations = self._marked(customer_id, user_id, citations, {c.n: c.regions for c in citations})
        hard = any(c.hard_to_read for c in citations)
        name = clean_text(profile.name)[:_MAX_NAME].strip()
        many = "1 document shares" if len(listed) == 1 else f"{len(listed)} documents share"
        head = f"{many} facts with {name}."
        return Answer(
            status="answered",
            label="verified"
            if all(c.label == "verified" for c in citations) and not hard
            else "needs_review",
            sentences=[AnswerSentence(head, []), *lines],  # the count sums up the lines below
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
            reading_note=HARD_TO_READ_NOTE if hard else None,
            documents=documents,
            vouched=frozenset(voice.numbers(head)),  # decision 2: the count and the focus's name
        )

    def _exhaustive_answer(
        self,
        source: Exhaustive,
        customer_id: str,
        user_id: str | None,
        term: str,
        kinds: tuple[str, ...],
        shown: str,
        scope: Scope,
    ) -> Answer | None:
        """How many documents in ``scope`` hold ``term``, and which (kaagaz/answering/
        exhaustive.py): the exact count first, then each listed document with its pages,
        citing its first matching piece; past MAX_LISTED, how many more. None when no
        document holds it: the search route answers as before. Two statements, and one for
        the word marks (F6). ``kinds``: the document types asked for ("land documents of
        Jalna"), inside the chat's own (none left: None, the search answers)."""
        if kinds:
            within = kinds if scope.doc_types is None else tuple(k for k in scope.doc_types if k in kinds)
            scope = replace(scope, doc_types=within)
        total, documents = source.documents_with(customer_id, term, scope, MAX_LISTED, user_id=user_id)
        said = clean_text(term)
        citations: list[Citation] = []
        lines: dict[int, list[dict[str, Any]]] = {}
        listed: list[AnswerSentence] = []
        counted: set[int] = {total}  # decision 2: the count, and each listed document's pages
        for hits in documents:
            usable = number_evidence(hits)
            if not usable:
                continue  # counted, never shown without a place to open
            n = len(citations) + 1
            first = replace(usable[0], n=n)
            citations.append(_piece_citation(n, first, [said]))
            lines[n] = first.regions
            name = clean_text(first.hit.document_name)[:_MAX_NAME].strip()
            pages = pages_with(hits, term)
            counted |= {*pages, len(pages)}
            listed.append(AnswerSentence(document_sentence(name, pages), [n]))
        if not total or not citations or not said:
            return None
        counted |= {len(citations), total - len(citations)}
        citations = self._marked(customer_id, user_id, citations, lines)
        hard = any(c.hard_to_read for c in citations)
        head = count_sentence(said, total, kinds)
        if total > len(citations):
            head += " " + more_text(len(citations), total - len(citations))
        return Answer(
            status="answered",
            label="verified"
            if all(c.label == "verified" for c in citations) and not hard
            else "needs_review",
            sentences=[AnswerSentence(head, []), *listed],  # the count sums up the lines below
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
            reading_note=HARD_TO_READ_NOTE if hard else None,
            vouched=frozenset(str(number) for number in counted),
        )

    def _fixed_answer(
        self, customer_id: str, user_id: str | None, plan: FixedPlan, shown: str, fixed: FixedQueries
    ) -> Answer:
        if plan.aggregate == "total" or plan.group_fields:
            # PRD 6 C2: a party's sum, like a total, only when the numbers rule holds.
            listed = self._total_answer(customer_id, user_id, plan, shown, fixed)
            if listed is not None:
                return listed
        rows = self._run_plan(fixed, customer_id, user_id, plan)
        if not rows:
            return _not_found("fixed_query", shown, [], model_used=False, reason="notfound_fixed_no_rows")
        top = [row for row in rows if row.number == rows[0].number]  # a tie is shown, never broken
        cited = _cited_values(top)
        sentence = _fixed_sentence(plan, top)
        if not cited or sentence is None:
            return _not_found("fixed_query", shown, [], model_used=False, reason="notfound_fixed_no_sentence")
        # PRD 6 C2: every value compared that is not verified is cited too, after the answer's own.
        seen = {value.value_id for value, _ in cited}
        doubted = [(v, valid_regions([v.region])) for v in (*top[0].unsure, *top[0].totals)]
        doubted = [(v, regions) for v, regions in doubted if v.value_id not in seen]
        shown_n = len(cited)
        cited += [(value, regions) for value, regions in doubted if regions][:MAX_CITED_VALUES]
        citations = [
            _value_citation(index, value, regions) for index, (value, regions) in enumerate(cited, start=1)
        ]
        citations = self._marked(customer_id, user_id, citations, {c.n: c.regions for c in citations})
        hard = any(c.hard_to_read for c in citations)
        verified = (
            all(row.verified for row in top)
            and not top[0].unsure
            and not top[0].totals
            and all(c.label == "verified" for c in citations)
        )
        sentences = [AnswerSentence(text=sentence, citations=[c.n for c in citations[:shown_n]])]
        doubts, totalish = {v.value_id for v in top[0].unsure}, {v.value_id for v in top[0].totals}
        unsure = [
            c.n
            for c in citations
            if c.ref_id not in totalish and (c.label != "verified" or c.ref_id in doubts)
        ]
        looks = [c.n for c in citations if c.ref_id in totalish]
        if not verified and plan.aggregate != "count" and unsure:
            # PRD 6 C: the answer itself says so, beside the label, citing only what it is about.
            sentences.append(AnswerSentence("Some of the values behind this are not verified yet.", unsure))
        if looks:  # C2: left out of the comparison, and why
            said = (
                "A row that reads like a total is" if len(looks) == 1 else "Rows that read like a total are"
            )
            sentences.append(AnswerSentence(f"{said} left out of this.", looks))
        # Decision 2: the template's own inputs, the query's result and how many records gave it,
        # the dates asked and the names it groups by (package C narrows totals, decision 10).
        period = describe_range(plan.start, plan.end) if plan.start and plan.end else ""
        given = [
            period,
            str(top[0].number),
            str(sum(row.records for row in top)),
            *(r.group or "" for r in top),
        ]
        return Answer(
            status="answered",
            label="verified" if verified and not hard else "needs_review",
            sentences=sentences,
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
            reading_note=HARD_TO_READ_NOTE if hard else None,
            vouched=frozenset().union(*map(voice.numbers, given)),
        )

    def _total_answer(
        self, customer_id: str, user_id: str | None, plan: FixedPlan, shown: str, fixed: FixedQueries
    ) -> Answer | None:
        """A total, or every matching record and why there is none (PRD 6 C, answering/totals.py),
        written in code. Verified only for a total whose every cited value is, and no figure of
        any record hard to read, listed or not. Two statements, and one for the word marks of
        every record's document (F6). For a highest or lowest by party
        (C2), the same records and reasons (``ranking``), or None when the rule holds and the
        ranking may be shown."""
        metric = plan.metric or ""
        ranking = plan.aggregate != "total"
        figures = fixed.figures(
            customer_id,
            metric,
            plan.date_fields,
            plan.start,
            plan.end,
            invoices=plan.invoices,
            group_fields=plan.group_fields,
            user_id=user_id,
        )
        noun = "invoice" if plan.invoices else "record"
        period = describe_range(plan.start, plan.end) if plan.start and plan.end else ""
        if plan.bound is not None:
            # #381: every record whose figure is past the bound, and every one with no figure to
            # compare (it withholds the total, as ever); a sheet's printed total row covers rows
            # on both sides, so it is left out, as with a date range.
            figures = [
                f for f in figures if not f.printed_total and (f.amount is None or plan.within(f.amount))
            ]
            side = "over" if plan.bound[0] == ">" else "under"
            period = f"of {noun}s {side} {indian_number(plan.bound[1])}" + (f" {period}" if period else "")
        if not figures:
            return _not_found("fixed_query", shown, [], model_used=False, reason="notfound_fixed_no_rows")
        said = (_words(metric).removeprefix("total "), period, noun)
        told = tell(figures, *said, ranking=ranking)
        if not told.lines:
            return _not_found("fixed_query", shown, [], model_used=False, reason="notfound_fixed_no_sentence")
        citations = [
            _value_citation(n, value, regions) for n, (_, value, regions) in enumerate(told.lines, start=1)
        ]
        hard = self._hard_values(customer_id, user_id, [figure.value for figure in figures if figure.value])
        citations = [replace(c, hard_to_read=c.ref_id in hard) for c in citations]
        if hard:
            told = tell(figures, *said, hard, ranking=ranking)  # the same records, in the same order
        if ranking and told.total is not None:
            return None  # the rule holds, no listed figure hard to read: the ranking follows
        verified = told.total is not None and not hard and all(c.label == "verified" for c in citations)
        return Answer(
            status="answered",
            label="verified" if verified else "needs_review",
            sentences=[
                AnswerSentence(told.head, [c.n for c in citations]),  # it sums up the lines below
                *(AnswerSentence(text, [n]) for n, (text, _, _) in enumerate(told.lines, start=1)),
            ],
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
            reading_note=HARD_TO_READ_NOTE if hard else None,
            # Decision 2: every number here is code's (totals.tell: figures as printed, a total
            # only under decision 10's rule, counts of the lines shown), so the final check keeps them.
            vouched=frozenset().union(*map(voice.numbers, [told.head, *(text for text, _, _ in told.lines)])),
        )

    def _run_plan(
        self, fixed: FixedQueries, customer_id: str, user_id: str | None, plan: FixedPlan
    ) -> list[FixedRow]:
        if plan.aggregate == "count":
            if plan.invoices:
                return fixed.count_invoices(customer_id, plan.start, plan.end, user_id=user_id)
            return fixed.count_documents(
                customer_id, plan.start, plan.end, date_fields=plan.date_fields, user_id=user_id
            )
        if plan.metric is None:
            return []
        descending = plan.aggregate == "highest"
        if plan.group_fields:
            return fixed.top_group(
                customer_id,
                plan.metric,
                plan.group_fields,
                plan.date_fields,
                plan.start,
                plan.end,
                descending,
                user_id=user_id,
            )
        return fixed.extreme_value(
            customer_id, plan.metric, plan.date_fields, plan.start, plan.end, descending, user_id=user_id
        )

    # --- search route -------------------------------------------------------

    def _search_answer(
        self,
        customer_id: str,
        user_id: str | None,
        question: str,
        normalised: NormalisedQuestion,
        shown: str,
        scope: Scope,
        expansion: Expansion,
        whole: Sequence[Hit] | None = None,
        aspects: Sequence[str] = (),
        choice: ChatChoice | None = None,
        pins: Sequence[Pin] = (),
        early: _Early | None = None,
        exclude: str | None = None,
    ) -> Answer:
        normalised, query_text, spoken = _searched_as(question, normalised)
        if not query_text:
            return _not_found("search", shown, [], model_used=False, reason="notfound_empty_question")
        numbers = _numbers(question, normalised.keywords, expansion)
        min_score = self._settings.answer_min_evidence_score
        translated: Translated | None = None
        first: Gate | None = None  # how the question's own search passed the gate, when translated
        vector: list[float] | None = None
        if whole is not None:
            evidence = number_evidence([hit for hit in whole if hit.document_id != exclude])  # CX5, step 22
        else:
            if early is not None and not aspects:  # step 19: the same embedding and search, done early
                vector, hits = early.vector, early.hits
                extra: list[tuple[str, list[float] | None]] = []
                timing = _timing()
                timing.degraded, timing.overlap = timing.degraded or early.degraded, True
                timing.lap("embed")
            elif early is not None and early.lists is not None and early.vector is not None:
                # #535: the question's vector and lists from step 19's early search; only its
                # aspects (CX6) are embedded and searched, fused with them as the search fuses.
                vector, meanings = early.vector, self._embed_all(aspects)
                timing = _timing()
                timing.degraded, timing.overlap = timing.degraded or early.degraded, True
                timing.lap("embed")
                extra = [
                    (_search_text(text), meaning) for text, meaning in zip(aspects, meanings, strict=True)
                ]
                limit = self._search_limit(question, scope, exclude)
                hits = self._hits(
                    customer_id, user_id, query_text, vector, extra, scope, expansion, limit, early.lists
                )
            else:
                # Its aspects (CX6) are embedded with the question, in one call.
                vector, *meanings = self._embed_all([spoken, *aspects])
                _timing().lap("embed")
                extra = [
                    (_search_text(text), meaning) for text, meaning in zip(aspects, meanings, strict=True)
                ]
                hits = self._hits(
                    customer_id,
                    user_id,
                    query_text,
                    vector,
                    extra,
                    scope,
                    expansion,
                    self._search_limit(question, scope, exclude),
                )
            if self._model is not None and (vector is None or any(m is None for _, m in extra)):
                _timing().vectorless = True  # #535: no outage, but never reused for a repeat
            if hits:  # never stuck (17 Oct 2026): found by words and typos, a missing vector is no outage
                _timing().degraded = False
            translated = self._translated_search(
                customer_id, user_id, question, query_text, vector, hits, scope, expansion, extra
            )
            if translated is not None:
                own = number_evidence(self._documents_once(question, scope, hits, exclude))
                first = enough_evidence(own, normalised.keywords, min_score, numbers, expansion)
                hits = translated.hits
            evidence = number_evidence(self._documents_once(question, scope, hits, exclude))
        hits_only = list(evidence)  # the search's own hits, before widening adds to the list

        # Only usable evidence counts as close: an instruction-like piece is not. A whole
        # document's pieces are in reading order, so its closest come from a search (``_closest``).
        def closest() -> list[dict[str, Any]]:
            return self._closest(
                customer_id, user_id, query_text, scope, expansion, normalised.keywords, hits_only, whole
            )

        gate = enough_evidence(evidence, normalised.keywords, min_score, numbers, expansion)
        if whole is not None:
            # CX5: only the numbers asked must be there; the keyword rule would turn away an
            # English question about a Hindi or Marathi document. The model and verify decide.
            passed = holds_numbers(evidence, numbers)
        else:
            scored = translated is not None and any_hit_scores(evidence, min_score, numbers)
            passed = gate is not None or scored
        _timing().lap("search")
        if not passed:
            return _not_found("search", shown, closest(), model_used=False, reason="notfound_gate")
        if whole is None:
            # Most relevant picks (answering/pins.py): after the gate, so a pick never decides
            # whether there is an answer; only a hit that passes on its own moves up.
            def alone(item: Evidence) -> bool:
                return enough_evidence([item], normalised.keywords, min_score, numbers) in (
                    "score",
                    "keywords",
                )

            evidence, moved = pinned_first(evidence, lifts(pins, normalised.keywords), alone)
            _timing().pinned = moved
            terms = [question, *aspects]
            widened = self._widened(customer_id, user_id, terms, normalised.keywords, vector, evidence, scope)
            widened = [hit for hit in widened if hit.document_id != exclude]  # step 22
            evidence += number_evidence(widened, start=len(evidence) + 1)
            # Q4: neighbours only after the gate passed on the hits, numbered after all of them,
            # while they fit the room the evidence and the question leave in MODEL_CONTEXT_TOKENS
            # (M1): every hit is kept, the neighbours go first.
            room = input_token_budget(
                num_ctx=self._settings.model_context_tokens, max_tokens=_REPLY_TOKENS, system=SYSTEM_PROMPT
            )
            room -= estimate_tokens_cautiously(question) + sum(_tokens(item) for item in evidence)
            near = self._retriever.neighbours(
                customer_id, [item.hit for item in evidence], scope, user_id=user_id
            )
            for item in number_evidence(near, start=len(evidence) + 1, neighbour=True):
                room -= _tokens(item)
                if room < 0:
                    break
                evidence.append(item)
            _timing().lap("widen")
        else:
            # Step 20 (#381): most relevant first, so the prompt puts them at its ends.
            evidence = self._ranked(customer_id, user_id, query_text, spoken, scope, expansion, evidence)
            _timing().lap("widen")

        drafted = self._draft(
            customer_id,
            user_id,
            scope,
            question,
            normalised,
            evidence,
            expansion,
            choice,
            whole=whole is not None,
        )
        _timing().lap("draft")
        stream.emit("stage", {"stage": "checking"})  # step 21: verify, the labels, the final check
        draft, model_used, seen, why, team = drafted
        texts = _texts(seen)
        kept = _kept(draft, texts)
        if not kept:
            why = why if draft.status != "answered" else "notfound_verify_dropped_all"
            return _not_found("search", shown, closest(), model_used=model_used, reason=why)

        by_n = {item.n: item for item in evidence}
        renumber, citations = _citations(kept, by_n, whole is not None)
        # F6: the cited text is the whole piece, every line of it.
        lines = {new: by_n[old].regions for old, new in renumber.items()}
        citations = self._marked(customer_id, user_id, citations, lines)
        hard = any(c.hard_to_read for c in citations)
        # A sentence backed by neighbours only, or with a number only a neighbour holds, is never
        # verified: the gate never judged them (PROVISIONAL, plan decision 8).
        on_neighbours = any(_rests_on_neighbours(s, by_n, texts) for s in kept)
        # Nor is one the glossary or a sound alone let through or chose the quote of, one
        # checked against nothing, one a cited hit does not pass the gate on its own, or one
        # with an English word that evidence in another script cannot back (council, PR2 and PR 2b,
        # and their code reviews). Only the question's dropped words (who, is, ka...): a
        # content word of it, a name the question puts forward, must be in the cited text
        # itself, by sound, or through the glossary's English.
        asked = _latin_words(f"{question} {normalised.english}") - set(normalised.keywords)
        unchecked = (
            gate in CAPPED
            or any(not _judged(s, by_n, normalised.keywords, min_score, numbers) for s in kept)
            or any(unchecked_cross_script(s, texts) for s in kept)
            or any(_unbacked_latin(s, texts, asked) for s in kept)
            or (not model_used and any(_quoted_through_glossary(s, by_n, normalised.keywords) for s in kept))
            # The translation (council 4 Oct 2026): it alone let the question through, or found
            # a cited hit, or lifted its score; capped here, never through CAPPED.
            or (
                translated is not None
                and (
                    first not in ("score", "keywords")
                    or any(_translated_only(by_n[n], translated, min_score) for s in kept for n in s.evidence)
                )
            )
            # Ensemble (features 25-27, C7): the other models not confirming adds Needs review, only.
            or (team is not None and team.agreement == "not_confirmed")
        )
        # #520 (Tushar, 6 Nov 2026): a line of a cited piece that reads as if addressed to the model
        # stays evidence, but the answer is never verified, and says why. Only the cited pieces
        # (review of #520): one the answer does not rest on never marks it.
        addressed = any(
            reads_like_instructions(f"{by_n[old].hit.context_header}\n{by_n[old].hit.text}")
            for old in renumber
        )
        if addressed:
            _log.info("answer needs review", extra={"code": "instruction_like_source"})
        verified = all(c.label == "verified" for c in citations) and not (
            on_neighbours or unchecked or hard or addressed
        )
        notes = [note for note, said in ((HARD_TO_READ_NOTE, hard), (INSTRUCTION_NOTE, addressed)) if said]
        return Answer(
            status="answered",
            label="verified" if verified else "needs_review",
            sentences=[AnswerSentence(s.text, [renumber[n] for n in s.evidence]) for s in kept],
            citations=citations,
            closest_documents=[] if whole is not None else closest(),
            route="search",
            normalized_question=shown,
            model_used=model_used,
            reading_note=" ".join(notes) or None,
            team=team,
            backing={new: by_n[old].hit.text for old, new in renumber.items()},
            related_documents=(
                []
                if whole is not None
                else related_documents(
                    hits_only, {c.document_id for c in citations}, normalised.keywords, min_score, numbers
                )
            ),
        )

    def _ranked(
        self,
        customer_id: str,
        user_id: str | None,
        query_text: str,
        spoken: str,
        scope: Scope,
        expansion: Expansion,
        evidence: Sequence[Evidence],
    ) -> list[Evidence]:
        """Step 20 (#381): a whole scope's pieces (CX5), most relevant first: the ones the
        question's own search finds (words, typos and meaning, fused), in its order, then the rest
        in reading order. Each keeps its number, so a citation points at the same piece. One
        embedding and one search; the model being down for the embedding only loses the meaning
        (it is no outage: the order is a help, never the answer)."""
        timing = _timing()
        down = timing.degraded
        vector = self._embed(spoken)
        timing.degraded = down
        timing.vectorless = timing.vectorless or vector is None  # #535: another order, never reused
        limit = max(1, min(len(evidence), MAX_LIMIT))
        found = self._retriever.search(
            customer_id, query_text, vector, limit, scope, expansion, user_id=user_id
        )
        place: dict[str, int] = {}
        for hit in found:
            place.setdefault(hit.piece_id, len(place))
        return sorted(evidence, key=lambda item: place.get(item.hit.piece_id, len(place)))

    def _closest(
        self,
        customer_id: str,
        user_id: str | None,
        query_text: str,
        scope: Scope,
        expansion: Expansion,
        keywords: Sequence[str],
        evidence: Sequence[Evidence],
        whole: Sequence[Hit] | None,
    ) -> list[dict[str, Any]]:
        """Decision 9: the documents close to the question (``closest_documents``: by words,
        typos or sound, never meaning alone). From the search's hits; with a whole document
        (CX5) from a search by words and typos (no vector), never its reading order: one
        statement more, only for a not-found answer."""
        if whole is None:
            return closest_documents([item.hit for item in evidence], keywords)
        limit = max(1, min(self._settings.answer_max_evidence, MAX_LIMIT))
        hits = self._retriever.search(customer_id, query_text, None, limit, scope, expansion, user_id=user_id)
        return closest_documents([item.hit for item in number_evidence(hits)], keywords)

    def _translated_search(
        self,
        customer_id: str,
        user_id: str | None,
        question: str,
        query_text: str,
        vector: list[float] | None,
        hits: Sequence[Any],
        scope: Scope,
        expansion: Expansion,
        extra: Sequence[tuple[str, list[float] | None]] = (),
    ) -> Translated | None:
        """The search again with the question's translations into the script of its first
        ``hits`` (translate.py), decided before any draft; None to answer as today: the
        setting off, no other script, or no usable translation. The texts are search
        parameters and embeddings only. At most one model call and one search. ``extra``:
        the aspects (CX6), searched with them."""
        if self._translator is None or self._translated is None:
            return None
        scripts = target_scripts(question, [hit.text for hit in hits])
        queries = translate(self._translator, question, scripts) if scripts else None
        if not queries:
            return None
        texts = [*extra, *((query.text, self._embed(query.text)) for query in queries)]
        return self._translated.search_translated(
            customer_id,
            query_text,
            vector,
            texts,
            self._search_limit(question, scope),
            scope,
            expansion,
            user_id=user_id,
        )

    def _hits(
        self,
        customer_id: str,
        user_id: str | None,
        query_text: str,
        vector: list[float] | None,
        extra: Sequence[tuple[str, list[float] | None]],
        scope: Scope,
        expansion: Expansion,
        limit: int,
        asked: Lists | None = None,
    ) -> Sequence[Hit]:
        """The search's best ``limit`` hits (``_search_limit``): the question's, fused with its
        aspects' (CX6) when it has any; ``asked``: the question's lists already found (#535), not
        searched again."""
        if extra and self._translated is not None:
            fused = self._translated.search_translated
            return fused(
                customer_id, query_text, vector, extra, limit, scope, expansion, asked, user_id=user_id
            ).hits
        return self._retriever.search(
            customer_id, query_text, vector, limit, scope, expansion, user_id=user_id
        )

    def _search_limit(self, question: str, scope: Scope, exclude: str | None = None) -> int:
        """How many hits a search of ``question`` keeps: ANSWER_MAX_EVIDENCE, or MAX_LIMIT when it
        asks which documents (#617 item 5), or any document other than the focus (step 22,
        ``exclude``), of a scope that names more than one, whose hits are then kept one a document
        (``_documents_once``), so the evidence spans more documents."""
        if (asks_documents(question) or exclude is not None) and not _one_document(scope):
            return MAX_LIMIT
        return max(1, min(self._settings.answer_max_evidence, MAX_LIMIT))

    def _documents_once(
        self, question: str, scope: Scope, hits: Sequence[Hit], exclude: str | None = None
    ) -> Sequence[Hit]:
        """#617 item 5: for a question that asks which documents, each document's best hit only
        (hits come best first); all of them when they are one document's or the scope names one.
        At most ANSWER_MAX_EVIDENCE either way; any other question's hits as they are. Step 22:
        ``exclude``'s hits never, and the rest as for a question that asks which documents. O(h)."""
        if exclude is not None:
            hits = [hit for hit in hits if hit.document_id != exclude]
        elif not asks_documents(question):
            return hits
        most = max(1, min(self._settings.answer_max_evidence, MAX_LIMIT))
        if _one_document(scope) or len({hit.document_id for hit in hits}) <= 1:
            return list(hits)[:most]
        best: dict[str, Hit] = {}
        for hit in hits:
            best.setdefault(hit.document_id, hit)
        return list(best.values())[:most]

    def _widened(
        self,
        customer_id: str,
        user_id: str | None,
        texts: Sequence[str],
        keywords: Sequence[str],
        vector: list[float] | None,
        evidence: Sequence[Evidence],
        scope: Scope,
    ) -> list[Hit]:
        """CX6: the pieces of the topics ``texts`` (the question and its aspects) or the hits
        name, then of the two sections the keywords or the vector find, each once and never a
        hit. Two statements; none without the context port, nor with the model off, so the
        keyless mode answers as PRD 3 built it (no sections or topics are mapped there)."""
        if self._context is None or self._model is None:
            return []
        hits = [item.hit for item in evidence]
        topics = self._context.topic_pieces(customer_id, topic_terms(texts), hits, scope, user_id=user_id)
        sections = self._context.section_pieces(
            customer_id, keywords, vector, [*hits, *topics], scope, user_id=user_id
        )
        return [*topics, *sections]

    def _embed(self, english: str) -> list[float] | None:
        """One text's vector (``_embed_all``)."""
        return self._embed_all([english])[0]

    def _embed_all(self, texts: Sequence[str]) -> list[list[float] | None]:
        """Each text's vector, or None (no model, model down, unusable vectors, or an empty
        text), so search falls back to word and fuzzy matching. One model call for them
        all, each as plain text (PRD 5 M5)."""
        wanted = [text for text in texts if text]
        if self._model is None or not wanted:
            return [None] * len(texts)
        try:
            vectors = self._model.embed(wanted)
        except (ModelUnavailable, ModelOutputError) as error:
            _log.warning("question searched without its meaning vector", extra={"code": error.code})
            if isinstance(error, ModelUnavailable):
                _timing().degraded = True
            return [None] * len(texts)
        if len(vectors) != len(wanted):
            return [None] * len(texts)
        found: Iterator[list[float]] = iter(vectors)
        return [next(found) if text else None for text in texts]

    def _cards(
        self, customer_id: str, user_id: str | None, scope: Scope, seen: Sequence[Evidence]
    ) -> list[str]:
        """M2: one card line for each document of ``seen``, in its order; none without the
        context port. A line that reads like instructions is left out. One statement."""
        if self._context is None or not seen:
            return []
        documents = list(dict.fromkeys(item.hit.document_id for item in seen))
        found = self._context.cards(customer_id, documents, scope, user_id=user_id)
        lines = [card_line(*found[document]) for document in documents if document in found]
        return [line for line in lines if line is not None and not looks_like_instructions(line)]

    def _draft(
        self,
        customer_id: str,
        user_id: str | None,
        scope: Scope,
        question: str,
        normalised: NormalisedQuestion,
        evidence: Sequence[Evidence],
        expansion: Expansion = NO_EXPANSION,
        choice: ChatChoice | None = None,
        *,
        whole: bool = False,
    ) -> tuple[Draft, bool, Sequence[Evidence], str, Team | None]:
        """The model's draft (and True), or the extractive one (and False), with the
        evidence it was drafted from: for the model, the best pieces that fit its window,
        and the reason code a not-found draft would give; and the ``Team`` of a ``choice``
        (features 25-27, C7), None without one.
        The model sees the question, the evidence and its documents' cards (M2, while they
        fit) only, never a user's word or meaning; ``expansion`` helps the extractive draft
        choose its lines. Step 21 (streamed): the ``sources`` the draft reads, then one model's
        draft is streamed and its checked sentences sent (``_Preview``; ``whole``: CX5's, whose
        citations have no rank); an Ensemble's, or a quoted one, sends none."""
        team: Team | None = None
        if self._model is None:
            stream.emit("sources", _sources(evidence))
        else:
            asked = f"{question} (in English: {normalised.english})" if normalised.hinglish else question
            cap = self._input_cap(choice)
            budget = self._evidence_budget if cap is None else min(cap, self._evidence_budget)
            seen = fit_evidence(asked, evidence, budget)
            code = "evidence_too_large"  # not even the best piece fits
            events = stream.EVENTS.get()
            alone = choice is None or len(choice.agents) <= 1
            streaming = events is not None and alone and isinstance(self._model, StreamingModel)
            if seen and self._in_parts(seen, choice):
                picked = self._read_in_parts(asked, seen, choice)
                if isinstance(picked, str):
                    # #535: the parts timed out, and a draft from all the evidence would too; but a
                    # streamed one is only cut when it goes quiet or past the stream's cap, so on
                    # the stream it is still drafted from all of it (agreed with the speed fixes).
                    if not streaming:
                        code, seen = picked, []
                elif picked is not None and not picked:  # every part read, none holds an answer
                    return Draft("not_found", []), True, seen, "notfound_model", team
                elif picked is not None:
                    seen = picked
            if seen:
                stream.emit("sources", _sources(seen))
                cards = fit_cards(asked, seen, self._cards(customer_id, user_id, scope, seen), budget)
                stream.emit("stage", {"stage": "writing"})
                draft_with = partial(
                    model_draft,
                    self._model,
                    asked,
                    seen,
                    max_tokens=self._settings.model_answer_max_tokens,
                    cards=cards,
                    deadline=_PART_DEADLINE.get(),  # step 22: a part's draft ends by the answer's deadline
                )
                if streaming and events is not None and isinstance(self._model, StreamingModel):
                    preview = _Preview(question, seen, whole, events)
                    draft_with = partial(
                        draft_streamed,
                        self._model,
                        asked,
                        seen,
                        max_tokens=self._settings.model_answer_max_tokens,
                        cards=cards,
                        offer=preview.offer,
                    )
                if choice is not None and len(choice.agents) > 1:
                    drafted, team, code = self._ensemble(draft_with, seen, choice)
                else:
                    drafted, team, code = _single(draft_with, self._model.chat_model, choice)
                if drafted is not None:
                    _timing().redrafts = drafted.redrafts
                    return drafted, True, seen, "notfound_model", team
            # Quoting the evidence needs no model.
            _log.warning("answer drafted without the model", extra={"code": code})
            if code not in _REPLY_FAULTS:
                _timing().degraded = True
                _timing().outage = code  # #535: a timeout says so (``Answer.outage_reason``)
        why = "notfound_no_model" if self._model is None else f"notfound_model_error:{code}"
        if mentions_date(normalised):
            # A quoted line cannot be checked against a date.
            return Draft("not_found", []), False, evidence, f"{why}:date", team
        return (
            extractive_draft(
                normalised.keywords, evidence, _numbers(question, normalised.keywords, expansion), expansion
            ),
            False,
            evidence,
            why,
            team,
        )

    def _in_parts(self, seen: Sequence[Evidence], choice: ChatChoice | None) -> bool:
        """Step 20 (#381): read ``seen`` in parts: a Precision answer (high thinking; Swift and
        Low never, they stay fast) whose evidence is more than ANSWER_PARTS_TOKENS (0: never)."""
        most = self._settings.answer_parts_tokens
        precise = choice is not None and bool(choice.agents) and choice.thinking == "high"
        return precise and most > 0 and sum(_tokens(item) for item in seen) > most

    def _read_in_parts(
        self, asked: str, seen: Sequence[Evidence], choice: ChatChoice | None
    ) -> list[Evidence] | str | None:
        """Step 20 (#381, answering/parts.py): ``seen`` split into at most ANSWER_PARTS_MAX parts,
        each read by the choice's first agent at its level, all at once (each call takes its own
        slot of the model limiter; one deadline, as ``_ensemble``), in a copy of the context
        (billing, the rate scope, the limiter's waits); every thread is joined before this
        returns. The pieces the parts name, in ``seen``'s order ([]: none holds an answer), or
        None when a part failed, logged by its code: the answer is then drafted from all of
        ``seen`` in one call, as before. #535 (test-server log, 7 Nov 2026): "timeout" when a part
        timed out, as a draft from all of ``seen``, more than any part, would too: none is made, and
        the answer goes the way a draft that timed out goes."""
        if self._model is None or choice is None or not choice.agents:
            return None
        model, lead = self._model, choice.agents[0]
        settings = self._settings
        groups = split(
            seen, [_tokens(item) for item in seen], settings.answer_parts_tokens, settings.answer_parts_max
        )
        deadline = time.monotonic() + settings.model_timeout_seconds + ENSEMBLE_SLACK_SECONDS

        def one(part: list[Evidence]) -> list[int] | ModelUnavailable | ModelOutputError:
            try:
                return read_part(
                    model,
                    asked,
                    part,
                    max_tokens=settings.model_answer_max_tokens,
                    chat_model=lead.model,
                    thinking=lead.level(choice.thinking),
                    provider=lead.provider,
                    deadline=deadline,
                )
            except (ModelUnavailable, ModelOutputError) as error:
                return error

        with ThreadPoolExecutor(max_workers=len(groups)) as pool:  # leaving it joins every thread
            futures = [pool.submit(copy_context().run, one, part) for part in groups]
        results = [future.result() for future in futures]
        for result in results:
            if not isinstance(result, list):
                timed_out = isinstance(result, ModelUnavailable) and result.code == "timeout"
                then = "not drafted again" if timed_out else "drafted from all the evidence"
                _log.warning(f"read in parts failed, {then}", extra={"code": result.code})
                return result.code if timed_out else None
        _log.info("read in %d parts by %s with prompt %s", len(groups), lead.model, PARTS_PROMPT_HASH)
        _timing().parts = len(groups)
        named = {n for result in results if isinstance(result, list) for n in result}
        return [item for item in seen if item.n in named]

    def _ensemble(
        self, draft_with: Callable[..., Draft], seen: Sequence[Evidence], choice: ChatChoice
    ) -> tuple[Draft | None, Team | None, str]:
        """C7: every agent drafts from the same evidence at once, each by one deadline
        (MODEL_TIMEOUT_SECONDS + ENSEMBLE_SLACK_SECONDS from now), the others billed as
        ``ensemble_chat``; every thread is joined before this returns, so the ledger has
        every call. The lead is fixed before any reply: the first agent (resolve_chat
        puts the default first). An outage (``_outage``) drops an agent out and the next
        in order leads; a content failure (a refusal, a bad reply, not found while another
        answered, no sentence verify keeps) is dissent, and an agent is never replaced
        for it: a lead that failed so gives (None, the team, its code) and the answer is
        quoted, Needs review. A lead that found nothing while another answered: that one's
        draft, not confirmed. No agent at all: (None, None, the lead's code)."""
        deadline = time.monotonic() + self._settings.model_timeout_seconds + ENSEMBLE_SLACK_SECONDS
        lead = choice.agents[0]

        def one(agent: Agent) -> Draft | ModelUnavailable | ModelOutputError:
            call = partial(
                draft_with,
                chat_model=agent.model,
                thinking=agent.level(choice.thinking),  # no reasoning field for a model with no levels
                provider=agent.provider,
                deadline=deadline,
            )
            try:
                if agent is lead:
                    return call()
                with billed_as("ensemble_chat"):
                    return call()
            except (ModelUnavailable, ModelOutputError) as error:
                return error

        with ThreadPoolExecutor(max_workers=len(choice.agents)) as pool:  # leaving it joins every thread
            futures = [pool.submit(copy_context().run, one, agent) for agent in choice.agents]
        results = [future.result() for future in futures]
        present = [(a, r) for a, r in zip(choice.agents, results, strict=True) if not _outage(r)]
        if not present:
            first = results[0]
            return None, None, first.code if not isinstance(first, Draft) else "ensemble"
        texts = _texts(seen)
        kept = [_kept(r, texts) if isinstance(r, Draft) else [] for _, r in present]
        main = 0
        main_result = present[0][1]
        if isinstance(main_result, Draft) and main_result.status == "not_found":
            main = next((i for i, sentences in enumerate(kept) if sentences), 0)
        answered = [agent for agent, r in present if isinstance(r, Draft)]
        named = sorted(answered, key=lambda agent: agent is not present[main][0])
        result: Literal["agreed", "not_confirmed"] | None = None
        if len(present) > 1:
            others = [sentences for i, sentences in enumerate(kept) if i != main]
            result = "not_confirmed" if main else agreement(kept[main], others)
        team = Team(
            tuple(agent.name for agent in named),
            tuple(agent.model for agent in named),
            len(choice.agents),
            result,  # a lead that failed has no sentence, so it is never "agreed"
            choice.thinking,
        )
        chosen = present[main][1]
        if not isinstance(chosen, Draft):
            return None, team, chosen.code
        _log.info("answer drafted by %s with prompt %s", present[main][0].model, ANSWER_PROMPT_HASH)
        return chosen, team, ""

    def _today(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("the clock must give a timezone-aware time")
        return now.astimezone(self._zone).date()


def _single(
    draft_with: Callable[..., Draft], default_model: str, choice: ChatChoice | None
) -> tuple[Draft | None, Team | None, str]:
    """One model's draft: the ``choice``'s one agent at its level (C7), or, with no choice,
    MODEL_CHAT as before; (None, None, the code) when it fails. Never "busy" (24 Oct 2026):
    when that model is down (``_outage``), each of the choice's ``backups`` is tried in turn,
    the answer then named by the backup that drafted it; a reply fault (too long, malformed,
    refused) is not retried, and a rejected key fails every model alike."""
    if choice is None or not choice.agents:
        try:
            draft = draft_with()
        except (ModelUnavailable, ModelOutputError) as error:
            return None, None, error.code
        _log.info("answer drafted by %s with prompt %s", default_model, ANSWER_PROMPT_HASH)
        return draft, None, ""
    code = ""
    for turn, agent in enumerate((choice.agents[0], *choice.backups)):
        try:
            draft = draft_with(
                chat_model=agent.model, thinking=agent.level(choice.thinking), provider=agent.provider
            )
        except (ModelUnavailable, ModelOutputError) as error:
            code = error.code
            if not _outage(error) or code == "key_rejected":
                return None, None, code
            continue
        if turn:
            _log.warning("answer drafted by a backup model", extra={"code": code})
        _log.info("answer drafted by %s with prompt %s", agent.model, ANSWER_PROMPT_HASH)
        return draft, Team((agent.name,), (agent.model,), 1, None, choice.thinking), ""
    return None, None, code


def _unsupported(
    text: str, cited: Sequence[Citation], asked: set[str], backing: dict[int, str]
) -> str | None:
    """Decision 2's final check of one sentence (``_checked``): why a number or fraction word in
    ``text`` is none of ``asked`` (the question's, and a fixed query's), a page of a source it
    cites, or in the full text (``backing``, else the snippet) or name of one; None when every
    one is."""
    allowed = set(asked)
    for c in cited:
        allowed |= voice.numbers(f"{backing.get(c.n, c.snippet)}\n{c.document_name}")
        allowed |= {str(page) for page in (c.page_no, *range(c.page_from, c.page_to + 1))}
    return voice.unsupported(text, allowed)


def _citations(
    kept: Sequence[DraftSentence], by_n: dict[int, Evidence], whole: bool
) -> tuple[dict[int, int], list[Citation]]:
    """The answer's citations of ``kept`` (``_search_answer``, and step 21's preview alike): each
    evidence id cited, numbered from 1 in the order the sentences cite them, with the lines of the
    sentences citing it (``_focus``); ranked sources (16 Oct 2026): a hit's evidence number is its
    place in the fused search order, after the Most relevant picks' lift (None for CX5's
    ``whole``). The renumbering (evidence id: citation number) and the citations, in order."""
    renumber: dict[int, int] = {}
    for sentence in kept:
        for n in sentence.evidence:
            renumber.setdefault(n, len(renumber) + 1)
    citations = [
        replace(
            _piece_citation(new, by_n[old], [s.text for s in kept if old in s.evidence]),
            rank=None if whole else old,
        )
        for old, new in renumber.items()
    ]
    return renumber, citations


def _sources(seen: Sequence[Evidence]) -> dict[str, Any]:
    """Step 21's ``sources`` event: the documents of the evidence the draft reads, best first, each
    once with its name (the customer's own, as the search found them under RLS), and how many
    passages."""
    names: dict[str, str] = {}
    for item in seen:
        names.setdefault(item.hit.document_id, item.hit.document_name)
    return {"documents": [{"id": d, "name": n} for d, n in names.items()], "passages": len(seen)}


@dataclass
class _Preview:
    """Step 21: the draft's sentences as they stream in (``stream.draft_streamed``), each sent once
    it passes what the answer will check it with: the draft's shape (``draft.checked``),
    ``verify`` (``_kept``), the same text once, then decision 2's final check
    (``_unsupported``). Its citations are the answer's own (``_piece_citation``, numbered from 1 in
    the order the sentences sent cite them, a hit's ``rank``), so the preview is the answer's
    sentences unless a later step drops one; the answer itself is still worked out in full and is
    the truth. O(e + t) a sentence."""

    question: str
    seen: Sequence[Evidence]
    whole: bool
    events: Events
    sent: list[DraftSentence] = field(default_factory=list)

    def offer(self, raw: object) -> None:
        """One raw sentence, or a list: the parts of a sentence cut in code (``part_of``), which
        stand or fall together as in ``_kept``: one that fails, and none of them is sent."""
        texts = _texts(self.seen)
        said = {s.text for s in self.sent}
        ready: list[DraftSentence] = []
        for each in raw if isinstance(raw, list) else [raw]:
            try:
                draft = checked({"status": "answered", "sentences": [each]})
            except ModelOutputError:
                return  # a sentence the answer's parse refuses is never sent
            kept = _kept(draft, texts)
            if not kept:
                return
            if kept[0].text not in said and kept[0].text not in {s.text for s in ready}:
                ready.append(kept[0])
        if not ready:
            return
        by_n = {item.n: item for item in self.seen}
        numbered, every = _citations([*self.sent, *ready], by_n, self.whole)
        asked = voice.numbers(self.question)
        cited: list[list[Citation]] = []
        for sentence in ready:
            citations = [every[numbered[n] - 1] for n in sentence.evidence]
            backing = {numbered[n]: by_n[n].hit.text for n in sentence.evidence}
            if _unsupported(sentence.text, citations, asked, backing) is not None:
                return
            cited.append(citations)
        # shortcut: a preview citation is never marked hard_to_read (one more statement a
        # sentence); the answer that follows marks it, and replaces the preview.
        timing = _timing()
        if not timing.first_sentence_ms:
            timing.first_sentence_ms = (time.monotonic() - timing.start) * 1000
        for sentence, citations in zip(ready, cited, strict=True):
            self.sent.append(sentence)
            self.events.send(
                "sentence",
                {
                    "index": len(self.sent) - 1,
                    "text": sentence.text,
                    "citations": [citation.to_json() for citation in citations],
                },
            )


def _outage(result: object) -> bool:
    """C7 M1: the model could not be reached or could never take this request (a
    ``ModelUnavailable``, or a 400 ``bad_request``): the agent drops out. Any other
    failure is about its reply, and counts as dissent."""
    return isinstance(result, ModelUnavailable) or (
        isinstance(result, ModelOutputError) and result.code == "bad_request"
    )


def _texts(seen: Sequence[Evidence]) -> dict[int, str]:
    """The text of each piece of evidence a draft saw, by id, cleaned. Line by line:
    joined, "12" and a next line's "August 2026" would read as a date."""
    return {item.n: "\n".join(map(clean_text, item.hit.text.splitlines())) for item in seen}


def _kept(draft: Draft, texts: dict[int, str]) -> list[DraftSentence]:
    """The draft's sentences, cleaned, that ``verify`` keeps against ``texts``; none for
    not found. #535: the parts of a sentence cut in code (``part_of``) stand or fall together, so
    no part is shown without the rest of its sentence."""
    if draft.status != "answered":
        return []
    cleaned = [DraftSentence(clean_text(s.text), s.evidence, s.part_of) for s in draft.sentences]
    passed = verify([s for s in cleaned if s.text], texts)
    # verify keeps its sentences in order, each with its ids once: pair them back with the cleaned.
    found: list[DraftSentence | None] = []
    at = 0
    for sentence in cleaned:
        same = (
            bool(sentence.text)
            and at < len(passed)
            and passed[at].text == sentence.text
            and passed[at].evidence == list(dict.fromkeys(sentence.evidence))
        )
        found.append(passed[at] if same else None)
        at += same
    fallen = {s.part_of for s, f in zip(cleaned, found, strict=True) if f is None and s.part_of is not None}
    if fallen:
        _log.info("answer text dropped", extra={"code": "verify:cut_sentence_part"})
    kept: dict[str, DraftSentence] = {}
    for sentence, verified in zip(cleaned, found, strict=True):
        if verified is not None and sentence.part_of not in fallen:
            kept.setdefault(verified.text, verified)  # a repeated sentence once, the first kept
    return list(kept.values())


def agreement(
    lead: Sequence[DraftSentence], others: Sequence[Sequence[DraftSentence]]
) -> Literal["agreed", "not_confirmed"]:
    """C7 M2: whether every other agent's kept sentences confirm the lead's kept ones.
    A lead sentence's atoms are its numbers (``numbers_in``); another agent confirms it
    when its sentences that cite one of the same evidence ids hold every one of them. A
    sentence with no number counts neither way. "agreed" only when at least one lead
    sentence has a number and every other agent confirms every such sentence; an agent
    with no sentence (not found, a bad reply) confirms nothing. O(s * o * t)."""
    counted = [(numbers_in(s.text), set(s.evidence)) for s in lead]
    counted = [(atoms, cited) for atoms, cited in counted if atoms]
    if not counted:
        return "not_confirmed"
    for sentences in others:
        for atoms, cited in counted:
            held: set[Decimal] = set().union(
                *(numbers_in(t.text) for t in sentences if cited & set(t.evidence))
            )
            if not atoms <= held:
                return "not_confirmed"
    return "agreed"


def _numbers(question: str, keywords: Sequence[str], expansion: Expansion) -> list[str]:
    """The numbers asked: the question's own (``numbers_asked``), then those of what the
    user's words were searched as ("my plot" meaning "gat 47" asks 47). O(q)."""
    return list(dict.fromkeys([*numbers_asked(question, keywords), *expansion.numbers()]))


def _searched_as(question: str, normalised: NormalisedQuestion) -> tuple[NormalisedQuestion, str, str]:
    """``normalised`` with its content words only, the text searched ("": nothing to search) and
    the text embedded. Romanised question words and fillers (ahe, kiti) say nothing about the
    topic: they are left out of every step after, never out of normalise (PR 2b-2). A question
    typed in another script is searched (council 4 Oct) and embedded (W3) as asked: its Latin
    spelling means little."""
    normalised = replace(normalised, keywords=glossary.content_words(normalised.keywords))
    query_text = " ".join(normalised.keywords) or normalised.english
    if not query_text and has_other_script_letters(question):
        query_text = question.strip()
    spoken = question if has_other_script_letters(question) else normalised.english
    return normalised, query_text, spoken


def _focused(step: Step, asked: str, profile: Profile | None, scope: Scope) -> tuple[Step, str | None]:
    """Step 22 (focus.py): for a question ``asked`` that points back at the focus or asks for its
    related documents, and names no property or person of its own, the step with the focus's subject
    put in when its question (the rewrite's) still points back, and the line saying what was
    understood and where it looks (neither when the focus has no subject it may say); and the focus
    to leave out ("other document", or related documents), else None, never when the scope is the
    focus alone. The scope is never changed."""
    if profile is None or step.clarify is not None or names_subject(asked):  # it names its own subject
        return step, None
    if not (points_back(asked) or asks_related(asked)):
        return step, None
    only = scope.document_ids == (profile.document_id,)  # asked in the focus alone: never left out
    other = asks_other(asked) and not only
    question, said = step.question, subject(profile)
    if said and points_back(question):
        end = "?" if question.rstrip().endswith("?") else ""
        question = f"{question.rstrip(' ?.')} ({said}){end}"
    narrowed = replace(scope, timezone=EVERYTHING.timezone) != EVERYTHING
    line = understood(profile, narrowed=narrowed, other=other)
    exclude = profile.document_id if other or (asks_related(asked) and not only) else None
    said_lines = (*step.assumptions, line) if line else step.assumptions
    return replace(step, question=question, assumptions=said_lines), exclude


MAX_PART_CHARS = 120  # of a part, as its line quotes it


def _together(question: str, asked: Sequence[str], total: int, results: Sequence[Answer | None]) -> Answer:
    """Step 22: the parts' answers as one, part by part: a line quoting each part, then its own
    sentences, citations (numbered on), documents and checks; a part that found nothing, or was not
    started in time, or met an outage, says so in its line. Verified only when every part answered
    is. No part found: not found, with every part's closest documents and missing words. More than
    MAX_PARTS asked: says how many were answered. O(s + c) in the parts' sentences and citations."""
    found = [a for a in results if a is not None and a.status in ("answered", "unchecked")]
    sentences: list[AnswerSentence] = []
    citations: list[Citation] = []
    documents: dict[str, dict[str, Any]] = {}
    backing: dict[int, str] = {}
    said: list[str] = []
    for part, answer in zip(asked, results, strict=True):
        quoted = clean_text(part)
        if len(quoted) > MAX_PART_CHARS:
            quoted = quoted[:MAX_PART_CHARS].rsplit(" ", 1)[0] + " …"
        head = f'For "{quoted}":'
        if answer is None:
            sentences.append(
                AnswerSentence(f"{head} I couldn't check this in time; ask it again on its own.", [])
            )
            continue
        said += [line for line in answer.assumptions if line not in said]
        if answer.unavailable or answer.outage_reason == "timeout":  # KZ-006: an outage is no "not found"
            sentences.append(
                AnswerSentence(f"{head} I couldn't check this just now. Ask it again in a minute.", [])
            )
            continue
        if answer.status not in ("answered", "unchecked"):
            sentences.append(AnswerSentence(f"{head} I couldn't find this in your documents.", []))
            continue
        moved = len(citations)
        citations += [replace(c, n=c.n + moved) for c in answer.citations]
        sentences.append(AnswerSentence(head, []))
        sentences += [AnswerSentence(s.text, [n + moved for n in s.citations]) for s in answer.sentences]
        backing |= {n + moved: text for n, text in answer.backing.items()}
        for document in answer.documents:
            kept = documents.setdefault(document["document_id"], {**document, "citations": []})
            kept["citations"] = [*kept["citations"], *(n + moved for n in document["citations"])]
    if total > len(asked):
        said.append(
            f"I answered the first {len(asked)} of your {total} questions; ask the others on their own."
        )
    if not found:
        gone = [a for a in results if a is not None]
        closest = list({str(d["id"]): d for a in gone for d in a.closest_documents}.values())
        missing = tuple(dict.fromkeys(w for a in gone for w in a.missing_words))
        down = bool(gone) and all(a.unavailable for a in gone)
        reason = next((a.outage_reason for a in gone if a.outage_reason), None)
        answer = _not_found(
            "search",
            question.strip(),
            closest,
            model_used=any(a.model_used for a in gone),
            reason="notfound_parts",
        )
        return replace(
            answer,
            documents=list(documents.values())
            or [d for a in gone for d in a.documents][: voice.MAX_DOCUMENTS],
            assumptions=said,
            missing_words=missing,
            unavailable=down,
            outage_reason=reason if down else None,
            degraded=any(a.degraded for a in gone),
        )
    cited = {c.document_id for c in citations}
    also = {d["document_id"]: d for a in found for d in a.related_documents if d["document_id"] not in cited}
    notes = [a.reading_note for a in found if a.reading_note]
    return Answer(
        status="answered" if any(a.status == "answered" for a in found) else "unchecked",
        label="verified" if all(a.label == "verified" for a in found) else "needs_review",
        sentences=sentences,
        citations=citations,
        closest_documents=[],
        route="search" if any(a.route == "search" for a in found) else "fixed_query",
        normalized_question=question.strip(),
        model_used=any(a.model_used for a in found),
        understood_words=tuple(dict.fromkeys(w for a in found for w in a.understood_words)),
        reading_note=" ".join(dict.fromkeys(notes)) or None,
        team=next((a.team for a in found if a.team is not None), None),
        related_documents=list(also.values()),
        documents=list(documents.values()),
        assumptions=said,
        backing=backing,
        vouched=frozenset().union(*(a.vouched for a in found)),
        degraded=any(a.degraded for a in results if a is not None),
    )


def _one_document(scope: Scope) -> bool:
    """The scope names exactly one document (#617 item 5: then "which documents" is that one)."""
    return scope.document_ids is not None and len(scope.document_ids) == 1


def _search_text(aspect: str) -> str:
    """An aspect as the search reads it: its keywords, as the question's (``normalise``)."""
    return " ".join(glossary.content_words(normalise(aspect).keywords)) or aspect


MAX_TOPIC_WORDS = 3  # CX6: a topic name or alias of up to three words


def topic_terms(texts: Sequence[str]) -> list[str]:
    """CX6: every run of one to three words (``spaced_words``, so छुट्टी stays one word) of
    ``texts``, casefolded (topics and their aliases are stored so), and a word ending in "s"
    without it too ("leaves" finds the topic leave). O(n) for n words."""
    terms: list[str] = []
    for text in texts:
        words = spaced_words(text.casefold())
        for size in range(1, MAX_TOPIC_WORDS + 1):
            terms += (" ".join(words[i : i + size]) for i in range(len(words) - size + 1))
        terms += (word[:-1] for word in words if len(word) > 3 and word.endswith("s"))
    return list(dict.fromkeys(terms))


def _tokens(item: Evidence) -> int:
    """About how many prompt tokens a piece takes: its header and text, and its fence."""
    return estimate_tokens_cautiously(item.hit.context_header + item.hit.text) + _FENCE_TOKENS


def _translated_only(item: Evidence, translated: Translated, min_score: float) -> bool:
    """A cited hit (not a neighbour) only a translation found, or whose meaning score only a
    translation brought to ``min_score``."""
    if item.neighbour:
        return False
    if item.hit.piece_id not in translated.original:
        return True
    own, score = translated.original[item.hit.piece_id], item.hit.meaning_score
    return score is not None and score >= min_score and (own is None or own < min_score)


def _rests_on_neighbours(sentence: DraftSentence, by_n: dict[int, Evidence], texts: dict[int, str]) -> bool:
    """True when ``sentence`` cites no hit, or a number in it is in none of the hits it cites."""
    hits = [n for n in sentence.evidence if not by_n[n].neighbour]
    allowed: set[Decimal] = set().union(*(numbers_in(texts[n]) for n in hits))
    return not hits or not numbers_in(sentence.text) <= allowed


def _judged(
    sentence: DraftSentence,
    by_n: dict[int, Evidence],
    keywords: Sequence[str],
    min_score: float,
    numbers: Sequence[str],
) -> bool:
    """True when every hit the sentence cites passes the gate by itself, on its score or its
    English keywords, so each holds every number asked as a whole token: a sentence citing a
    gat 47 piece and a gat 48 page is never verified for "gat 48". O(c * k * t)."""
    hits = [by_n[n] for n in sentence.evidence if not by_n[n].neighbour]
    passes = ("score", "keywords")
    return all(enough_evidence([item], keywords, min_score, numbers) in passes for item in hits)


# English words any answer may use, whatever its evidence.
_FUNCTION_WORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "of",
        "for",
        "in",
        "on",
        "to",
        "and",
        "by",
        "this",
        "that",
        "it",
        "its",
        "has",
        "have",
        "as",
        "at",
        "from",
        "with",
    ]
)
_LATIN_TOKEN = re.compile(r"[a-z0-9]+")


def _latin_words(text: str) -> set[str]:
    """The Latin-letter words of ``text``, casefolded; a token with a digit is an id, not a word. O(n)."""
    return {token for token in _LATIN_TOKEN.findall(text.casefold()) if not any(ch.isdigit() for ch in token)}


def _unbacked_latin(sentence: DraftSentence, texts: dict[int, str], asked: set[str]) -> bool:
    """True when a text the sentence cites has letters of a script other than Latin (Devanagari,
    Gujarati...) and the sentence holds an English word that neither the question's dropped
    words (``asked``), ``_FUNCTION_WORDS``, the English side of a glossary word in the cited
    text, nor the cited texts themselves have: "The owner (भोगवटादार) is Sita Deshmukh" names
    nobody the evidence names. Fail closed: Latin words in the cited text ("Page 1 of 1",
    "GSTIN") only add to what is allowed; they never turn the check off. A word written the
    Romanised way (PR 2b) is backed only when its sound key equals that of a word of the cited
    text's Latin spelling ("Ramchandra" for रामचंद्र; never "Ramchandran",
    ``glossary.sounds_like`` strict); never by its glossary meaning (Malik, Kisan and Bhumika
    are names), nor because the question has it ("... is malak patil" for "malak kon ahe?").
    O(s * w + e * g) for w words and g glossary words."""
    cited = [texts[n] for n in sentence.evidence if n in texts]
    if not any(has_other_script_letters(text) for text in cited):
        return False
    found: set[str] = set().union(*(_latin_words(text) for text in cited))
    english = glossary.english_for("\n".join(cited))
    unbacked = _latin_words(sentence.text) - (asked | _FUNCTION_WORDS | found | english)
    if not unbacked:
        return False
    words = glossary.sound_words("\n".join(cited))
    return any(not glossary.sounds_like(word, words, strict=True) for word in unbacked)


def _quoted_through_glossary(
    sentence: DraftSentence, by_n: dict[int, Evidence], keywords: Sequence[str]
) -> bool:
    """True when a quoted line's piece holds a keyword only through a glossary equivalent or
    a word that sounds like it (not as written): they chose the quote, so it is never
    verified. O(k * t)."""
    pieces = [word_text(by_n[n].hit.text) for n in sentence.evidence]
    words = [keyword.casefold() for keyword in keywords if not any(ch.isdigit() for ch in keyword)]
    # A whole word, as the gate counts it; a Romanised word as written is a guess too.
    return any(
        glossary.is_romanised(word) or not has_word(word, text) for word in words for text in pieces
    )  # numbers: numbers_asked


def _not_found(
    route: Route, shown: str, closest: list[dict[str, Any]], *, model_used: bool, reason: str
) -> Answer:
    """A not-found answer, and one log line with its fixed ``reason`` code (never the
    question or page text): notfound_empty_scope, notfound_fixed_no_rows,
    notfound_fixed_no_sentence, notfound_empty_question, notfound_gate (no evidence
    passed), notfound_model (the model said so), notfound_model_error:<code> (it failed
    and the quoted lines held nothing), notfound_no_model (the same, model off),
    notfound_verify_dropped_all (the checker kept no sentence), notfound_final_check (PRD 6
    decision 2's check kept none); ":date" after notfound_model_error and notfound_no_model: a
    date question, never quoted; notfound_owners_part (land records match only part of an
    owner question)."""
    _log.info("answer not found", extra={"code": reason})
    _timing().reason = reason
    return Answer(
        status="not_found",
        label=None,
        sentences=[],
        citations=[],
        closest_documents=closest,
        route=route,
        normalized_question=shown,
        model_used=model_used,
    )


def _piece_citation(n: int, item: Evidence, sentences: Sequence[str]) -> Citation:
    hit = item.hit
    regions = sorted(_focus(item, sentences), key=lambda region: int(region["page"]))  # H4: by page
    return Citation(
        n=n,
        kind="piece",
        ref_id=hit.piece_id,
        document_id=hit.document_id,
        document_name=hit.document_name,
        detected_type=hit.detected_type,
        page_no=int(regions[0]["page"]),
        regions=regions,
        snippet=clean_snippet(hit.text),
        label=hit.label,
        page_from=hit.page_from,
        page_to=hit.page_to,
        label_page_no=None,
        label_regions=[],
    )


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _focus(item: Evidence, sentences: Sequence[str]) -> list[dict[str, Any]]:
    """The regions of the lines the answer used, so the page shows the exact spot.

    A text piece keeps one region per line, in order; a table piece or a sheet
    row one per cell, each row's cells joined by " | " (#382), so its cells pair
    up with its regions instead. A line (or cell) is used when it
    holds a sentence (or a sentence holds it), or shares a number with one.
    Falls back to every region when the lines and regions do not pair up, no
    line matches, or the lines used miss a page of a piece
    that crosses pages (H4: each of its pages shows its spot). Digits of any
    script read as ASCII on both sides, so "450" finds the line "४५०".
    O(lines * sentences).
    """
    lines = ascii_digits(item.hit.text).splitlines()
    if len(lines) != len(item.regions):
        lines = [cell for line in lines for cell in line.split(" | ")]
    if len(lines) != len(item.regions) or len(item.regions) != len(item.hit.regions):
        return item.regions
    folded = [ascii_digits(sentence).casefold().strip() for sentence in sentences]
    numbers = {n.replace(",", "") for sentence in folded for n in _NUMBER.findall(sentence)}
    used = []
    for line, region in zip(lines, item.regions, strict=True):
        text = line.casefold().strip()
        if not text:
            continue
        if any(text in sentence or sentence in text for sentence in folded if sentence) or any(
            n.replace(",", "") in numbers for n in _NUMBER.findall(text)
        ):
            used.append(region)
    if {region["page"] for region in used} != {region["page"] for region in item.regions}:
        return item.regions
    return used


def _value_citation(n: int, value: ValueRef, regions: list[dict[str, Any]]) -> Citation:
    return Citation(
        n=n,
        kind="value",
        ref_id=value.value_id,
        document_id=value.document_id,
        document_name=value.document_name,
        detected_type=value.detected_type,
        page_no=value.page_no,
        regions=regions,
        snippet=clean_snippet(f"{value.field}: {value.value_text}"),
        label=value.label,
        page_from=value.page_no,
        page_to=value.page_no,
        label_page_no=value.label_page_no,
        label_regions=valid_regions([value.label_region] if value.label_region else []),
    )


def _cited_values(rows: Sequence[FixedRow]) -> list[tuple[ValueRef, list[dict[str, Any]]]]:
    """The values behind the rows, each once, with a valid region, at most MAX_CITED_VALUES.

    Taken from the rows in turn, so every party named in a tie has a source.
    """
    cited: list[tuple[ValueRef, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for layer in zip_longest(*(row.values for row in rows)):
        for value in layer:
            if value is None:
                continue
            regions = valid_regions([value.region])
            if value.value_id in seen or not regions:
                continue
            seen.add(value.value_id)
            cited.append((value, regions))
    return cited[:MAX_CITED_VALUES]


def _fixed_sentence(plan: FixedPlan, top: Sequence[FixedRow]) -> str | None:
    """The template sentence. Every number in it comes from ``top``; None if it cannot be written."""
    period = describe_range(plan.start, plan.end) if plan.start and plan.end else ""
    number = indian_number(top[0].number)
    if plan.aggregate == "count":
        if plan.invoices:
            invoices = "1 invoice is" if top[0].number == 1 else f"{number} invoices are"
            return f"{invoices} present {period}." if period else f"{invoices} present."
        documents = "1 document is" if top[0].number == 1 else f"{number} documents are"
        return f"{documents} dated {period}." if period else f"{documents} dated."
    metric = _words(plan.metric or "")
    after = f", {period}" if period else ""
    if not plan.group_fields:
        return f"The {plan.aggregate} {metric} was {number}{after}."
    names = [clean_text(row.group or "")[:_MAX_NAME].strip() for row in top]
    if not all(names):
        return None
    each = " each" if len(names) > 1 else ""
    return f"{' and '.join(names)} had the {plan.aggregate} {metric}, {number}{each}{after}."


def _words(field: str) -> str:
    return field.replace("_", " ")
