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
    evidence is every piece in reading order, and the gate runs over all of them; no
    search, aspect, topic, section or neighbour. Otherwise the rewrite also lists up to
    QUERY_ASPECTS_MAX aspects (even with no history), each searched with the question
    and fused (``TranslatedSearch``, one batched embed); after the gate, the pieces of the
    topics the question, an aspect or a hit names (TOPIC_EXPAND_MAX_PIECES a document)
    and of the two best sections come after the hits, then the neighbours. Each document
    that supplies the model's evidence adds one card line, "AI summary, not evidence",
    with no id, so it is never cited. M4: the evidence is cut from its end (neighbours,
    then topic and section pieces, then hits), and the cards before any of it.
11. Memory (PRD 5 M3, kaagaz/answering/memory.py): the conversation's summary and memory
    go to the rewrite only; ``remember`` writes a new one.

No source, no answer: an answered result always has at least one sentence,
every sentence cites at least one source, and every source has a region.

Nothing here logs the question or any document text: a model error is logged
as its code, and each model answer as the model's tag and ANSWER_PROMPT_HASH.
Every not-found answer logs one line, "answer not found", with a fixed reason
code only (``_not_found``'s ``reason``).

Time: one ``available_fields`` query only for a question with an aggregate
word and no unknown topic word, then either one fixed query or one search
(then one neighbours statement for the usable hits) plus at most one model
call; the rest is O(e * t) over e pieces of evidence of t characters. e is at
most ANSWER_MAX_EVIDENCE hits plus 2 * ANSWER_NEIGHBOURS neighbours per hit.
"""

import logging
import re
from collections.abc import Callable, Iterator, Sequence
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
    estimate_tokens_cautiously,
    input_token_budget,
)
from kaagaz.answering import glossary
from kaagaz.answering.dates import date_range, describe_range, mentions_date
from kaagaz.answering.draft import (
    ANSWER_PROMPT_HASH,
    SYSTEM_PROMPT,
    Draft,
    DraftSentence,
    card_line,
    extractive_draft,
    fit_cards,
    fit_evidence,
    model_draft,
)
from kaagaz.answering.evidence import (
    CAPPED,
    Context,
    Evidence,
    Gate,
    Hit,
    PgRetriever,
    Retriever,
    Translated,
    TranslatedSearch,
    any_hit_scores,
    closest_documents,
    enough_evidence,
    has_word,
    looks_like_instructions,
    number_evidence,
    numbers_asked,
    valid_regions,
    word_text,
)
from kaagaz.answering.expansion import NO_EXPANSION, Expansion, PgWords, WordSource, expand
from kaagaz.answering.fixed_queries import MAX_CITED_VALUES, FixedQueries, FixedRow, PgFixedQueries, ValueRef
from kaagaz.answering.format import indian_number
from kaagaz.answering.hard_to_read import (
    HARD_TO_READ_NOTE,
    FlaggedPage,
    PgReadingQuality,
    ReadingQuality,
    hard_to_read,
    not_found_note,
)
from kaagaz.answering.hinglish import NormalisedQuestion, normalise
from kaagaz.answering.intent import FixedPlan, metric_asked, plan_fixed
from kaagaz.answering.memory import NO_MEMORY, Memory, allowed_tokens, prompt_text, summarise
from kaagaz.answering.rewrite import MAX_REPLY_TOKENS as REWRITE_REPLY_TOKENS
from kaagaz.answering.rewrite import SYSTEM_PROMPT as REWRITE_PROMPT
from kaagaz.answering.rewrite import Turn, rewrite
from kaagaz.answering.sanitize import clean_snippet, clean_text
from kaagaz.answering.translate import target_scripts, translate
from kaagaz.answering.unchecked import PgUncheckedValues, UncheckedValues, unchecked_sentence
from kaagaz.answering.verify import numbers_in, unchecked_cross_script, verify
from kaagaz.chunking.script import ascii_digits, has_other_script_letters
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
    hard_to_read: bool = False  # PRD 5 F6: the cited text holds an unsure or illegible word

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
    understood_words: tuple[tuple[str, str], ...] = ()  # My words used: (word, meaning)
    # PRD 5 F6: the note on pages that couldn't be read, the pages it names, and how many in all.
    reading_note: str | None = None
    unreadable_pages: tuple[FlaggedPage, ...] = ()
    unreadable_page_count: int = 0

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
            "understood_words": [{"word": w, "meaning": m} for w, m in self.understood_words],
            "reading_note": self.reading_note,
            "unreadable_pages": [page.to_json() for page in self.unreadable_pages],
            "unreadable_page_count": self.unreadable_page_count,
        }


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
        )
        self._retriever: Retriever = pg if retriever is None else retriever
        self._translated: TranslatedSearch | None = pg if retriever is None else translated_search
        self._fixed = fixed_queries if fixed_queries is not None else PgFixedQueries(db)
        self._unchecked = unchecked if unchecked is not None else PgUncheckedValues(db)
        self._words = words if words is not None else PgWords(db)
        self._reading: ReadingQuality | None = PgReadingQuality(db) if retriever is None else reading
        self._context: Context | None = pg if retriever is None else context

    def answer(
        self,
        customer_id: str,
        question: str,
        *,
        scope: Scope = EVERYTHING,
        history: Sequence[Turn] = (),
        user_id: str | None = None,
        memory: Memory = NO_MEMORY,
    ) -> Answer:
        """Answer ``question`` for ``customer_id`` only, from documents inside ``scope``;
        ``history`` is the conversation's earlier turns, oldest first; ``user_id``, the
        asker, whose own words (only) may help the search (None: nobody's); ``memory``, the
        conversation's (M3), for the rewrite only."""
        if scope.matches_nothing:
            return _not_found("search", question.strip(), [], model_used=False, reason="notfound_empty_scope")
        whole = self._whole(customer_id, scope)
        turns = self._settings.chat_history_turns
        recent = history[-turns:] if turns else ()
        remembered = prompt_text(memory)
        rewritten: str | None = None
        aspects: tuple[str, ...] = ()
        # CX6: the aspects are asked for, even on a first question, when they can be searched.
        most = self._settings.query_aspects_max if whole is None and self._widens() else 0
        if self._model is not None and (recent or remembered or most):
            rewritten, aspects = rewrite(
                self._model,
                question,
                recent,
                memory=remembered,
                allowed=allowed_tokens(memory),
                aspects_max=most,
                budget=self._rewrite_budget,
            )
        answer = self._answer(customer_id, rewritten or question, scope, user_id, whole, aspects)
        return replace(self._unreadable(customer_id, answer, scope), rewritten_question=rewritten)

    def remember(self, turns: Sequence[Turn], memory: Memory, cited: Sequence[str]) -> Memory | None:
        """M3: the conversation's new summary and memory from ``memory`` and the ``turns``
        since (oldest first), with the documents ``cited`` in them; None with no model, or
        when it is down or replies badly. One model call (kaagaz/answering/memory.py)."""
        return None if self._model is None else summarise(self._model, turns, memory, cited)

    def _widens(self) -> bool:
        """CX6's steps can run: a context port and a fused search to search aspects with."""
        return self._context is not None and self._translated is not None

    def _whole(self, customer_id: str, scope: Scope) -> Sequence[Hit] | None:
        """CX5: every piece of ``scope``, in reading order, when the model is on and they fit
        ANSWER_WHOLE_DOC_TOKENS, counted cautiously as the evidence is; None otherwise. The
        port's size check (characters / 4) comes first, so a big scope reads no text."""
        budget = self._settings.answer_whole_doc_tokens
        if self._model is None or self._context is None or budget <= 0:
            return None
        pieces = self._context.whole(customer_id, scope, budget)
        if pieces is None or sum(_tokens(item) for item in number_evidence(pieces)) > budget:
            return None
        return pieces

    def _unreadable(self, customer_id: str, answer: Answer, scope: Scope) -> Answer:
        """F6: a not-found answer names the pages in ``scope`` that couldn't be read well
        (unreadable or uncertain, kept as is or not), with the note; any other answer, or
        no such page, is as it was. One statement for a not-found answer."""
        if answer.status != "not_found" or self._reading is None:
            return answer
        pages, total = self._reading.flagged_pages(customer_id, scope=scope)
        note = not_found_note(pages, total)
        if note is None:
            return answer
        return replace(answer, reading_note=note, unreadable_pages=tuple(pages), unreadable_page_count=total)

    def _marked(
        self, customer_id: str, citations: list[Citation], regions: dict[int, list[dict[str, Any]]]
    ) -> list[Citation]:
        """F6: each citation ``hard_to_read`` when a word mark sits inside the cited text,
        at ``regions`` (by citation number: a piece's every line, a value's box). One
        statement for the cited documents' word marks."""
        if self._reading is None or not citations:
            return citations
        marks = self._reading.word_marks(customer_id, [citation.document_id for citation in citations])
        if not marks:
            return citations
        return [
            replace(citation, hard_to_read=hard_to_read(citation.document_id, regions[citation.n], marks))
            for citation in citations
        ]

    def _answer(
        self,
        customer_id: str,
        question: str,
        scope: Scope,
        user_id: str | None,
        whole: Sequence[Hit] | None = None,
        aspects: Sequence[str] = (),
    ) -> Answer:
        """``whole``: every piece of the scope (CX5), or None to search; ``aspects``: CX6's."""
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
        # My words: read once, for this question and this user only, never cached.
        words = () if user_id is None else self._words.words(customer_id, user_id)
        expansion = expand(question, words) if words else NO_EXPANSION
        answer = self._search_answer(
            customer_id, question, normalised, shown, scope, expansion, whole, aspects
        )
        if not expansion.used:
            return answer
        # The note, and never verified whatever passed (council 3 Oct 2026, A3).
        label = None if answer.label is None else "needs_review"
        return replace(answer, label=label, understood_words=expansion.understood())

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
            return _not_found("fixed_query", shown, [], model_used=False, reason="notfound_fixed_no_rows")
        top = [row for row in rows if row.number == rows[0].number]  # a tie is shown, never broken
        cited = _cited_values(top)
        sentence = _fixed_sentence(plan, top)
        if not cited or sentence is None:
            return _not_found("fixed_query", shown, [], model_used=False, reason="notfound_fixed_no_sentence")
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
        citations = self._marked(customer_id, citations, {c.n: c.regions for c in citations})
        hard = any(c.hard_to_read for c in citations)
        verified = all(row.verified for row in top) and all(c.label == "verified" for c in citations)
        return Answer(
            status="answered",
            label="verified" if verified and not hard else "needs_review",
            sentences=[AnswerSentence(text=sentence, citations=[c.n for c in citations])],
            citations=citations,
            closest_documents=[],
            route="fixed_query",
            normalized_question=shown,
            model_used=False,
            reading_note=HARD_TO_READ_NOTE if hard else None,
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
        self,
        customer_id: str,
        question: str,
        normalised: NormalisedQuestion,
        shown: str,
        scope: Scope,
        expansion: Expansion,
        whole: Sequence[Hit] | None = None,
        aspects: Sequence[str] = (),
    ) -> Answer:
        # Romanised question words and fillers (ahe, kiti) say nothing about the topic: they
        # are left out of every step below, never out of normalise (PR 2b-2).
        normalised = replace(normalised, keywords=glossary.content_words(normalised.keywords))
        query_text = " ".join(normalised.keywords) or normalised.english
        if not query_text and has_other_script_letters(question):
            query_text = question.strip()  # typed in another script: searched as asked (council 4 Oct)
        if not query_text:
            return _not_found("search", shown, [], model_used=False, reason="notfound_empty_question")
        numbers = _numbers(question, normalised.keywords, expansion)
        min_score = self._settings.answer_min_evidence_score
        translated: Translated | None = None
        first: Gate | None = None  # how the question's own search passed the gate, when translated
        vector: list[float] | None = None
        if whole is not None:
            evidence = number_evidence(whole)  # CX5: every piece, in reading order
        else:
            # A question in another script is embedded as asked (W3): its Latin spelling means
            # little. Its aspects (CX6) are embedded with it, in one call.
            spoken = question if has_other_script_letters(question) else normalised.english
            vector, *meanings = self._embed_all([spoken, *aspects])
            extra = [(_search_text(text), meaning) for text, meaning in zip(aspects, meanings, strict=True)]
            hits = self._hits(customer_id, query_text, vector, extra, scope, expansion)
            translated = self._translated_search(
                customer_id, question, query_text, vector, hits, scope, expansion, extra
            )
            if translated is not None:
                own = number_evidence(hits)
                first = enough_evidence(own, normalised.keywords, min_score, numbers, expansion)
                hits = translated.hits
            evidence = number_evidence(hits)
        # Only usable evidence counts as close: an instruction-like piece is not.
        closest = closest_documents([item.hit for item in evidence])
        gate = enough_evidence(evidence, normalised.keywords, min_score, numbers, expansion)
        if gate is None and not (translated is not None and any_hit_scores(evidence, min_score, numbers)):
            return _not_found("search", shown, closest, model_used=False, reason="notfound_gate")
        if whole is None:
            terms = [question, *aspects]
            widened = self._widened(customer_id, terms, normalised.keywords, vector, evidence, scope)
            evidence += number_evidence(widened, start=len(evidence) + 1)
            # Q4: neighbours only after the gate passed on the hits, numbered after all of them,
            # while they fit the room the evidence and the question leave in MODEL_CONTEXT_TOKENS
            # (M1): every hit is kept, the neighbours go first.
            room = input_token_budget(
                num_ctx=self._settings.model_context_tokens, max_tokens=_REPLY_TOKENS, system=SYSTEM_PROMPT
            )
            room -= estimate_tokens_cautiously(question) + sum(_tokens(item) for item in evidence)
            near = self._retriever.neighbours(customer_id, [item.hit for item in evidence], scope)
            for item in number_evidence(near, start=len(evidence) + 1, neighbour=True):
                room -= _tokens(item)
                if room < 0:
                    break
                evidence.append(item)

        drafted = self._draft(customer_id, scope, question, normalised, evidence, expansion)
        draft, model_used, seen, why = drafted
        # Only the evidence the draft saw. Line by line: joined, "12" and a next
        # line's "August 2026" would read as a date.
        texts = {item.n: "\n".join(map(clean_text, item.hit.text.splitlines())) for item in seen}
        cleaned = [DraftSentence(clean_text(s.text), s.evidence) for s in draft.sentences]
        kept = verify([s for s in cleaned if s.text], texts) if draft.status == "answered" else []
        if not kept:
            why = why if draft.status != "answered" else "notfound_verify_dropped_all"
            return _not_found("search", shown, closest, model_used=model_used, reason=why)

        renumber: dict[int, int] = {}
        for sentence in kept:
            for n in sentence.evidence:
                renumber.setdefault(n, len(renumber) + 1)
        by_n = {item.n: item for item in evidence}
        citations = [
            _piece_citation(new, by_n[old], [s.text for s in kept if old in s.evidence])
            for old, new in renumber.items()
        ]
        # F6: the cited text is the whole piece, every line of it.
        lines = {new: by_n[old].regions for old, new in renumber.items()}
        citations = self._marked(customer_id, citations, lines)
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
        )
        verified = all(c.label == "verified" for c in citations) and not (on_neighbours or unchecked or hard)
        return Answer(
            status="answered",
            label="verified" if verified else "needs_review",
            sentences=[AnswerSentence(s.text, [renumber[n] for n in s.evidence]) for s in kept],
            citations=citations,
            closest_documents=closest,
            route="search",
            normalized_question=shown,
            model_used=model_used,
            reading_note=HARD_TO_READ_NOTE if hard else None,
        )

    def _translated_search(
        self,
        customer_id: str,
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
        limit = max(1, min(self._settings.answer_max_evidence, MAX_LIMIT))
        return self._translated.search_translated(
            customer_id, query_text, vector, texts, limit, scope, expansion
        )

    def _hits(
        self,
        customer_id: str,
        query_text: str,
        vector: list[float] | None,
        extra: Sequence[tuple[str, list[float] | None]],
        scope: Scope,
        expansion: Expansion,
    ) -> Sequence[Hit]:
        """The search's hits: the question's, fused with its aspects' (CX6) when it has any."""
        limit = max(1, min(self._settings.answer_max_evidence, MAX_LIMIT))
        if extra and self._translated is not None:
            fused = self._translated.search_translated
            return fused(customer_id, query_text, vector, extra, limit, scope, expansion).hits
        return self._retriever.search(customer_id, query_text, vector, limit, scope, expansion)

    def _widened(
        self,
        customer_id: str,
        texts: Sequence[str],
        keywords: Sequence[str],
        vector: list[float] | None,
        evidence: Sequence[Evidence],
        scope: Scope,
    ) -> list[Hit]:
        """CX6: the pieces of the topics ``texts`` (the question and its aspects) or the hits
        name, then of the two sections the keywords or the vector find, each once and never a
        hit. Two statements; none without the context port."""
        if self._context is None:
            return []
        hits = [item.hit for item in evidence]
        topics = self._context.topic_pieces(customer_id, topic_terms(texts), hits, scope)
        sections = self._context.section_pieces(customer_id, keywords, vector, [*hits, *topics], scope)
        return [*topics, *sections]

    def _embed(self, english: str) -> list[float] | None:
        """One text's vector (``_embed_all``)."""
        return self._embed_all([english])[0]

    def _embed_all(self, texts: Sequence[str]) -> list[list[float] | None]:
        """Each text's vector, or None (no model, model down, unusable vectors, or an empty
        text), so search falls back to word and fuzzy matching. One model call for them
        all, each in MODEL_EMBED's query input (kaagaz.search.indexing.query_input, PRD 3
        V3, PRD 5 M5)."""
        wanted = [text for text in texts if text]
        if self._model is None or not wanted:
            return [None] * len(texts)
        model_embed = self._settings.model_embed
        try:
            vectors = self._model.embed([query_input(text, model_embed) for text in wanted])
        except (ModelUnavailable, ModelOutputError) as error:
            _log.warning("question searched without its meaning vector", extra={"code": error.code})
            return [None] * len(texts)
        if len(vectors) != len(wanted):
            return [None] * len(texts)
        found: Iterator[list[float]] = iter(vectors)
        return [next(found) if text else None for text in texts]

    def _cards(self, customer_id: str, scope: Scope, seen: Sequence[Evidence]) -> list[str]:
        """M2: one card line for each document of ``seen``, in its order; none without the
        context port. A line that reads like instructions is left out. One statement."""
        if self._context is None or not seen:
            return []
        documents = list(dict.fromkeys(item.hit.document_id for item in seen))
        found = self._context.cards(customer_id, documents, scope)
        lines = [card_line(*found[document]) for document in documents if document in found]
        return [line for line in lines if line is not None and not looks_like_instructions(line)]

    def _draft(
        self,
        customer_id: str,
        scope: Scope,
        question: str,
        normalised: NormalisedQuestion,
        evidence: Sequence[Evidence],
        expansion: Expansion = NO_EXPANSION,
    ) -> tuple[Draft, bool, Sequence[Evidence], str]:
        """The model's draft (and True), or the extractive one (and False), with the
        evidence it was drafted from: for the model, the best pieces that fit its window,
        and the reason code a not-found draft would give.
        The model sees the question, the evidence and its documents' cards (M2, while they
        fit) only, never a user's word or meaning; ``expansion`` helps the extractive draft
        choose its lines."""
        if self._model is not None:
            asked = f"{question} (in English: {normalised.english})" if normalised.hinglish else question
            seen = fit_evidence(asked, evidence, self._evidence_budget)
            code = "evidence_too_large"  # not even the best piece fits
            if seen:
                model, max_tokens = self._model, self._settings.model_answer_max_tokens
                cards = fit_cards(asked, seen, self._cards(customer_id, scope, seen), self._evidence_budget)
                try:
                    draft = model_draft(model, asked, seen, max_tokens=max_tokens, cards=cards)
                except (ModelUnavailable, ModelOutputError) as error:
                    code = error.code
                else:
                    _log.info("answer drafted by %s with prompt %s", model.chat_model, ANSWER_PROMPT_HASH)
                    return draft, True, seen, "notfound_model"
            # Quoting the evidence needs no model.
            _log.warning("answer drafted without the model", extra={"code": code})
        why = "notfound_no_model" if self._model is None else f"notfound_model_error:{code}"
        if mentions_date(normalised):
            # A quoted line cannot be checked against a date.
            return Draft("not_found", []), False, evidence, f"{why}:date"
        return (
            extractive_draft(
                normalised.keywords, evidence, _numbers(question, normalised.keywords, expansion), expansion
            ),
            False,
            evidence,
            why,
        )

    def _today(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("the clock must give a timezone-aware time")
        return now.astimezone(self._zone).date()


def _numbers(question: str, keywords: Sequence[str], expansion: Expansion) -> list[str]:
    """The numbers asked: the question's own (``numbers_asked``), then those of what the
    user's words were searched as ("my plot" meaning "gat 47" asks 47). O(q)."""
    return list(dict.fromkeys([*numbers_asked(question, keywords), *expansion.numbers()]))


def _search_text(aspect: str) -> str:
    """An aspect as the search reads it: its keywords, as the question's (``normalise``)."""
    return " ".join(glossary.content_words(normalise(aspect).keywords)) or aspect


_WORDS = re.compile(r"\w+")
MAX_TOPIC_WORDS = 3  # CX6: a topic name or alias of up to three words


def topic_terms(texts: Sequence[str]) -> list[str]:
    """CX6: every run of one to three words of ``texts``, casefolded (topics and their
    aliases are stored so), and a word ending in "s" without it too ("leaves" finds the
    topic leave). O(n) for n words."""
    terms: list[str] = []
    for text in texts:
        words = _WORDS.findall(text.casefold())
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
    route: Route, shown: str, closest: list[dict[str, str]], *, model_used: bool, reason: str
) -> Answer:
    """A not-found answer, and one log line with its fixed ``reason`` code (never the
    question or page text): notfound_empty_scope, notfound_fixed_no_rows,
    notfound_fixed_no_sentence, notfound_empty_question, notfound_gate (no evidence
    passed), notfound_model (the model said so), notfound_model_error:<code> (it failed
    and the quoted lines held nothing), notfound_no_model (the same, model off),
    notfound_verify_dropped_all (the checker kept no sentence); ":date" after the
    last two: a date question, never quoted."""
    _log.info("answer not found", extra={"code": reason})
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
