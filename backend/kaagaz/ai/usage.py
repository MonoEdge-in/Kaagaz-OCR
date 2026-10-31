"""Who each model call is billed to (model choice, council 11 Oct 2026, SP1).

``usage_scope`` opens a scope for one piece of work (a document's reading, a chat answer, a
search): the customer, the user who asked (None for work no user asked for), the kind of
work and the document or conversation. The model client calls ``record_call`` for every
reply it is billed for, and the calls add up in the scope that is current: a ContextVar, so
a thread started with ``contextvars.copy_context().run`` adds to the same scope (its tallies
are kept under a lock). A call with no scope open is not recorded (verify-models, tests).

``billed_as(kind)`` bills the calls made inside as another kind of the same work (features
25-27, council 15 Oct 2026, C8): Dynamic's ``careful`` re-reads, Ensemble's
``ensemble_scan`` page reads and ``ensemble_chat`` drafts, so the operator sees them apart
from ``scan`` and ``chat``. It is a ContextVar too, so it reaches threads started with
``copy_context().run`` inside it.

When the scope ends, with or without an error (the calls were billed either way), its
tallies are saved in one INSERT in ``db.customer()``: one row per (model, kind, cached,
thinking, resolution). #463 (Tushar, 31 Oct 2026): a page read (PAGE_READ_KINDS) records the
thinking level and picture detail it was sent at, so the operator's estimate measures a page
at given settings; every other kind records neither.
A failure to save logs ``usage_not_saved`` and never fails the work. A scope opened inside
another is its own scope; the outer one is current again when it ends.

Time: O(1) a call; one statement a scope.
"""

import contextvars
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError

from kaagaz.db.engine import Database, unavailable
from kaagaz.db.usage import UsageRow, insert_usage
from kaagaz.ingestion.errors import BackendUnavailable

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
)
# #463: the kinds whose calls read a page and record their thinking level and picture detail.
PAGE_READ_KINDS = frozenset({"scan", "careful", "upgrade", "ensemble_scan"})

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Tally:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = 0.0  # None once a call came without a cost


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
        kind = kind or self.kind
        page = kind in PAGE_READ_KINDS
        key = (model, kind, cached, thinking if page else None, resolution if page else None)
        with self._lock:
            tally = self._tallies.setdefault(key, _Tally())
            tally.calls += 1
            tally.input_tokens += input_tokens
            tally.output_tokens += output_tokens
            tally.reasoning_tokens += reasoning_tokens
            tally.cost_usd = None if cost_usd is None or tally.cost_usd is None else tally.cost_usd + cost_usd
            if provider is not None or key not in self._providers:
                self._providers[key] = provider

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
    scope = UsageScope(customer_id, user_id, kind, document_id, conversation_id)
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
