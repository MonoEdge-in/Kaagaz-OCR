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
   the values used. No rows: not found.
   **Unchecked**: no plan because no checked value holds the figure, but an
   unchecked card value does: no number, a request to confirm it in Review, and
   the documents where it appears (status ``unchecked``, answering/unchecked.py).
5. **Search route**: embed the question (skipped without a model), retrieve,
   the evidence gate on the hits, then the hits' neighbours (Q4) after them,
   then a draft (the model, or quoting without one), then ``verify`` drops
   every sentence its evidence's text does not back.
6. ``sanitize`` on every sentence and snippet. The label is verified only
   when every value or piece behind the answer is verified, and no sentence
   rests on neighbours alone (PROVISIONAL, plan decision 8: the gate never
   judged them). Nor is it verified when the gate passed only through the
   glossary's Marathi and Hindi words, or a sentence citing Devanagari
   evidence was checked against nothing (``verify.unchecked_cross_script``;
   council 2 Oct 2026, PR2).

No source, no answer: an answered result always has at least one sentence,
every sentence cites at least one source, and every source has a region.

Nothing here logs the question or any document text: a model error is logged
as its code, and each model answer as the model's tag and ANSWER_PROMPT_HASH.

Time: one ``available_fields`` query only for a question with an aggregate
word and no unknown topic word, then either one fixed query or one search
(then one neighbours statement for the usable hits) plus at most one model
call; the rest is O(e * t) over e pieces of evidence of t characters. e is at
most ANSWER_MAX_EVIDENCE hits plus 2 * ANSWER_NEIGHBOURS neighbours per hit.
"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from itertools import zip_longest
from typing import Any, Literal
from zoneinfo import ZoneInfo

from kaagaz.ai.client import (
    ModelClient,
    ModelOutputError,
    ModelUnavailable,
    estimate_tokens,
    input_token_budget,
)
from kaagaz.answering.dates import date_range, describe_range, mentions_date
from kaagaz.answering.draft import (
    ANSWER_PROMPT_HASH,
    SYSTEM_PROMPT,
    Draft,
    DraftSentence,
    extractive_draft,
    fit_evidence,
    model_draft,
)
from kaagaz.answering.evidence import (
    Evidence,
    PgRetriever,
    Retriever,
    closest_documents,
    enough_evidence,
    number_evidence,
    valid_regions,
)
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, FixedQueries, FixedRow, PgFixedQueries, ValueRef
from kaagaz.answering.format import indian_number
from kaagaz.answering.hinglish import NormalisedQuestion, normalise
from kaagaz.answering.intent import FixedPlan, metric_asked, plan_fixed
from kaagaz.answering.rewrite import Turn, rewrite
from kaagaz.answering.sanitize import clean_snippet, clean_text
from kaagaz.answering.unchecked import PgUncheckedValues, UncheckedValues, unchecked_sentence
from kaagaz.answering.verify import numbers_in, unchecked_cross_script, verify
from kaagaz.chunking.script import ascii_digits, has_devanagari_letters
from kaagaz.db.engine import Database
from kaagaz.search.hybrid import MAX_LIMIT
from kaagaz.search.indexing import query_input, recipe_tag
from kaagaz.search.scope import EVERYTHING, Scope
from kaagaz.settings import Settings

_log = logging.getLogger(__name__)

# "unchecked": the figure asked for exists only as unchecked card values, so no
# number is given (kaagaz/answering/unchecked.py).
Status = Literal["answered", "not_found", "unchecked"]
Route = Literal["fixed_query", "search"]
_MAX_NAME = 100  # a party name longer than this is cut in the sentence
_REPLY_TOKENS = 1024  # PROVISIONAL: a draft's reply budget (num_predict), kept free of evidence
_FENCE_TOKENS = 10  # an evidence tag and its line breaks


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
        }


@dataclass(frozen=True, slots=True)
class AnswerSentence:
    text: str
    citations: list[int]


@dataclass(frozen=True, slots=True)
class Answer:
    status: Status
    label: str | None  # None when not found
    sentences: list[AnswerSentence]
    citations: list[Citation]
    closest_documents: list[dict[str, str]]  # {"id", "display_name"}
    route: Route
    normalized_question: str
    model_used: bool
    rewritten_question: str | None = None  # H1: what was searched for, when a follow-up was rewritten

    def to_json(self) -> dict[str, Any]:
        """The ``Answer`` of frontend/lib/types.ts, exactly."""
        return {
            "status": self.status,
            "label": self.label,
            "sentences": [{"text": s.text, "citations": list(s.citations)} for s in self.sentences],
            "citations": [citation.to_json() for citation in self.citations],
            "closest_documents": [
                {"id": doc["id"], "display_name": doc["display_name"]} for doc in self.closest_documents
            ],
            "route": self.route,
            "normalized_question": self.normalized_question,
            "model_used": self.model_used,
            "rewritten_question": self.rewritten_question,
        }


