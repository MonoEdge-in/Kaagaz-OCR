"""Who each model call is billed to (model choice, council 11 Oct 2026, SP1).

``usage_scope`` opens a scope for one piece of work (a document's reading, a chat answer, a
search): the customer, the user who asked (None for work no user asked for), the kind of
work and the document or conversation. The model client calls ``record_call`` for every
reply it is billed for, and the calls add up in the scope that is current: a ContextVar, so
a thread started with ``contextvars.copy_context().run`` adds to the same scope (its tallies
are kept under a lock). A call with no scope open is not recorded (verify-models, tests).

``billed_as(kind)`` bills the calls made inside as another kind of the same work (features
25-27, council 15 Oct 2026, C8): Dynamic's ``careful`` re-reads, Ensemble's
``ensemble_scan`` page reads and ``ensemble_chat`` drafts, and #488's ``ulc_scan`` (a page read
under Ultra low cost, its limit hits on its row), so the operator sees them apart from ``scan``
and ``chat``. It is a ContextVar too, so it reaches threads started with
``copy_context().run`` inside it.

When the scope ends, with or without an error (the calls were billed either way), its
tallies are saved in one INSERT in ``db.customer()``: one row per (model, kind, cached,
thinking, resolution). #463 (Tushar, 31 Oct 2026): a page read (PAGE_READ_KINDS) records the
thinking level and picture detail it was sent at, so the operator's estimate measures a page
at given settings; every other kind records neither. #463 review: a page read is counted once
on its row (``record_page``), however many calls it took, so the estimate prices a page.
A failure to save logs ``usage_not_saved`` and never fails the work. A scope opened inside
another is its own scope; the outer one is current again when it ends.

The reading pause's live counter (#478, Tushar, 3 Nov 2026; ``db.reading_days``): each page read
(``record_page``, Gemini's and Mistral's alike) adds one page, and the dollars of the scope's
calls since the last page, to the organisation's India day at once, in its own short
transaction; every other call's dollars reach the budget through the ledger when its scope
ends. An operator's accuracy check never counts. A failure logs ``reading_not_counted`` and
never fails the work.

Time: O(1) a call; one statement a page read; one a scope.
"""

import contextvars
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.db import reading_days
from kaagaz.db.engine import Database, unavailable
from kaagaz.db.usage import UsageRow, insert_usage
from kaagaz.ingestion.errors import BackendUnavailable

# #463: an operator's accuracy check. Every call inside its scope is billed as it, Careful's
# too (``UsageScope.add``), and it never counts in an organisation's budget (db.usage, 0064).
ACCURACY_CHECK = "accuracy_check"
# #488: a page read under Ultra low cost (``gemini_ocr``), first read or read again, billed apart
# from scan and upgrade so the operator sees what a ceiling-held page really costs.
ULC_SCAN = "ulc_scan"
# The kinds of work a ledger row is billed as (0023's CHECK allows ^[a-z_]{1,32}$).
KINDS = (
    "scan",
    "upgrade",
    "card",
    "invoice",
    "map",
    "embed",
    "chat",
    "memory",
    "search",
    "story",
    "careful",
    "ensemble_scan",
    "ensemble_chat",
    "land",  # PRD 6 D2 and D3: the land windows, on upload and in the land-facts re-read
    ACCURACY_CHECK,
    ULC_SCAN,
)
# #463: the kinds whose calls read a page and record their thinking level and picture detail.
PAGE_READ_KINDS = frozenset({"scan", "careful", "upgrade", "ensemble_scan", ULC_SCAN})

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Tally:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = 0.0  # None once a call came without a cost
    limit_hits: int = 0  # #463: calls whose reply hit the page read's output limit
    pages: int = 0  # #463 review: page reads among the calls, each counted once


_Key = tuple[str, str, bool, str | None, str | None]  # (model, kind, cached, thinking, resolution)