class AnswerService:
    """``retriever``, ``fixed_queries`` and ``unchecked`` default to the PostgreSQL ones;
    tests pass fakes."""

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
    ) -> None:
        self._model = model
        self._settings = settings
        self._clock = clock
        self._zone = ZoneInfo(settings.timezone)
        # What the answer's evidence may take: the window less the reply, its repair and the prompt.
        self._evidence_budget = input_token_budget(
            num_ctx=settings.model_num_ctx, max_tokens=settings.model_answer_max_tokens, system=SYSTEM_PROMPT
        )
        self._retriever = (
            retriever
            if retriever is not None
            else PgRetriever(
                db,
                recipe=recipe_tag(settings.model_embed),
                ef_search=settings.search_ef,
                neighbours=settings.answer_neighbours,
            )
        )
        self._fixed = fixed_queries if fixed_queries is not None else PgFixedQueries(db)
        self._unchecked = unchecked if unchecked is not None else PgUncheckedValues(db)

    def answer(
        self, customer_id: str, question: str, *, scope: Scope = EVERYTHING, history: Sequence[Turn] = ()
    ) -> Answer:
        """Answer ``question`` for ``customer_id`` only, from documents inside ``scope``;
        ``history`` is the conversation's earlier turns, oldest first."""
        if scope.matches_nothing:
            return _not_found("search", question.strip(), [], model_used=False)
        turns = self._settings.chat_history_turns
        rewritten = None
        if self._model is not None and history and turns:
            rewritten = rewrite(self._model, question, history[-turns:])
        return replace(self._answer(customer_id, rewritten or question, scope), rewritten_question=rewritten)

    def _answer(self, customer_id: str, question: str, scope: Scope) -> Answer:
        normalised = normalise(question)
        # "Hinglish understood as" is shown only when there was Hinglish to understand.
        shown = normalised.english if normalised.hinglish else question.strip()
        found_range = date_range(normalised, self._today())
        fixed = self._fixed.scoped(scope)
        fields = partial(fixed.available_fields, customer_id)  # asked for only if a plan could follow
        plan = plan_fixed(normalised, found_range, mentions_date(normalised), fields)
        if plan is not None:
            return self._fixed_answer(customer_id, plan, shown, fixed)
        unchecked = self._unchecked_answer(customer_id, normalised, shown, scope)
        if unchecked is not None:
            return unchecked
        return self._search_answer(customer_id, question, normalised, shown, scope)

    def _unchecked_answer(
        self, customer_id: str, normalised: NormalisedQuestion, shown: str, scope: Scope
    ) -> Answer | None:
        """A figure asked for that no checked value holds but a card does: no number,
        the documents where it appears, and a request to confirm it in Review first
        (decided 30 Sep 2026). None when no card holds it either."""
        asked = metric_asked(normalised)
        if asked is None:
            return None
        concept, fields = asked
        documents = self._unchecked.documents_with(customer_id, fields, scope=scope)
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

    def _fixed_answer(self, customer_id: str, plan: FixedPlan, shown: str, fixed: FixedQueries) -> Answer:
        rows = self._run_plan(fixed, customer_id, plan)
        if not rows:
            return _not_found("fixed_query", shown, [], model_used=False)
        top = [row for row in rows if row.number == rows[0].number]  # a tie is shown, never broken
        cited = _cited_values(top)
        sentence = _fixed_sentence(plan, top)
        if not cited or sentence is None:
            return _not_found("fixed_query", shown, [], model_used=False)
        citations = [
            Citation(
                n=index,
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
            for index, (value, regions) in enumerate(cited, start=1)
        ]
        verified = all(row.verified for row in top) and all(c.label == "verified" for c in citations)
        return Answer(
            status="answered",
            label="verified" if verified else "needs_review",
            sentences=[AnswerSentence(text=sentence, citations=[c.n for c in citations])],
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
        )

    def _run_plan(self, fixed: FixedQueries, customer_id: str, plan: FixedPlan) -> list[FixedRow]:
        if plan.aggregate == "count":
            return fixed.count_documents(customer_id, plan.start, plan.end, date_fields=plan.date_fields)
        if plan.metric is None:
            return []
        if plan.aggregate == "total":
            return fixed.total(customer_id, plan.metric, plan.date_fields, plan.start, plan.end)
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
            )
        return fixed.extreme_value(
            customer_id, plan.metric, plan.date_fields, plan.start, plan.end, descending
        )

    # --- search route -------------------------------------------------------

    def _search_answer(
        self, customer_id: str, question: str, normalised: NormalisedQuestion, shown: str, scope: Scope
    ) -> Answer:
        query_text = " ".join(normalised.keywords) or normalised.english
        if not query_text:
            return _not_found("search", shown, [], model_used=False)
        limit = max(1, min(self._settings.answer_max_evidence, MAX_LIMIT))
        # A Devanagari question is embedded as asked (W3): its Latin spelling means little to the model.
        meant = question if has_devanagari_letters(question) else normalised.english
        hits = self._retriever.search(customer_id, query_text, self._embed(meant), limit, scope)
        evidence = number_evidence(hits)
        # Only usable evidence counts as close: an instruction-like piece is not.
        closest = closest_documents([item.hit for item in evidence])
        gate = enough_evidence(evidence, normalised.keywords, self._settings.answer_min_evidence_score)
        if gate is None:
            return _not_found("search", shown, closest, model_used=False)
        # Q4: neighbours only after the gate passed on the hits, numbered after all of them,
        # while they fit the room the hits and the question leave in MODEL_NUM_CTX: every
        # hit is kept, the neighbours go first. ponytail: fit_evidence (perf/s2-query-wins)
        # replaces this cut at the rebase.
        room = input_token_budget(
            num_ctx=self._settings.model_num_ctx, max_tokens=_REPLY_TOKENS, system=SYSTEM_PROMPT
        )
        room -= estimate_tokens(question) + sum(_tokens(item) for item in evidence)
        near = self._retriever.neighbours(customer_id, [item.hit for item in evidence], scope)
        for item in number_evidence(near, start=len(evidence) + 1, neighbour=True):
            room -= _tokens(item)
            if room < 0:
                break
            evidence.append(item)

        draft, model_used, seen = self._draft(question, normalised, evidence)
        # Only the evidence the draft saw. Line by line: joined, "12" and a next
        # line's "August 2026" would read as a date.
        texts = {item.n: "\n".join(map(clean_text, item.hit.text.splitlines())) for item in seen}
        cleaned = [DraftSentence(clean_text(s.text), s.evidence) for s in draft.sentences]
        kept = verify([s for s in cleaned if s.text], texts) if draft.status == "answered" else []
        if not kept:
            return _not_found("search", shown, closest, model_used=model_used)

        renumber: dict[int, int] = {}
        for sentence in kept:
            for n in sentence.evidence:
                renumber.setdefault(n, len(renumber) + 1)
        by_n = {item.n: item for item in evidence}
        citations = [
            _piece_citation(new, by_n[old], [s.text for s in kept if old in s.evidence])
            for old, new in renumber.items()
        ]
        # A sentence backed by neighbours only, or with a number only a neighbour holds, is never
        # verified: the gate never judged them (PROVISIONAL, plan decision 8).
        on_neighbours = any(_rests_on_neighbours(s, by_n, texts) for s in kept)
        # Nor is one the glossary alone let through or chose the quote of, one checked against
        # nothing, or one no hit it cites passes the gate on its own (council, PR2).
        min_score = self._settings.answer_min_evidence_score
        unchecked = (
            gate == "glossary"
            or any(not _judged(s, by_n, normalised.keywords, min_score) for s in kept)
            or any(unchecked_cross_script(s, texts) for s in kept)
            or (not model_used and any(_quoted_through_glossary(s, by_n, normalised.keywords) for s in kept))
        )
        verified = all(c.label == "verified" for c in citations) and not (on_neighbours or unchecked)
        return Answer(
            status="answered",
            label="verified" if verified else "needs_review",
            sentences=[AnswerSentence(s.text, [renumber[n] for n in s.evidence]) for s in kept],
            citations=citations,
            closest_documents=closest,
            route="search",
            normalized_question=shown,
            model_used=model_used,
        )

    def _embed(self, english: str) -> list[float] | None:
        """The question's vector, or None (no model, model down, or unusable
        vectors), so search falls back to word and fuzzy matching. One model call,
        with the query instruction (kaagaz.search.indexing.query_input, PRD 3 V3)."""
        if self._model is None or not english:
            return None
        try:
            vectors = self._model.embed([query_input(english)])
        except (ModelUnavailable, ModelOutputError) as error:
            _log.warning("question searched without its meaning vector", extra={"code": error.code})
            return None
        return vectors[0] if vectors else None

    def _draft(
        self, question: str, normalised: NormalisedQuestion, evidence: Sequence[Evidence]
    ) -> tuple[Draft, bool, Sequence[Evidence]]:
        """The model's draft (and True), or the extractive one (and False), with the
        evidence it was drafted from: for the model, the best pieces that fit its window."""
        if self._model is not None:
            asked = f"{question} (in English: {normalised.english})" if normalised.hinglish else question
            seen = fit_evidence(asked, evidence, self._evidence_budget)
            code = "evidence_too_large"  # not even the best piece fits
            if seen:
                model, max_tokens = self._model, self._settings.model_answer_max_tokens
                try:
                    draft = model_draft(model, asked, seen, max_tokens=max_tokens)
                except (ModelUnavailable, ModelOutputError) as error:
                    code = error.code
                else:
                    _log.info("answer drafted by %s with prompt %s", model.chat_model, ANSWER_PROMPT_HASH)
                    return draft, True, seen
            # Quoting the evidence needs no model.
            _log.warning("answer drafted without the model", extra={"code": code})
        if mentions_date(normalised):
            return Draft("not_found", []), False, evidence  # a quoted line cannot be checked against a date
        return extractive_draft(normalised.keywords, evidence), False, evidence

    def _today(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("the clock must give a timezone-aware time")
        return now.astimezone(self._zone).date()


def _tokens(item: Evidence) -> int:
    """About how many prompt tokens a piece takes: its header and text, and its fence."""
    return estimate_tokens(item.hit.context_header + item.hit.text) + _FENCE_TOKENS


def _rests_on_neighbours(sentence: DraftSentence, by_n: dict[int, Evidence], texts: dict[int, str]) -> bool:
    """True when ``sentence`` cites no hit, or a number in it is in none of the hits it cites."""
    hits = [n for n in sentence.evidence if not by_n[n].neighbour]
    allowed: set[Decimal] = set().union(*(numbers_in(texts[n]) for n in hits))
    return not hits or not numbers_in(sentence.text) <= allowed


def _judged(
    sentence: DraftSentence, by_n: dict[int, Evidence], keywords: Sequence[str], min_score: float
) -> bool:
    """True when one hit the sentence cites passes the gate by itself, on its score or its
    English keywords, so it holds every number asked as a whole token: a sentence citing a
    gat 47 piece and a receipt for Rs 48 is never verified for "gat 48". O(c * k * t)."""
    hits = [by_n[n] for n in sentence.evidence if not by_n[n].neighbour]
    return any(enough_evidence([item], keywords, min_score) in ("score", "keywords") for item in hits)


def _quoted_through_glossary(
    sentence: DraftSentence, by_n: dict[int, Evidence], keywords: Sequence[str]
) -> bool:
    """True when a quoted line's piece holds a keyword only through a glossary equivalent:
    the glossary chose the quote, so it is never verified. O(k * t)."""
    pieces = [ascii_digits(by_n[n].hit.text).casefold() for n in sentence.evidence]
    return any(keyword.casefold() not in text for keyword in keywords for text in pieces)


def _not_found(route: Route, shown: str, closest: list[dict[str, str]], *, model_used: bool) -> Answer:
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

    A text piece keeps one region per line, in order. A line is used when it
    holds a sentence (or a sentence holds it), or shares a number with one.
    Falls back to every region when the lines and regions do not pair up (a
    spreadsheet row), no line matches, or the lines used miss a page of a piece
    that crosses pages (H4: each of its pages shows its spot). Digits of any
    script read as ASCII on both sides, so "450" finds the line "४५०".
    O(lines * sentences).
    """
    lines = ascii_digits(item.hit.text).splitlines()
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
        documents = "1 document is" if top[0].number == 1 else f"{number} documents are"
        return f"{documents} dated {period}." if period else f"{documents} dated."
    metric = _words(plan.metric or "")
    if plan.aggregate == "total":
        metric = metric.removeprefix("total ")
        records = sum(row.records for row in top)
        counted = "1 record" if records == 1 else f"{indian_number(records)} records"
        return f"The total {metric}{' ' + period if period else ''} was {number}, from {counted}."
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