@dataclass(slots=True)
class UsageScope:
    """One piece of work's model calls, tallied by (model, kind, cached, thinking,
    resolution), with the last provider OpenRouter named for each."""

    customer_id: str
    user_id: str | None
    kind: str
    document_id: str | None = None
    conversation_id: str | None = None
    db: Database | None = field(default=None, repr=False)  # #478: the live counter's (None: none)
    _live_usd: float = field(default=0.0, repr=False)  # #478: dollars since the last page read
    _tallies: dict[_Key, _Tally] = field(default_factory=dict, repr=False)
    _providers: dict[_Key, str | None] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        model: str,
        provider: str | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cost_usd: float | None,
        *,
        cached: bool = False,
        kind: str | None = None,
        thinking: str | None = None,
        resolution: str | None = None,
    ) -> None:
        """Count one call (thread-safe), as ``kind`` (None: the scope's); ``thinking`` and
        ``resolution`` are kept for a page read's kind only (PAGE_READ_KINDS)."""
        key = self._key(model, kind, cached, thinking, resolution)
        with self._lock:
            tally = self._tallies.setdefault(key, _Tally())
            tally.calls += 1
            tally.input_tokens += input_tokens
            tally.output_tokens += output_tokens
            tally.reasoning_tokens += reasoning_tokens
            tally.cost_usd = None if cost_usd is None or tally.cost_usd is None else tally.cost_usd + cost_usd
            if cost_usd is not None and self.kind != ACCURACY_CHECK:
                self._live_usd += cost_usd
            if provider is not None or key not in self._providers:
                self._providers[key] = provider

    def hit_limit(self, model: str, *, kind: str | None, thinking: str, resolution: str) -> None:
        """#463: the call just counted for this page read hit its output limit (thread-safe)."""
        key = self._key(model, kind, False, thinking, resolution)
        with self._lock:
            tally = self._tallies.get(key)
            if tally is not None:  # always: the client counts the call before reading its text
                tally.limit_hits += 1

    def count_page(self, model: str, *, kind: str | None, thinking: str, resolution: str) -> bool:
        """#463 review: one page read on the row of its calls (thread-safe); nothing when no
        call of it was counted (a page with no billed call is never a page of the cost). Whether
        it was counted."""
        key = self._key(model, kind, False, thinking, resolution)
        with self._lock:
            tally = self._tallies.get(key)
            if tally is not None:
                tally.pages += 1
            return tally is not None

    def count_live(self, *, pages: int) -> None:
        """#478: ``pages`` and the scope's dollars since the last page read onto the
        organisation's India day, in a transaction of its own; nothing for an accuracy check or
        a scope with no database."""
        if self.db is None or self.kind == ACCURACY_CHECK:
            return
        with self._lock:
            usd, self._live_usd = self._live_usd, 0.0
        try:
            with unavailable(), self.db.customer(self.customer_id) as conn:
                day = reading_days.india_day(datetime.now(UTC))
                reading_days.count(conn, self.customer_id, day, pages=pages, cost_usd=usd)
        except (SQLAlchemyError, BackendUnavailable):
            _log.warning("reading not counted", extra={"code": "reading_not_counted"})

    def _key(
        self, model: str, kind: str | None, cached: bool, thinking: str | None, resolution: str | None
    ) -> _Key:
        """A call's row: billed as ``kind`` (None: the scope's; an accuracy check's scope bills
        every call as itself), with a page read's settings only (PAGE_READ_KINDS)."""
        billed = self.kind if self.kind == ACCURACY_CHECK else kind or self.kind
        page = billed in PAGE_READ_KINDS
        return (model, billed, cached, thinking if page else None, resolution if page else None)

    def rows(self) -> list[UsageRow]:
        """The ledger rows so far, one per (model, kind, cached, thinking, resolution)."""
        with self._lock:
            return [
                UsageRow(
                    customer_id=self.customer_id,
                    user_id=self.user_id,
                    document_id=self.document_id,
                    conversation_id=self.conversation_id,
                    kind=kind,
                    model=model,
                    provider_name=self._providers[(model, kind, cached, thinking, resolution)],
                    cached=cached,
                    calls=tally.calls,
                    input_tokens=tally.input_tokens,
                    output_tokens=tally.output_tokens,
                    reasoning_tokens=tally.reasoning_tokens,
                    cost_usd=tally.cost_usd,
                    thinking=thinking,
                    resolution=resolution,
                    limit_hits=tally.limit_hits,
                    pages=tally.pages,
                )
                for (model, kind, cached, thinking, resolution), tally in self._tallies.items()
            ]


_CURRENT: contextvars.ContextVar[UsageScope | None] = contextvars.ContextVar("usage_scope", default=None)
_BILLED_AS: contextvars.ContextVar[str | None] = contextvars.ContextVar("billed_as", default=None)


def current_rate_scope() -> tuple[str | None, str | None]:
    """The organisation and user of the model call currently being billed, if any."""
    scope = _CURRENT.get()
    return (None, None) if scope is None else (scope.customer_id, scope.user_id)


@contextmanager
def usage_scope(
    db: Database,
    *,
    customer_id: str,
    user_id: str | None,
    kind: str,
    document_id: str | None = None,
    conversation_id: str | None = None,
) -> Iterator[UsageScope]:
    """Bill the model calls made inside to this work; saved when it ends. ``kind`` is one
    of KINDS (ValueError otherwise)."""
    if kind not in KINDS:
        raise ValueError("unknown usage kind")
    scope = UsageScope(customer_id, user_id, kind, document_id, conversation_id, db=db)
    token, billed = _CURRENT.set(scope), _BILLED_AS.set(None)  # an outer billed_as is not this work's
    try:
        yield scope
    finally:
        _BILLED_AS.reset(billed)
        _CURRENT.reset(token)
        rows = scope.rows()
        if rows:
            try:
                with unavailable(), db.customer(customer_id) as conn:
                    insert_usage(conn, rows)
            except (SQLAlchemyError, BackendUnavailable):
                _log.warning("usage not saved", extra={"code": "usage_not_saved"})


@contextmanager
def billed_as(kind: str) -> Iterator[None]:
    """Bill the model calls made inside as ``kind`` (one of KINDS, ValueError otherwise)
    in the current scope; the scope's own kind again when it ends."""
    if kind not in KINDS:
        raise ValueError("unknown usage kind")
    token = _BILLED_AS.set(kind)
    try:
        yield
    finally:
        _BILLED_AS.reset(token)


def record_limit_hit(model: str, *, thinking: str, resolution: str) -> None:
    """#463: the page read just counted (``record_call``) hit its output limit and is read in
    halves; counted on its ledger row; nothing when no scope is open."""
    scope = _CURRENT.get()
    if scope is not None:
        scope.hit_limit(model, kind=_BILLED_AS.get(), thinking=thinking, resolution=resolution)


def record_unsent_hit(model: str, *, thinking: str, resolution: str) -> None:
    """#488: a page read by the local reader to stay within its ceiling before any call was
    sent (Ultra low cost: no output limit of MIN_CAP_TOKENS affordable, or no known price) is a
    limit hit and a page on its ledger row all the same, so the share of pages Kaagaz's own
    reader read is whole. It goes on as a call of 0 tokens at cost 0: 0023's CHECK keeps no row
    with no call. Nothing when no scope is open."""
    scope = _CURRENT.get()
    if scope is None:
        return
    kind = _BILLED_AS.get()
    scope.add(model, None, 0, 0, 0, 0.0, kind=kind, thinking=thinking, resolution=resolution)
    scope.hit_limit(model, kind=kind, thinking=thinking, resolution=resolution)
    if scope.count_page(model, kind=kind, thinking=thinking, resolution=resolution):
        scope.count_live(pages=1)  # #478: a page of the day, costing nothing


def record_page(model: str, *, thinking: str, resolution: str) -> None:
    """#463 review: one page read (its calls, halves and repairs counted with ``record_call``,
    sent at ``thinking`` and ``resolution``) is one page on their ledger row, so the estimate
    prices a page, not a call; nothing when no scope is open."""
    scope = _CURRENT.get()
    if scope is not None and scope.count_page(
        model, kind=_BILLED_AS.get(), thinking=thinking, resolution=resolution
    ):
        scope.count_live(pages=1)  # #478: the reading pause's counter, at once


def record_call(
    model: str,
    provider: str | None,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cost_usd: float | None,
    *,
    cached: bool = False,
    thinking: str | None = None,
    resolution: str | None = None,
) -> None:
    """Count one billed reply (or, with ``cached``, one page taken from the page cache, at
    cost 0) in the current scope, as ``billed_as``'s kind when one is open; nothing when no
    scope is open. ``thinking`` and ``resolution``: what a page read was sent at (#463)."""
    scope = _CURRENT.get()
    if scope is not None:
        scope.add(
            model,
            provider,
            input_tokens,
            output_tokens,
            reasoning_tokens,
            cost_usd,
            cached=cached,
            kind=_BILLED_AS.get(),
            thinking=thinking,
            resolution=resolution,
        )
